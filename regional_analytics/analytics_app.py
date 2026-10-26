"""LivesFlow | Regional purchasing intelligence (synthetic-data prototype).

Run by itself: python -m streamlit run analytics_app.py
Integrate into existing MDM app: from ui.regional_analytics import render_analytics
                                 render_analytics()

Expected files beside this module in data/: purchase_orders.csv, customers.csv,
customer_mapping.csv. To override: render_analytics(data_dir=Path(...)).
No database writes, master-data merges, or SAP integration are performed.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

PRODUCTS = ("Product A", "Product B", "Product C")
REQUIRED_ORDERS = {
    "order_id", "customer_id", "country", "product", "order_date", "quantity", "order_status"
}
REQUIRED_CUSTOMERS = {"customer_id", "customer_name", "country"}
REQUIRED_MAPPING = {"original_customer_id", "canonical_customer_id"}


def load_and_validate(data_dir: Path):
    """Load CSVs and refuse inconsistent identity mappings or invalid order data."""
    files = [data_dir / name for name in ("purchase_orders.csv", "customers.csv", "customer_mapping.csv")]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise ValueError("Missing data files: " + ", ".join(missing))
    orders, customers, mapping = [pd.read_csv(f, dtype={"customer_id": "string"}) for f in files]
    for frame, required, label in (
        (orders, REQUIRED_ORDERS, "purchase_orders.csv"),
        (customers, REQUIRED_CUSTOMERS, "customers.csv"),
        (mapping, REQUIRED_MAPPING, "customer_mapping.csv"),
    ):
        absent = required - set(frame.columns)
        if absent:
            raise ValueError(f"{label} lacks columns: {', '.join(sorted(absent))}")
    for frame, columns in (
        (orders, ["customer_id", "order_id", "country", "product", "order_status"]),
        (customers, ["customer_id", "customer_name", "country"]),
        (mapping, ["original_customer_id", "canonical_customer_id"]),
    ):
        for col in columns:
            frame[col] = frame[col].astype("string").str.strip()
            if frame[col].isna().any() or (frame[col] == "").any():
                raise ValueError(f"Missing/blank values in {col}.")

    if customers["customer_id"].duplicated().any():
        raise ValueError("Duplicate customer IDs in customers.csv; each ID must have one master row.")
    if mapping["original_customer_id"].duplicated().any():
        raise ValueError("One alias has multiple mappings; verify customer_mapping.csv.")
    if orders["order_id"].duplicated().any():
        raise ValueError("Duplicate order IDs could inflate demand; check purchase_orders.csv.")
    customer_ids = set(customers["customer_id"])
    if not set(orders["customer_id"]).issubset(customer_ids):
        raise ValueError("Some purchase orders reference missing customer IDs.")
    if not set(mapping["original_customer_id"]).issubset(customer_ids) or not set(mapping["canonical_customer_id"]).issubset(customer_ids):
        raise ValueError("Customer mappings reference IDs absent from customers.csv.")
    if set(mapping["canonical_customer_id"]) & set(mapping["original_customer_id"]):
        raise ValueError("Chained mappings are not supported; map each alias directly to a canonical ID.")

    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
    orders["quantity"] = pd.to_numeric(orders["quantity"], errors="coerce")
    if orders["order_date"].isna().any() or orders["quantity"].isna().any():
        raise ValueError("Invalid order dates or quantities found.")
    if (orders["quantity"] <= 0).any():
        raise ValueError("Order quantities must be positive.")
    if not set(orders["product"]).issubset(PRODUCTS):
        raise ValueError("Unexpected product label; expected Product A, B or C.")
    if not set(orders["country"]).issubset(set(customers["country"])):
        raise ValueError("Order country not found in customer master.")

    # Validate country alignment, including alias-to-canonical mappings.
    country_for = customers.set_index("customer_id")["country"].to_dict()
    if any(country_for[row.customer_id] != row.country for row in orders.itertuples()):
        raise ValueError("Order/customer country mismatch detected.")
    if any(country_for[row.original_customer_id] != country_for[row.canonical_customer_id]
           for row in mapping.itertuples()):
        raise ValueError("Mapping crosses countries; verify customer identities.")

    confirmed = orders.loc[orders["order_status"] == "Confirmed Order"].copy()
    if confirmed.empty:
        raise ValueError("No confirmed orders available for analysis.")
    confirmed = confirmed.merge(
        mapping, left_on="customer_id", right_on="original_customer_id",
        how="left", validate="many_to_one"
    )
    confirmed["canonical_customer_id"] = confirmed["canonical_customer_id"].fillna(confirmed["customer_id"])
    if len(confirmed) != len(orders.loc[orders["order_status"] == "Confirmed Order"]):
        raise ValueError("Reconciliation unexpectedly changed the order-line count.")
    master_names = customers.set_index("customer_id")["customer_name"]
    confirmed["customer_name"] = confirmed["canonical_customer_id"].map(master_names)
    confirmed["month"] = confirmed["order_date"].dt.to_period("M").dt.to_timestamp()
    return orders, customers, mapping, confirmed


def cross_sell_leads(data: pd.DataFrame, customer_col: str = "canonical_customer_id") -> pd.DataFrame:
    """One candidate row per customer, not one row per missing product."""
    totals = data.groupby([customer_col, "product"])["quantity"].sum().unstack(fill_value=0)
    totals = totals.reindex(columns=list(PRODUCTS), fill_value=0)
    leads = []
    for customer_id, row in totals.iterrows():
        owned = [p for p in PRODUCTS if row[p] > 0]
        missing = [p for p in PRODUCTS if row[p] == 0]
        if owned and missing:
            leads.append({"Customer ID": customer_id, "Currently purchases": ", ".join(owned),
                          "Products to investigate": ", ".join(missing),
                          "Confirmed units": int(row.sum())})
    return pd.DataFrame(leads, columns=[
        "Customer ID", "Currently purchases", "Products to investigate", "Confirmed units"
    ]).sort_values("Confirmed units", ascending=False).reset_index(drop=True)


def declining_customers(data: pd.DataFrame, threshold: float = -30.0) -> pd.DataFrame:
    """Compare first versus last six months, including true zero-order months."""
    latest_month = data["month"].max()
    start = latest_month - pd.DateOffset(months=11)
    recent = data.loc[data["month"].between(start, latest_month)].copy()
    if recent.empty:
        return pd.DataFrame(columns=["Customer ID", "First 6 months", "Last 6 months", "Change (%)"])
    split = start + pd.DateOffset(months=6)
    recent["half"] = np.where(recent["month"] < split, "First 6 months", "Last 6 months")
    totals = recent.groupby(["canonical_customer_id", "half"])["quantity"].sum().unstack(fill_value=0)
    for col in ("First 6 months", "Last 6 months"):
        if col not in totals:
            totals[col] = 0
    totals = totals.loc[totals["First 6 months"] > 0].copy()
    totals["Change (%)"] = (totals["Last 6 months"] / totals["First 6 months"] - 1) * 100
    result = totals.loc[totals["Change (%)"] <= threshold].reset_index().rename(
        columns={"canonical_customer_id": "Customer ID"}
    )
    return result.sort_values("Change (%)").reset_index(drop=True)


def forecast_comparison(data: pd.DataFrame, country: str, product: str):
    """Rolling one-step-backtests, last three months, six-month history per model."""
    selection = data.loc[(data["country"] == country) & (data["product"] == product)]
    # Use dataset-wide reporting window, not just months with an order.
    all_months = pd.date_range(data["month"].min(), data["month"].max(), freq="MS")
    series = selection.groupby("month")["quantity"].sum().reindex(all_months, fill_value=0)
    values = series.to_numpy(dtype=float)
    if len(values) < 9:
        return None
    results = []
    for index in range(len(values) - 3, len(values)):
        recent = values[index - 6:index]
        baseline = float(np.mean(recent))
        slope, intercept = np.polyfit(np.arange(6), recent, 1)
        trend = max(0.0, float(slope * 6 + intercept))
        results.append({"Month": series.index[index], "Actual": values[index],
                        "Six-month average": baseline, "Trend-aware": trend})
    test = pd.DataFrame(results)
    baseline_error = float(np.mean(np.abs(test["Actual"] - test["Six-month average"])))
    trend_error = float(np.mean(np.abs(test["Actual"] - test["Trend-aware"])))
    recent = values[-6:]
    slope, intercept = np.polyfit(np.arange(6), recent, 1)
    next_forecasts = {"Six-month average": float(np.mean(recent)),
                      "Trend-aware": max(0.0, float(slope * 6 + intercept))}
    preferred = "Six-month average" if baseline_error <= trend_error else "Trend-aware"
    return test, baseline_error, trend_error, next_forecasts, preferred, all_months[-1] + pd.DateOffset(months=1)


def render_overview(orders, confirmed, selected_countries, selected_products):
    st.subheader("Purchasing performance")
    filtered = confirmed.loc[confirmed["country"].isin(selected_countries)
                             & confirmed["product"].isin(selected_products)].copy()
    if filtered.empty:
        st.info("No confirmed purchases match these filters.")
        return
    a, b, c = st.columns(3)
    a.metric("Confirmed units", f"{filtered['quantity'].sum():,.0f}")
    b.metric("Confirmed order lines", f"{filtered['order_id'].nunique():,}")
    c.metric("Reconciled customers", f"{filtered['canonical_customer_id'].nunique():,}")
    st.caption("All values are simulated. 'Order lines' counts dataset rows, not necessarily distinct purchase orders.")

    monthly = filtered.groupby("month", as_index=False)["quantity"].sum()
    # Fill zero-activity months to prevent growth comparisons silently skipping months.
    index = pd.date_range(confirmed["month"].min(), confirmed["month"].max(), freq="MS")
    monthly = monthly.set_index("month").reindex(index, fill_value=0).rename_axis("month").reset_index()
    fig = px.line(monthly, x="month", y="quantity", markers=True,
                  labels={"month": "Month", "quantity": "Confirmed units"},
                  title="Monthly confirmed purchasing volume")
    fig.update_xaxes(tickformat="%b %Y", dtick="M1")
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    by_country = filtered.groupby("country", as_index=False)["quantity"].sum().sort_values("quantity", ascending=False)
    left.plotly_chart(px.bar(by_country, x="country", y="quantity", title="Units by country",
                             labels={"country": "Country", "quantity": "Units"}), use_container_width=True)
    by_product = filtered.groupby(["country", "product"], as_index=False)["quantity"].sum()
    right.plotly_chart(px.bar(by_product, x="country", y="quantity", color="product", barmode="group",
                              title="Product mix by country", labels={"quantity": "Units", "country": "Country"}),
                       use_container_width=True)

    monthly["Monthly growth (%)"] = monthly["quantity"].pct_change(fill_method=None).mul(100)
    # No meaningful growth percentage when previous month had zero orders.
    monthly.loc[monthly["quantity"].shift(1).eq(0), "Monthly growth (%)"] = np.nan
    monthly.loc[0, "Monthly growth (%)"] = np.nan
    st.markdown("**Monthly growth**")
    latest_growth = monthly["Monthly growth (%)"].iloc[-1]
    if pd.notna(latest_growth):
        st.metric("Latest month-on-month change", f"{latest_growth:+.1f}%")
    else:
        st.caption("A growth percentage is not meaningful when the previous month has zero confirmed purchases.")

    # Actual management takeaways come from the current filtered dataset,
    # never a hard-coded ranking or a claim of realised business growth.
    top_country = by_country.iloc[0]
    product_totals = (filtered.groupby("product")["quantity"]
                      .sum().sort_values(ascending=False))
    top_product = product_totals.index[0]
    top_product_units = int(product_totals.iloc[0])
    selected_scope = "the selected country/product filters"
    st.markdown("### Management decision summary")
    with st.container(border=True):
        one, two = st.columns(2)
        with one:
            st.markdown("**Where is demand concentrated?**")
            st.write(f"**{top_country['country']}** has the highest confirmed volume "
                     f"({int(top_country['quantity']):,} units) within {selected_scope}.")
            st.caption("Action: Review whether supply and account coverage match this volume; assess margin and capacity before allocating resources.")
        with two:
            st.markdown("**Which product needs attention?**")
            st.write(f"**{top_product}** leads with **{top_product_units:,} units** within {selected_scope}.")
            st.caption("Action: Check inventory availability, customer mix, and product profitability before increasing stock.")
        if pd.notna(latest_growth):
            if latest_growth < 0:
                st.warning(f"**Latest month: {latest_growth:+.1f}%**. Investigate order timing, stockouts and customer demand before treating this as lost sales.")
            else:
                st.info(f"**Latest month: {latest_growth:+.1f}%**. Review whether the existing supply plan can accommodate this demand.")
        st.caption("Ranking and actions are recalculated when filters change. Synthetic quantities illustrate a method, not actual Lives Group performance.")
    with st.expander("See monthly calculations and included orders"):
        st.dataframe(monthly.rename(columns={"month": "Month", "quantity": "Units"}), hide_index=True)
        st.dataframe(filtered, hide_index=True)
    with st.expander("See excluded enquiries and pending purchase requests"):
        excluded = orders.loc[orders["order_status"] != "Confirmed Order"]
        st.dataframe(excluded, hide_index=True)


def render_forecasts(confirmed):
    st.subheader("Forecast demand — compare methods fairly")
    st.write("Both methods forecast each test month using **only the six preceding months**. Lower mean absolute error means smaller average mistakes.")
    a, b = st.columns(2)
    country = a.selectbox("Country", sorted(confirmed["country"].unique()), key="analytics_fc_country")
    product = b.selectbox("Product", sorted(confirmed["product"].unique()), key="analytics_fc_product")
    comparison = forecast_comparison(confirmed, country, product)
    if comparison is None:
        st.warning("At least nine consecutive reporting months are required for this comparison.")
        return
    test, baseline_error, trend_error, next_forecasts, preferred, next_month = comparison
    c1, c2 = st.columns(2)
    c1.metric("Six-month average error", f"{baseline_error:,.1f} units")
    c2.metric("Trend-aware error", f"{trend_error:,.1f} units")
    st.caption("Mean absolute error, calculated over the final three historical months. This is a small test sample.")
    long = test.melt(id_vars="Month", var_name="Series", value_name="Units")
    fig = px.line(long, x="Month", y="Units", color="Series", markers=True,
                  title=f"Backtest — {country}, {product}")
    fig.update_xaxes(tickformat="%b %Y", dtick="M1")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(f"**Next-month forecast — {next_month:%B %Y}**")
    x, y = st.columns(2)
    x.metric("Six-month average", f"{next_forecasts['Six-month average']:,.0f} units")
    y.metric("Trend-aware", f"{next_forecasts['Trend-aware']:,.0f} units")
    st.success(f"**Preliminary recommendation:** {preferred}, approximately **{next_forecasts[preferred]:,.0f} units**. It had the lower error in this three-month synthetic-data backtest.")
    st.caption("A forecasting estimate is not a purchase order or an inventory target. Confirm with real demand, lead times, and stock constraints.")
    with st.expander("Show forecast calculations"):
        st.dataframe(test, hide_index=True)


def render_sales(confirmed, customers):
    st.subheader("Sales actions — customer identities already reconciled")
    st.caption("Uses confirmed orders and verified synthetic customer mappings. These are leads to investigate, not guaranteed sales.")
    names = customers.set_index("customer_id")["customer_name"].to_dict()
    countries = customers.set_index("customer_id")["country"].to_dict()
    leads = cross_sell_leads(confirmed)
    if len(leads):
        leads["Customer"] = leads["Customer ID"].map(names)
        leads["Country"] = leads["Customer ID"].map(countries)
        leads["Suggested action"] = "Check product fit; prioritise salesperson follow-up"
        leads = leads[["Customer ID", "Customer", "Country", "Currently purchases",
                       "Products to investigate", "Confirmed units", "Suggested action"]]
    st.metric("Cross-selling candidates", len(leads))
    if leads.empty:
        st.info("No missing-product candidates under these rules.")
    else:
        st.dataframe(leads, hide_index=True, use_container_width=True)
    st.caption("Absence of a confirmed order does not establish that a product is suitable or commercially available to the customer.")

    st.markdown("**Declining customers**")
    threshold = st.slider("Flag declines of at least", 10, 80, 30, 5, format="%d%%", key="analytics_decline_threshold")
    declining = declining_customers(confirmed, threshold=-float(threshold))
    st.metric("Customers flagged for follow-up", len(declining))
    if declining.empty:
        st.info("No customers meet this decline threshold.")
    else:
        declining["Customer"] = declining["Customer ID"].map(names)
        declining["Country"] = declining["Customer ID"].map(countries)
        st.dataframe(declining, hide_index=True, use_container_width=True,
                     column_config={"Change (%)": st.column_config.NumberColumn(format="%.1f%%")})
    st.info("**Suggested action:** Ask account managers to investigate stockouts, seasonality, contract renewals, and possible customer churn before taking action.")


def render_quality(orders, mapping, confirmed):
    st.markdown("### The business story: one customer, fragmented records")
    st.write("A customer can be recorded under several IDs. This splits order history and may lead to incorrect customer counts or sales follow-ups. LivesFlow's MDM workflow flags possible duplicates; **this analytics demonstration separately applies predefined, verified identity mappings** to show how reporting could improve.")
    st.subheader("Why clean data matters")
    original = confirmed["customer_id"].nunique()
    reconciled = confirmed["canonical_customer_id"].nunique()
    total = confirmed["quantity"].sum()
    # An explicit independent check using original raw orders.
    raw_total = orders.loc[orders["order_status"] == "Confirmed Order", "quantity"].sum()
    raw_lines = int((orders["order_status"] == "Confirmed Order").sum())
    valid = total == raw_total and len(confirmed) == raw_lines
    a, b, c = st.columns(3)
    a.metric("Original customer IDs", original)
    b.metric("Verified unique customers", reconciled)
    c.metric("Extra IDs reconciled", original - reconciled)
    if valid:
        st.success(f"**Data integrity passed:** {total:,.0f} units and {raw_lines:,} confirmed order lines preserved.")
    else:
        st.error("Data integrity failed: stop and inspect the mapping before relying on these results.")
    st.caption("This demo uses predefined verified mappings; the MDM intake form flags duplicates but does not merge historical transactions.")

    st.markdown("**Example 1 — Existing history was split across customer IDs**")
    if mapping.empty:
        st.info("No verified alias mappings in the supplied dataset.")
    else:
        target = str(mapping.iloc[0]["canonical_customer_id"])
        raw = confirmed.loc[confirmed["customer_id"] == target].groupby("product")["quantity"].sum()
        clean = confirmed.loc[confirmed["canonical_customer_id"] == target].groupby("product")["quantity"].sum()
        compare = pd.DataFrame({"Product": PRODUCTS, "Before reconciliation": [raw.get(p, 0) for p in PRODUCTS],
                                "After reconciliation": [clean.get(p, 0) for p in PRODUCTS]})
        fig = px.bar(compare.melt("Product", var_name="View", value_name="Units"),
                     x="Product", y="Units", color="View", barmode="group",
                     title=f"Verified customer {target}: purchasing attributed correctly")
        st.plotly_chart(fig, use_container_width=True)
        st.metric("Existing units reattributed", f"{int(clean.sum() - raw.sum()):,}")
        st.caption("These are not additional orders or revenue. Reporting attribution changed; total group-level demand did not.")
        with st.expander("Verified identity mapping and calculation"):
            st.dataframe(mapping, hide_index=True)
            st.dataframe(compare, hide_index=True)

    st.markdown("### Featured case — Prevent an unnecessary sales pitch")
    st.warning("**Separate constructed example — not included in the 12-month dataset.** This example illustrates a possible failure mode, not a measured Lives Group outcome.")
    # Display the source records explicitly, so judges can follow the reasoning.
    illustrative_orders = pd.DataFrame([
        {"Customer ID": "C101", "Recorded name": "ABC Hospital", "Product": "Product A", "Units": 200},
        {"Customer ID": "C102", "Recorded name": "ABC Hospital (alias)", "Product": "Product B", "Units": 150},
    ])
    with st.container(border=True):
        st.markdown("**1. Two customer IDs, one verified hospital**")
        st.dataframe(illustrative_orders, hide_index=True, use_container_width=True)
        left, right = st.columns(2)
        with left:
            st.markdown("**2. Before reconciliation**")
            st.error("C101 appears to have **0 Product B units**. A sales representative might pitch Product B unnecessarily.")
        with right:
            st.markdown("**3. After reconciliation**")
            st.success("The verified combined history includes **150 Product B units**. That Product B pitch is no longer a missing-product lead.")
        example = pd.DataFrame({"Product": ["Product A", "Product B"],
                                "Before (C101 only)": [200, 0],
                                "After (C101 + verified C102)": [200, 150]})
        st.dataframe(example, hide_index=True, use_container_width=True)
        st.info("**Decision:** Check the customer's full purchasing history before targeting a new product. The benefit is a more reliable recommendation — not newly generated revenue.")

    st.markdown("**What happens to cross-selling leads in the main synthetic dataset?**")
    before = cross_sell_leads(confirmed, "customer_id")
    after = cross_sell_leads(confirmed, "canonical_customer_id")
    x, y, z = st.columns(3)
    x.metric("Before reconciliation", len(before))
    y.metric("After reconciliation", len(after))
    z.metric("Change", len(after) - len(before))
    st.caption("If the number stays unchanged, that is the actual result of this synthetic dataset; we do not claim a reduction.")


def render_analytics(data_dir: Path | str | None = None):
    """Reusable Streamlit analytics screen; safe to import into the MDM app."""
    folder = Path(data_dir) if data_dir is not None else Path(__file__).resolve().parent / "data"
    try:
        orders, customers, mapping, confirmed = load_and_validate(folder)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        st.error(f"Analytics data could not be loaded: {exc}")
        st.info("Put purchase_orders.csv, customers.csv and customer_mapping.csv inside the analytics data folder.")
        return

    # Clear header and accessible navigation, with descriptive text in each card.
    st.title("LivesFlow | Regional Sales Intelligence")
    st.caption("**SYNTHETIC DEMONSTRATION — NOT ACTUAL LIVES GROUP RESULTS.** Countries and products are illustrative assumptions.")
    st.markdown("**Cleaner customer records → clearer purchasing insights → better-informed decisions.**")

    sections = [
        ("overview", "📊 Overview", "Regional purchasing performance"),
        ("forecast", "📈 Forecasting", "Demand and model accuracy"),
        ("sales", "🎯 Sales Insights", "Cross-selling and customer follow-up"),
        ("quality", "🛡️ Data Quality", "Verified identities and business value"),
    ]
    if st.session_state.get("analytics_active_page") not in {item[0] for item in sections}:
        st.session_state["analytics_active_page"] = "overview"

    nav = st.columns(4, gap="small")
    for column, (page_id, label, subtitle) in zip(nav, sections):
        with column:
            active = st.session_state["analytics_active_page"] == page_id
            # A bordered card gives every nav item its own readable, clickable area.
            with st.container(border=True):
                st.markdown(f"**{subtitle}**")
                if st.button(
                    label,
                    key=f"analytics_nav_{page_id}",
                    use_container_width=True,
                    type="primary" if active else "secondary",
                ):
                    st.session_state["analytics_active_page"] = page_id
                    st.rerun()
    st.divider()

    selected_page = st.session_state["analytics_active_page"]
    if selected_page == "overview":
        st.header("Regional Overview")
        st.write("Explore confirmed purchasing demand across five illustrative countries and three products.")
        countries = sorted(confirmed["country"].unique())
        products = sorted(confirmed["product"].unique())
        x, y = st.columns(2)
        selected_countries = x.multiselect(
            "Countries", countries, default=countries, key="analytics_filter_countries"
        )
        selected_products = y.multiselect(
            "Products", products, default=products, key="analytics_filter_products"
        )
        render_overview(orders, confirmed, selected_countries, selected_products)
    elif selected_page == "forecast":
        st.header("Demand Forecasting")
        st.write("Test two practical forecasting approaches before making a planning recommendation.")
        render_forecasts(confirmed)
    elif selected_page == "sales":
        st.header("Sales Insights")
        st.write("Prioritise follow-ups using reconciled customer identities, not duplicate records.")
        render_sales(confirmed, customers)
    else:
        st.header("Data Quality & Business Value")
        st.write("See how verified customer identities improve reporting without inventing extra sales.")
        render_quality(orders, mapping, confirmed)

    st.divider()
    st.caption(
        "**Decision guidance:** Validate demo patterns with real records before operational use. "
        "Product fit, customer needs, regulatory obligations and ERP controls require human review."
    )


if __name__ == "__main__":
    st.set_page_config(page_title="LivesFlow Regional Analytics", page_icon="📊", layout="wide")
    render_analytics()
