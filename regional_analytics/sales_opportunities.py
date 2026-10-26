"""Explainable cross-selling review priorities using reconciled customer IDs."""

import pandas as pd

from regional_analytics.forecasting import PRODUCTS


def rank_opportunities(confirmed: pd.DataFrame) -> pd.DataFrame:
    """Rank missing-product leads by same-country peer adoption, then activity.

    Peer adoption is descriptive evidence, never a conversion probability or
    an estimate of incremental sales. Eligibility and customer needs are unknown.
    """
    columns = ["Customer ID", "Country", "Product to review", "Recent customer units",
               "Active peer customers", "Peers buying product", "Peer adoption (%)",
               "Peer median product units (6 months)"]
    if confirmed.empty:
        return pd.DataFrame(columns=columns)
    cutoff = confirmed["month"].max() - pd.DateOffset(months=5)
    recent = confirmed.loc[confirmed["month"] >= cutoff]
    lifetime = confirmed.groupby(["canonical_customer_id", "product"])["quantity"].sum()
    rows = []
    for country, country_data in confirmed.groupby("country"):
        activity = recent.loc[recent["country"].eq(country)].groupby("canonical_customer_id")["quantity"].sum()
        peer_products = recent.loc[recent["country"].eq(country)].groupby(
            ["canonical_customer_id", "product"]
        )["quantity"].sum()
        for customer in country_data["canonical_customer_id"].unique():
            # Focus sales effort on customers with recent purchasing activity.
            if activity.get(customer, 0) <= 0:
                continue
            peers = len(activity) - 1
            for product in PRODUCTS:
                if lifetime.get((customer, product), 0) > 0:
                    continue
                buyers = peer_products.loc[
                    (peer_products.index.get_level_values("product") == product)
                    & (peer_products.index.get_level_values("canonical_customer_id") != customer)
                ]
                rows.append({"Customer ID": customer, "Country": country,
                             "Product to review": product, "Recent customer units": float(activity[customer]),
                             "Active peer customers": peers, "Peers buying product": len(buyers),
                             "Peer adoption (%)": len(buyers) / peers * 100 if peers else float("nan"),
                             "Peer median product units (6 months)": float(buyers.median()) if len(buyers) else float("nan")})
    return (pd.DataFrame(rows, columns=columns)
            .sort_values(["Peer adoption (%)", "Recent customer units", "Customer ID", "Product to review"],
                         ascending=[False, False, True, True], na_position="last").reset_index(drop=True))
