# Improving purchase-order forecasting and finding sales growth

**Proposed case answer:** Lives Group could improve its traditional 6-to-12-month purchase-order planning by maintaining a rolling, regularly refreshed forecast for each of the 15 combinations of Products A, B and C across Singapore, Malaysia, Indonesia, Thailand and Vietnam. Compare statistical and, when sufficient data is available, machine-learning forecasts against the existing planning process. Use the results to direct planners toward exceptions and account managers toward relevant customer opportunities. Master data management supplies the consistent customer and product identities needed for both activities.

The countries and all quantities in this prototype are illustrative. The supplied dataset covers January–December 2025. It does not establish actual Lives Group demand patterns, productivity savings, or incremental revenue.

## How the linked project informs the solution

The reference [Sales-Forecasting project](https://github.com/panyashree12/Sales-Forecasting) describes a workflow covering transaction preparation, time-based evaluation, statistical forecasting and a Streamlit dashboard. Its README compares ARIMA and Holt-Winters on retail revenue and reports a 30-day forecast. That workflow is a useful starting point; its reported retail accuracy, promotional findings and seasonality are not evidence about these products or countries.

This implementation uses original code and the local synthetic purchasing data. It does not import the reference dataset, copy its source code, or load its serialized model files. It adapts the forecasting workflow to monthly product–country unit volumes and validates at the requested planning horizon.

## 1. Build one trustworthy demand history

Standardize customer IDs, product codes, countries, units of measure, dates and order statuses. Link aliases only after identity verification. Reconciliation should preserve transaction counts and quantities: it improves attribution and customer coverage rather than creating extra sales.

Use confirmed order quantities as the initial observable target. Keep enquiries and pending requests separate. In production, capture cancellations, returns, stockouts, requested delivery dates, fulfilled quantities and order-status history. Purchase-order intake is not the same as unconstrained customer demand or the delivery schedule. A forecast built on orders alone can reproduce suppressed demand during stockouts.

Check that every reporting month is complete before treating absent transactions as zero. Use only information known at each historical forecast date; a record's current confirmed status alone cannot recreate exactly what a planner knew months earlier.

## 2. Refresh the outlook and select models using evidence

Maintain 6- and 12-month supply outlooks with 1- and 3-month checkpoints. Refresh monthly, or more frequently if reliable data and operational decisions justify it. Keep human review for exceptional events, new contracts and material supply changes.

The prototype compares a trailing six-month average, a robust damped trend, damped Holt exponential smoothing and ARIMA(1,1,1). The [Holt documentation](https://www.statsmodels.org/stable/generated/statsmodels.tsa.holtwinters.Holt.html) describes damped-trend forecasting, while the [ARIMA documentation](https://www.statsmodels.org/stable/generated/statsmodels.tsa.arima.model.ARIMA.html) specifies its model parameters. Statistical methods are not guaranteed to outperform the baseline. Model choice can differ between countries and between A, B and C; product labels alone do not justify assigning different demand patterns.

The default **Trend outlook** projects recent movement using the median pairwise slope of the latest six months. Its level blends the fitted endpoint with the latest three-month median. Volatility shrinks the slope, an initial monthly cap of 15% limits extrapolation, and a damping factor of 0.8 gradually reduces future trend increments. These are disclosed planning assumptions. The projection is deterministic and does not add random fluctuations; constant histories can remain flat. Users can choose **Best historical fit** to see the historically selected method instead.

At each test origin, train only on the preceding history and predict the entire requested horizon. Compare all eligible models on the same origins using MAE, RMSE, WAPE and signed bias. Failed fits are excluded, and ties favor the baseline. Overlapping test windows are identified rather than treated as independent observations. Country and product forecasts sum into consistent regional totals; real products need comparable units before quantities can be combined meaningfully.

**The present evidence is limited:** with 12 reporting months and at least six training months, there are six eligible one-month test origins, four three-month origins, one six-month origin, and no twelve-month origin. **Best historical fit** requires at least three origins for exploratory model selection, so that view retains the baseline for both long horizons. **Trend outlook** provides a separate planning projection rather than claiming a validated long-range winner. Even where model selection is possible, the selection sample is not an independent test of improvement. These safeguards are prototype choices, not a claim of statistical certainty.

Low/high planning scenarios add or subtract one recent monthly standard deviation multiplied by the square root of the forecast lead, with a floor of zero. They are deterministic stress scenarios rather than calibrated confidence intervals. Strategic recommendations flag projected movement beyond 10% for supply review, recent variability above 25% for more frequent demand reviews, and relevant cross-sell or retention follow-ups using reconciled customer histories. Each recommendation gives its evidence and owner. These thresholds identify reviews; they do not infer stockouts, profitable growth or purchase quantities.

The six-month average is a proxy baseline. The business's 6-to-12-month planning horizon does not imply that its existing forecast is a six-month average. Save actual forecast versions and planner overrides to test the proposed method against the real traditional approach.

## 3. Add richer AI capabilities as data becomes available

With a longer, reliable history, test seasonal methods and pooled machine-learning models across the 15 series. Candidate features include lagged demand, past rolling averages, country calendars, known tender and contract dates, installed equipment, planned promotions and prices known when the forecast is made. Retain only features available at the forecast origin; future actual prices or realized tender wins would leak information.

Collect at least two to three years of monthly history to investigate annual patterns and support longer-horizon tests; data volume alone does not guarantee a stable seasonal effect. Compare new candidates against simple baselines and the existing business plan. Calibrate prediction intervals by horizon and monitor their actual coverage before relying on them for service-level decisions. The current prototype does not assert calibrated confidence intervals.

Once stock, inbound supply, lead times, expiry and capacity data are available, translate forecast demand into replenishment scenarios and exception alerts. Demand forecasts alone do not determine purchase quantities. Supply constraints, shelf life and customer service requirements still need explicit planning rules.

## 4. Increase planner and salesperson productivity

Automated aggregation and repeatable comparisons reduce repeated spreadsheet consolidation. A shared dashboard and exports give country teams a common planning view. Planners can focus review time on large forecast changes, persistent bias, insufficient data and potential supply gaps.

The prototype recomputes when its input data or horizon changes and caches repeated comparisons. It does not install a scheduler, connect live ERP feeds, send alerts or create purchase orders. Those integrations are the next operational steps.

Measure productivity through planner hours per forecasting cycle, elapsed time to publish the regional plan and the share of exceptions resolved within the agreed review period. Establish baseline measurements before the pilot; no time saving has been measured here.

## 5. Uncover value-added sales opportunities

Use reconciled customer histories to find accounts buying only part of the A/B/C portfolio. The prototype ranks missing-product reviews by adoption among other active customers in the same country, followed by the account's recent purchasing volume. Peer purchasing is a useful prompt for investigation, not evidence of product fit or a conversion probability.

Account managers should validate customer needs, product eligibility, contracts, availability and contribution margin before making an offer. The existing declining-customer view identifies accounts for retention investigation; order timing, seasonality and supply problems can also explain declines. Forecasts can support account conversations and planned bundles once these commercial checks are complete.

Track each accepted lead through outreach, conversion and realized contribution margin. Where practical, use a randomized holdout or phased pilot to estimate incremental sales relative to normal account activity. Measure retention and avoided stockout losses separately to avoid counting the same benefit twice. The current data contains quantities, not prices, margins, stock balances or sales outcomes, so it cannot quantify profitable sales uplift.

## Suggested pilot and success measures

1. Reconcile data and confirm reporting completeness across all five countries. Capture the existing plans and planning effort before changing the process.
2. Run candidate forecasts alongside business-as-usual plans at identical cutoffs. Evaluate each horizon once its actual outcomes are available; a short pilot cannot validate a 12-month forecast.
3. Let planners review forecast exceptions and account managers review a controlled set of sales leads. Record reasons for overrides and rejected recommendations.
4. Compare horizon-specific error and bias, planning hours, stockout and expiry measures where available, and incremental contribution margin from the sales pilot. Scale only where the evidence supports the change.

## Demonstrate it in the app

Run `python -m streamlit run app.py`, then open **Regional Sales Analytics → Forecasting**. Choose a country, product, forecast horizon and forecast view. **Outlook & actions** shows three decision metrics, the forecast with planning scenarios, and three strategic recommendation cards. **Regional priorities** compares all 15 country–product combinations. Use the **ⓘ** button for a brief model explanation. Increases appear in green and decreases in red. Download the monthly or regional outlook as needed. **Sales Insights** shows reconciled customer coverage, ranked product reviews and declining accounts.

The strongest defensible conclusion is that this approach creates a repeatable, testable decision process. Better long-range accuracy, lower planning effort and profitable sales growth remain outcomes to establish with real data.
