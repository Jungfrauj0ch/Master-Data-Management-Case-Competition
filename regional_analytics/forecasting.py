"""Monthly PO forecasting with fixed-origin, horizon-matched validation.

Original implementation inspired by panyashree12/Sales-Forecasting's workflow.
Only confirmed-order units are used; these are a proxy for observed demand.
"""

import warnings
from itertools import product as combinations

import numpy as np
import pandas as pd

BASELINE = "Six-month average"
HOLT = "Damped Holt"
ARIMA = "ARIMA(1,1,1)"
TREND = "Robust damped trend"
MODELS = (BASELINE, TREND, HOLT, ARIMA)
PRODUCTS = ("Product A", "Product B", "Product C")
MIN_HISTORY = 6
MIN_SELECTION_ORIGINS = 3
TREND_DAMPING = 0.8
MAX_MONTHLY_TREND_RATE = 0.15


def trend_components(values) -> tuple[float, float]:
    """Estimate recent level and slope without letting one unusual PO dominate.

    Use the median pairwise slope over six months, shrink it as volatility
    increases, and cap the initial monthly movement at 15% of recent level.
    These are explicit planning assumptions, not parameters tuned on test data.
    """
    recent = np.asarray(values, dtype=float)[-6:]
    x = np.arange(len(recent))
    slopes = [(recent[j] - recent[i]) / (j - i)
              for i in range(len(recent)) for j in range(i + 1, len(recent))]
    slope = float(np.median(slopes))
    intercept = float(np.median(recent - slope * x))
    level = max(0.0, (intercept + slope * x[-1] + np.median(recent[-3:])) / 2)
    cv = float(recent.std() / recent.mean()) if recent.mean() else 0.0
    limit = MAX_MONTHLY_TREND_RATE * level
    return level, float(np.clip(slope / (1 + cv), -limit, limit))


def monthly_panel(orders: pd.DataFrame) -> pd.DataFrame:
    """Keep the common reporting calendar, including zero-order combinations.

    Input must cover complete reporting months. A missing order means zero only
    under that assumption; source outages must be resolved before calling this.
    """
    confirmed = orders.loc[orders["order_status"].eq("Confirmed Order")].copy()
    if confirmed.empty:
        raise ValueError("No confirmed orders available for forecasting.")
    dates = pd.to_datetime(orders["order_date"], errors="raise")
    start, end = dates.min().to_period("M"), dates.max().to_period("M")
    months = pd.period_range(start, end, freq="M").to_timestamp()
    confirmed["Month"] = pd.to_datetime(confirmed["order_date"]).dt.to_period("M").dt.to_timestamp()
    quantities = pd.to_numeric(confirmed["quantity"], errors="raise")
    if not np.isfinite(quantities).all() or (quantities <= 0).any():
        raise ValueError("Confirmed quantities must be finite and positive.")
    confirmed["quantity"] = quantities
    index = pd.MultiIndex.from_product(
        [sorted(orders["country"].unique()), PRODUCTS, months],
        names=["Country", "Product", "Month"],
    )
    return (confirmed.groupby(["country", "product", "Month"])["quantity"].sum()
            .rename_axis(["Country", "Product", "Month"]).reindex(index, fill_value=0)
            .rename("Units").reset_index())


def predict(values, horizon: int, model: str) -> np.ndarray:
    """Fit on the supplied past only; reject failed or nonfinite model fits."""
    values = np.asarray(values, dtype=float)
    if horizon < 1 or len(values) < MIN_HISTORY:
        raise ValueError("Forecasting requires six reporting months and a positive horizon.")
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("History must contain finite, nonnegative units.")
    if model == BASELINE:
        return np.repeat(values[-6:].mean(), horizon)
    if model == TREND:
        level, slope = trend_components(values)
        damped_steps = np.cumsum(TREND_DAMPING ** np.arange(1, horizon + 1))
        return np.maximum(0.0, level + slope * damped_steps)
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")
    # Avoid fitting unidentified trend/ARIMA parameters to constant histories.
    if np.ptp(values) == 0:
        return np.repeat(values[-1], horizon)
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    from statsmodels.tsa.arima.model import ARIMA as ArimaModel
    from statsmodels.tsa.holtwinters import Holt

    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        # statsmodels automatically replaces these initial guesses with zeros;
        # the final convergence check still determines candidate eligibility.
        warnings.filterwarnings("ignore", message="Non-stationary starting autoregressive parameters.*", category=UserWarning)
        warnings.filterwarnings("ignore", message="Non-invertible starting MA parameters.*", category=UserWarning)
        if model == HOLT:
            fitted = Holt(values, damped_trend=True, initialization_method="estimated").fit()
        else:
            fitted = ArimaModel(values, order=(1, 1, 1)).fit()
        if not fitted.mle_retvals.get("success", fitted.mle_retvals.get("converged", True)):
            raise ValueError("Model optimizer did not converge.")
        forecast = np.asarray(fitted.forecast(horizon), dtype=float)
    if not np.isfinite(forecast).all():
        raise ValueError("Model returned nonfinite forecasts.")
    return np.maximum(0.0, forecast)


def evaluate_series(series: pd.Series, horizon: int) -> dict:
    """Compare each model on the same last six eligible forecast origins.

    Every origin predicts the *entire* requested horizon without observing any
    of its actuals. Overlapping test windows are explicitly counted as such.
    The validation sample selects a provisional model, not an unbiased uplift.
    """
    if horizon not in (1, 3, 6, 12):
        raise ValueError("Supported forecast horizons: 1, 3, 6 or 12 months.")
    values = series.to_numpy(dtype=float)
    if len(values) < MIN_HISTORY:
        raise ValueError("At least six complete reporting months are required.")
    origins = list(range(max(MIN_HISTORY, len(values) - horizon - 5), len(values) - horizon + 1))
    records, scores, future = [], [], {}
    for model in MODELS:
        rows = []
        status = "Available"
        try:
            for origin in origins:
                predicted = predict(values[:origin], horizon, model)
                rows.extend({
                    "Origin": series.index[origin - 1], "Month": series.index[origin + step],
                    "Lead month": step + 1, "Model": model,
                    "Actual": values[origin + step], "Predicted": float(predicted[step]),
                } for step in range(horizon))
            future[model] = predict(values, horizon, model)
        except (ValueError, RuntimeError, np.linalg.LinAlgError, Warning) as exc:
            # An unsuccessful candidate cannot compete using easier folds only.
            status = f"Unavailable: {type(exc).__name__}: {exc}"
            rows = []
        records.extend(rows)
        if rows:
            actual = np.array([row["Actual"] for row in rows])
            error = np.array([row["Predicted"] for row in rows]) - actual
            mae = float(np.abs(error).mean())
            rmse = float(np.sqrt(np.square(error).mean()))
            wape = float(np.abs(error).sum() / actual.sum() * 100) if actual.sum() else np.nan
            bias = float(error.mean())
        else:
            mae = rmse = wape = bias = np.nan
        scores.append({"Model": model, "MAE (units)": mae, "RMSE (units)": rmse,
                       "WAPE (%)": wape, "Bias (units)": bias,
                       "Test origins": len(origins) if rows else 0,
                       "Test predictions": len(rows), "Status": status})
    metrics = pd.DataFrame(scores)
    if len(origins) >= MIN_SELECTION_ORIGINS:
        available = metrics.loc[metrics["MAE (units)"].notna()]
        # Stable ordering favors the baseline on equal errors.
        selected = available.sort_values("MAE (units)", kind="stable").iloc[0]["Model"]
        validation = "Exploratory model selection"
    else:
        selected = BASELINE
        validation = ("No complete test horizon" if not origins
                      else f"Only {len(origins)} test origin(s); baseline retained")
    return {"metrics": metrics, "backtest": pd.DataFrame(records), "future": future,
            "selected": selected, "validation": validation, "origins": len(origins)}


def regional_forecast(orders: pd.DataFrame, horizon: int = 6, approach: str = "trend") -> dict:
    """Produce a chosen planning outlook and additive regional totals.

    The default trend outlook is explicitly a planning projection. Users can
    separately choose the historically selected method, which may be flat.
    Neither view asserts improved accuracy without an independent evaluation.
    """
    if approach not in ("trend", "backtest"):
        raise ValueError("Forecast approach must be 'trend' or 'backtest'.")
    panel = monthly_panel(orders)
    summary, forecasts, metrics, tests = [], [], [], []
    for country, product in combinations(sorted(panel["Country"].unique()), PRODUCTS):
        history = panel.loc[panel["Country"].eq(country) & panel["Product"].eq(product)]
        series = history.set_index("Month")["Units"]
        result = evaluate_series(series, horizon)
        selected = TREND if approach == "trend" and TREND in result["future"] else result["selected"]
        model_forecast = result["future"][selected]
        baseline = result["future"][BASELINE]
        validation = result["validation"]
        recent = series.to_numpy(dtype=float)[-6:]
        recent_average = float(recent.mean())
        forecast_average = float(model_forecast.mean())
        change = (forecast_average / recent_average - 1) * 100 if recent_average else np.nan
        variability = float(recent.std() / recent_average * 100) if recent_average else np.nan
        # A deterministic stress scenario, not a probability/confidence interval.
        scenario_width = float(recent.std()) * np.sqrt(np.arange(1, horizon + 1))
        low = np.maximum(0.0, model_forecast - scenario_width)
        high = model_forecast + scenario_width
        if not series.sum():
            validation = "No confirmed orders; verify demand and source coverage"
        summary.append({"Country": country, "Product": product, "Selected model": selected,
                        "History months": len(series), "Test origins": result["origins"],
                        "Forecast units": float(model_forecast.sum()),
                        "Baseline units": float(baseline.sum()),
                        "Monthly forecast": forecast_average,
                        "Recent monthly average": recent_average,
                        "Change vs recent average (%)": change,
                        "Recent variability (%)": variability,
                        "Backtest best model": result["selected"] if result["origins"] >= MIN_SELECTION_ORIGINS else "Not established",
                        "Forecast view": "Trend outlook" if approach == "trend" else "Best historical fit",
                        "Validation": validation})
        months = pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
        forecasts.extend({"Country": country, "Product": product, "Month": month,
                          "Lead month": step + 1, "Model": selected,
                          "Forecast units": float(model_forecast[step]),
                          "Baseline units": float(baseline[step]), "Validation": validation,
                          "Lower scenario": float(low[step]), "Upper scenario": float(high[step]),
                          "Forecast view": "Trend outlook" if approach == "trend" else "Best historical fit",
                          "Source": "Synthetic confirmed-order demonstration"}
                         for step, month in enumerate(months))
        metrics.append(result["metrics"].assign(Country=country, Product=product))
        if not result["backtest"].empty:
            tests.append(result["backtest"].assign(Country=country, Product=product))
    forecast = pd.DataFrame(forecasts)
    return {"history": panel, "summary": pd.DataFrame(summary), "forecast": forecast,
            "metrics": pd.concat(metrics, ignore_index=True),
            "backtest": pd.concat(tests, ignore_index=True) if tests else pd.DataFrame(),
            "regional": forecast.groupby("Month", as_index=False)[["Forecast units", "Baseline units"]].sum()}
