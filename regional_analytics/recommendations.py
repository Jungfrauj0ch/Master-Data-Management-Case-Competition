"""Explainable decision prompts from forecasts and reconciled order history."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from regional_analytics.sales_opportunities import rank_opportunities

CHANGE_THRESHOLD = 10.0
VARIABILITY_THRESHOLD = 25.0


@dataclass(frozen=True)
class Recommendation:
    title: str
    evidence: str
    action: str
    owner: str
    priority: str


def strategic_recommendations(history: pd.Series, forecast: pd.DataFrame,
                              country: str, product: str,
                              confirmed: pd.DataFrame | None = None) -> list[Recommendation]:
    """Return supply, review-cadence and sales actions with their actual evidence.

    Thresholds are transparent review rules. Stockouts, revenue uplift and
    reorder quantities cannot be inferred from confirmed-order units alone.
    """
    recent = history.to_numpy(dtype=float)[-6:]
    average = float(recent.mean())
    projected = float(forecast["Forecast units"].mean())
    change = (projected / average - 1) * 100 if average else np.nan
    cv = float(recent.std() / average * 100) if average else np.nan
    peak = forecast.loc[forecast["Forecast units"].idxmax()]
    peak_label = f"{peak['Month']:%b %Y}"
    if not average:
        supply = Recommendation(
            "Check for missing orders", "No confirmed orders were recorded in the last six months.",
            "Check that all order records have been added before planning new stock.",
            "Data team + supply planner", "Review first")
    elif change > CHANGE_THRESHOLD:
        supply = Recommendation(
            f"Check stock for {peak_label}",
            f"Expected monthly orders are {change:.1f}% above the last six-month average.",
            "Check usable stock and incoming deliveries, then confirm suppliers can cover any gap.",
            "Supply planner", "Review first")
    elif change < -CHANGE_THRESHOLD:
        supply = Recommendation(
            "Review new stock orders",
            f"Expected monthly orders are {abs(change):.1f}% below the last six-month average.",
            "Check stock on hand and expiry dates before committing to more stock.",
            "Supply planner", "Review first")
    else:
        supply = Recommendation(
            "Keep your regular stock review",
            f"Expected monthly orders are within {CHANGE_THRESHOLD:.0f}% of the last six-month average.",
            "Check stock and incoming deliveries at the next monthly planning meeting.",
            "Supply planner", "Routine review")

    if not average:
        risk = Recommendation(
            "Confirm customer activity", "There are no recent orders to assess demand.",
            "Ask account managers whether customers still need this product.",
            "Account manager", "Review first")
    elif cv >= VARIABILITY_THRESHOLD:
        risk = Recommendation(
            "Check demand every week",
            "Order volumes have changed substantially from month to month.",
            "Check for large new orders, cancellations and delivery changes each week.",
            "Demand planner", "Monitor closely")
    else:
        risk = Recommendation(
            "Refresh the forecast every month",
            "Monthly order volumes have been relatively steady.",
            "Add newly confirmed orders and check for upcoming tenders each month.",
            "Demand planner", "Routine review")

    sales = Recommendation(
        "Confirm customers’ next orders",
        "The forecast is based on past purchase orders.",
        f"Ask key customers in {country} how much {product} they plan to order and when.",
        "Account manager", "Account review")
    if confirmed is not None and not confirmed.empty:
        opportunities = rank_opportunities(confirmed)
        leads = opportunities.loc[
            opportunities["Country"].eq(country)
            & opportunities["Product to review"].eq(product)
            & opportunities["Peers buying product"].gt(0)
        ]
        # Retention is assessed at the selected country/product, not all products.
        selected = confirmed.loc[confirmed["country"].eq(country) & confirmed["product"].eq(product)].copy()
        latest = confirmed["month"].max()
        start = latest - pd.DateOffset(months=11)
        selected = selected.loc[selected["month"].between(start, latest)]
        first = selected.loc[selected["month"] < start + pd.DateOffset(months=6)].groupby("canonical_customer_id")["quantity"].sum()
        last = selected.loc[selected["month"] >= start + pd.DateOffset(months=6)].groupby("canonical_customer_id")["quantity"].sum()
        lost = int(((last.reindex(first.index, fill_value=0) / first - 1) <= -0.30).sum())
        if not leads.empty:
            count = leads["Customer ID"].nunique()
            sales = Recommendation(
                f"Check {count} potential buyer{'s' if count != 1 else ''}",
                f"They have no recorded {product} orders, while other customers in {country} buy it.",
                "Open Sales Insights, check product fit and eligibility, then contact suitable customers.",
                "Account manager", "Growth opportunity")
        elif lost and confirmed["month"].min() <= start:
            sales = Recommendation(
                f"Call {lost} customer{'s' if lost != 1 else ''} buying less",
                f"Their {product} orders fell at least 30% versus the previous six months.",
                "Ask what changed and whether delivery problems or changing needs explain the drop.",
                "Account manager", "Retention review")
    return [supply, risk, sales]


def regional_priorities(summary: pd.DataFrame) -> pd.DataFrame:
    """Rank review prompts by demand movement and variability, not inferred ROI."""
    table = summary.copy()
    change = table["Change vs recent average (%)"]
    table["Suggested focus"] = np.select(
        [table["Recent monthly average"].eq(0), change.gt(CHANGE_THRESHOLD),
         change.lt(-CHANGE_THRESHOLD), table["Recent variability (%)"].ge(VARIABILITY_THRESHOLD)],
        ["Verify demand coverage", "Review supply capacity", "Review replenishment pace", "Review demand swings"],
        default="Maintain review cadence",
    )
    table["_review"] = table["Suggested focus"].ne("Maintain review cadence")
    table["_movement"] = change.abs().fillna(0)
    return (table.sort_values(["_review", "_movement", "Forecast units"], ascending=[False, False, False])
            [["Country", "Product", "Forecast units", "Change vs recent average (%)",
              "Recent variability (%)", "Suggested focus"]].reset_index(drop=True))
