"""Check chronology, horizon coverage and honest model selection safeguards."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from regional_analytics import forecasting as fc
from regional_analytics.sales_opportunities import rank_opportunities
from regional_analytics.recommendations import regional_priorities, strategic_recommendations

ROOT = Path(__file__).resolve().parents[1]


def series(months=12):
    return pd.Series(np.arange(1, months + 1, dtype=float),
                     index=pd.date_range("2025-01-01", periods=months, freq="MS"))


def test_panel_keeps_calendar_and_zero_combinations_but_excludes_pipeline():
    orders = pd.DataFrame([
        {"country": "Singapore", "product": "Product A", "order_date": "2025-01-01",
         "quantity": 10, "order_status": "Confirmed Order"},
        {"country": "Singapore", "product": "Product B", "order_date": "2025-03-01",
         "quantity": 9999, "order_status": "Enquiry"},
    ])
    panel = fc.monthly_panel(orders)
    assert len(panel) == 9
    assert panel["Units"].sum() == 10
    assert panel["Month"].max() == pd.Timestamp("2025-03-01")
    assert panel.loc[panel["Product"].eq("Product C"), "Units"].sum() == 0


def test_validation_predicts_entire_horizon_from_each_past_prefix(monkeypatch):
    calls = []

    def spy(values, horizon, model):
        calls.append((np.asarray(values).copy(), horizon, model))
        return np.repeat(np.mean(values[-6:]), horizon)

    monkeypatch.setattr(fc, "predict", spy)
    history = series(18)
    result = fc.evaluate_series(history, 6)
    assert result["origins"] == 6
    assert result["selected"] == fc.BASELINE  # ties favor the simple method
    for values, horizon, model in calls:
        np.testing.assert_array_equal(values, history.to_numpy()[:len(values)])
        assert horizon == 6
    test = result["backtest"]
    for (model, origin), block in test.groupby(["Model", "Origin"]):
        assert len(block) == 6
        assert block["Month"].min() == origin + pd.offsets.MonthBegin(1)
        assert block["Lead month"].tolist() == list(range(1, 7))
        expected = history.loc[:origin].iloc[-6:].mean()
        np.testing.assert_allclose(block["Predicted"], expected)


@pytest.mark.parametrize("horizon,origins", [(1, 6), (3, 4), (6, 1), (12, 0)])
def test_short_history_never_claims_long_horizon_validation(monkeypatch, horizon, origins):
    monkeypatch.setattr(fc, "predict", lambda values, horizon, model: np.repeat(np.mean(values[-6:]), horizon))
    result = fc.evaluate_series(series(), horizon)
    assert result["origins"] == origins
    assert result["selected"] == fc.BASELINE
    assert len(result["future"][fc.BASELINE]) == horizon
    if not origins:
        assert result["metrics"]["MAE (units)"].isna().all()
        assert result["backtest"].empty


def test_failed_candidate_cannot_win_on_only_successful_folds(monkeypatch):
    def candidate(values, horizon, model):
        if model == fc.HOLT and len(values) == 7:
            raise ValueError("deliberate failed fit")
        return np.repeat(np.mean(values[-6:]), horizon)

    monkeypatch.setattr(fc, "predict", candidate)
    result = fc.evaluate_series(series(), 1)
    failed = result["metrics"].set_index("Model").loc[fc.HOLT]
    assert failed["Status"].startswith("Unavailable")
    assert pd.isna(failed["MAE (units)"])
    assert fc.HOLT not in result["backtest"]["Model"].unique()
    assert result["selected"] == fc.BASELINE


def test_zero_demand_is_finite_and_percentage_error_is_undefined():
    history = series() * 0
    result = fc.evaluate_series(history, 3)
    assert result["selected"] == fc.BASELINE
    assert result["metrics"]["WAPE (%)"].isna().all()
    assert result["metrics"]["MAE (units)"].eq(0).all()
    assert all(np.array_equal(value, np.zeros(3)) for value in result["future"].values())


def test_trend_outlook_responds_to_momentum_and_eases_with_horizon():
    increasing = np.arange(100, 220, 10, dtype=float)
    forecast = fc.predict(increasing, 12, fc.TREND)
    assert np.diff(forecast).min() > 0
    assert np.diff(np.diff(forecast)).max() < 0
    assert forecast[-1] > forecast[0]
    np.testing.assert_array_equal(forecast, fc.predict(increasing, 12, fc.TREND))
    falling = fc.predict(increasing[::-1], 12, fc.TREND)
    assert np.diff(falling).max() < 0
    assert np.isfinite(falling).all() and (falling >= 0).all()


def test_single_unusual_order_does_not_dominate_trend_level():
    history = np.array([100, 110, 120, 130, 140, 1000000], dtype=float)
    forecast = fc.predict(history, 6, fc.TREND)
    assert forecast.max() < 250
    stable = fc.predict(np.repeat(100.0, 12), 6, fc.TREND)
    np.testing.assert_array_equal(stable, np.repeat(100.0, 6))


def test_supply_actions_have_evidence_and_do_not_infer_stockouts():
    history = series()
    dates = pd.date_range("2026-01-01", periods=6, freq="MS")
    high = pd.DataFrame({"Month": dates, "Forecast units": np.arange(20, 26)})
    recommendations = strategic_recommendations(history, high, "Singapore", "Product A")
    assert len(recommendations) == 3
    assert recommendations[0].title == "Check stock for Jun 2026"
    assert "six-month average" in recommendations[0].evidence
    assert "usable stock" in recommendations[0].action
    assert all(item.owner and item.evidence and item.action for item in recommendations)
    low = high.assign(**{"Forecast units": 1.0})
    assert strategic_recommendations(history, low, "Singapore", "Product A")[0].title == "Review new stock orders"
    assert strategic_recommendations(history * 0, low, "Singapore", "Product A")[0].title == "Check for missing orders"


def test_regional_forecasts_reconcile_and_preserve_fifteen_combinations(monkeypatch):
    monkeypatch.setattr(fc, "predict", lambda values, horizon, model: np.repeat(np.mean(values[-6:]), horizon))
    orders = pd.read_csv(ROOT / "regional_analytics/data/purchase_orders.csv")
    result = fc.regional_forecast(orders, 12)
    assert len(result["summary"]) == 15
    assert len(result["forecast"]) == 180
    assert result["summary"]["Test origins"].eq(0).all()
    assert result["forecast"]["Month"].min() == pd.Timestamp("2026-01-01")
    assert result["forecast"]["Month"].max() == pd.Timestamp("2026-12-01")
    assert result["history"]["Units"].sum() == orders.loc[orders["order_status"].eq("Confirmed Order"), "quantity"].sum()
    assert result["regional"]["Forecast units"].sum() == pytest.approx(result["summary"]["Forecast units"].sum())
    assert result["summary"]["Selected model"].eq(fc.TREND).all()
    assert (result["forecast"]["Lower scenario"] <= result["forecast"]["Forecast units"]).all()
    assert (result["forecast"]["Upper scenario"] >= result["forecast"]["Forecast units"]).all()
    assert len(regional_priorities(result["summary"])) == 15


@pytest.mark.parametrize("horizon", [1, 3, 6, 12])
def test_trend_outlook_skips_statistical_fits_and_preserves_projections(monkeypatch, horizon):
    original_predict = fc.predict
    calls = set()

    def lightweight_only(values, horizon, model):
        assert model in (fc.BASELINE, fc.TREND), "Trend view must not fit Holt or ARIMA"
        calls.add(model)
        return original_predict(values, horizon, model)

    monkeypatch.setattr(fc, "predict", lightweight_only)
    orders = pd.read_csv(ROOT / "regional_analytics/data/purchase_orders.csv")
    result = fc.regional_forecast(orders, horizon, "trend")
    assert calls == {fc.BASELINE, fc.TREND}
    assert set(result["metrics"]["Model"]) == calls
    assert result["summary"]["Backtest best model"].eq("Not evaluated in trend outlook").all()
    for (country, product), future in result["forecast"].groupby(["Country", "Product"]):
        history = result["history"].loc[
            result["history"]["Country"].eq(country) & result["history"]["Product"].eq(product), "Units"
        ]
        np.testing.assert_allclose(future["Forecast units"], original_predict(history, horizon, fc.TREND))
        np.testing.assert_allclose(future["Baseline units"], original_predict(history, horizon, fc.BASELINE))


def test_best_historical_fit_still_compares_all_models(monkeypatch):
    calls = set()

    def candidate(values, horizon, model):
        calls.add(model)
        return np.repeat(np.mean(values[-6:]), horizon)

    monkeypatch.setattr(fc, "predict", candidate)
    orders = pd.read_csv(ROOT / "regional_analytics/data/purchase_orders.csv")
    result = fc.regional_forecast(orders, 1, "backtest")
    assert calls == set(fc.MODELS)
    assert set(result["metrics"]["Model"]) == set(fc.MODELS)
    assert set(result["backtest"]["Model"]) == set(fc.MODELS)
    assert result["summary"]["Selected model"].eq(fc.BASELINE).all()
    assert result["summary"]["Backtest best model"].eq(fc.BASELINE).all()


def test_sales_priorities_respect_identity_and_country_boundaries():
    data = pd.DataFrame([
        {"canonical_customer_id": "C1", "country": "Singapore", "product": "Product A", "quantity": 20, "month": pd.Timestamp("2025-12-01")},
        {"canonical_customer_id": "C1", "country": "Singapore", "product": "Product B", "quantity": 10, "month": pd.Timestamp("2025-12-01")},
        {"canonical_customer_id": "C2", "country": "Singapore", "product": "Product C", "quantity": 8, "month": pd.Timestamp("2025-12-01")},
        {"canonical_customer_id": "C3", "country": "Malaysia", "product": "Product C", "quantity": 9999, "month": pd.Timestamp("2025-12-01")},
    ])
    result = rank_opportunities(data)
    c1 = result.loc[result["Customer ID"].eq("C1")]
    assert c1["Product to review"].tolist() == ["Product C"]
    assert c1.iloc[0]["Peer median product units (6 months)"] == 8
    assert c1.iloc[0]["Active peer customers"] == 1
    assert c1.iloc[0]["Peer adoption (%)"] == 100
    assert result.loc[result["Customer ID"].eq("C3"), "Peer adoption (%)"].isna().all()


def test_forecast_and_sales_screens_render_real_models_and_horizons():
    app = AppTest.from_file(str(ROOT / "regional_analytics/analytics_app.py"), default_timeout=90).run()
    assert not app.exception
    next(button for button in app.button if "Forecasting" in button.label).click().run()
    assert not app.exception
    assert any(metric.label == "Expected orders over this period" for metric in app.metric)
    assert any(heading.value == "### Strategic recommendations" for heading in app.markdown)
    assert not any("How this outlook was built" in heading.value for heading in app.markdown)
    regional = next(table.value for table in app.dataframe if "Suggested focus" in table.value.columns)
    assert len(regional) == 15
    month_table = next(table.value for table in app.dataframe if "Expected units" in table.value.columns)
    assert month_table["Expected units"].nunique() > 1
    next(widget for widget in app.selectbox if widget.label == "Months ahead").set_value(12).run()
    assert not app.exception
    assert not app.warning
    month_table = next(table.value for table in app.dataframe if "Expected units" in table.value.columns)
    assert len(month_table) == 12
    assert month_table["Expected units"].nunique() > 1
    next(widget for widget in app.selectbox if widget.label == "Country").set_value("Singapore").run()
    next(widget for widget in app.selectbox if widget.label == "Product").set_value("Product B").run()
    assert not app.exception
    assert any("Singapore · Product B" in caption.value for caption in app.caption)
    next(widget for widget in app.selectbox if widget.label == "Forecast view").set_value("Best historical fit").run()
    assert not app.exception
    month_table = next(table.value for table in app.dataframe if "Expected units" in table.value.columns)
    assert month_table["Expected units"].nunique() == 1
    next(button for button in app.button if button.label == "Open Sales Insights").click().run()
    assert not app.exception
    assert any("Peer adoption (%)" in table.value.columns for table in app.dataframe)
    next(button for button in app.button if "Data Quality" in button.label).click().run()
    assert not app.exception
    assert not any("business story" in heading.value for heading in app.markdown)
    assert any("Illustrative example · Separate from the main dataset." == caption.value for caption in app.caption)
