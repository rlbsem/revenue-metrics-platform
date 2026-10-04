"""Business review of a validated release, never an independently calculated dashboard."""

import os

from pathlib import Path


import pandas as pd

import streamlit as st


from revenue_platform.consumer import analyst_csv, metric_rows, review


st.set_page_config(page_title="Commercial review | Revenue Metrics", page_icon="◈", layout="wide")

st.markdown("### REVENUE METRICS / QUARTERLY BUSINESS REVIEW")

st.title("One quarter. Six different questions.")

st.caption("Synthetic B2B subscription business · Q1 2026 · No accounting compliance claim")

publication = Path(os.environ.get("REVENUE_PUBLICATION", ".local/demo/published"))

try:
    result = review(publication)

except (OSError, ValueError, KeyError) as error:
    st.error(f"No valid release available: {error}")

    st.info("Run revenue demo --workspace .local/demo, then refresh.")

    st.stop()


region = st.selectbox(
    "Reporting region", ["ALL", *sorted({r["region"] for r in result["metrics"]} - {"ALL"})]
)

rows = metric_rows(result, region)

by_name = {r["metric_id"]: r for r in rows}

labels = [
    ("open_pipeline", "Open pipeline"),
    ("net_bookings", "Net bookings"),
    ("net_invoiced", "Net invoiced"),
    ("cash_received", "Cash received"),
    ("period_end_arr", "Period-end ARR"),
    ("cohort_win_rate", "30-day win rate"),
]

for column, (key, label) in zip(st.columns(6), labels, strict=True):
    row = by_name[key]

    amount = None if row["value"] is None else float(row["value"])

    display = (
        "Not mature"
        if amount is None
        else (
            f"{amount:.1%}"
            if row["unit"] == "ratio"
            else (f"${amount / 1e6:,.2f}M" if abs(amount) >= 1e6 else f"${amount:,.0f}")
        )
    )

    column.metric(label, display)

st.caption(
    f"Reporting period: {result['period_start']} to {result['period_end']} · Balances at period end · ARR policy {result['arr_policy']} · {result['target']} execution"
)

st.info(
    "Installed ARR ≠ quarterly bookings ≠ quarterly invoicing ≠ quarterly cash. "
    "ARR includes contracts acquired in prior years. Only this quarter’s signed new business, renewals and amendments enter bookings. Invoices follow billing schedules; cash follows payment timing."
    if "dataset" in result
    else "Pipeline is potential business. Bookings are signed commitments. Invoices request payment. Cash records allocations. ARR annualizes eligible recurring contracts; it is not recognized revenue."
)


if "dataset" in result:
    scale = result["dataset"]

    counts = scale["source_counts"]

    st.caption(
        f"{scale['profile'].title()} profile · {scale['accounts']:,} accounts · "
        f"{scale['opportunities']:,} opportunities · {scale['active_subscriptions']:,} active subscriptions · "
        f"{counts['booking_events']:,} booking components · {counts['invoice_lines']:,} invoice lines · "
        f"{counts['payment_allocations']:,} payment allocations · Seed {scale['seed']}"
    )


if "dataset" in result:
    timeline = result["dataset"].get("timeline")
    if timeline:
        st.caption(
            f"Established customer base · {timeline['active_accounts_without_q1_bookings']:,} "
            "active accounts have no Q1 booking. Their eligible contracts still contribute ARR."
        )
        with st.expander("Installed base and this quarter’s commercial activity"):
            st.dataframe(
                pd.DataFrame(timeline["acquisition_years"]), hide_index=True, width="stretch"
            )
            st.dataframe(
                pd.DataFrame(timeline["q1_commercial_events"]), hide_index=True, width="stretch"
            )
            st.caption(
                "Renewals carry prior-quarter opportunity creation dates. Current-quarter sales cohorts include open, won and lost expansion/retention proposals; installed customers are not assigned automatic Q1 wins."
            )

st.subheader("Why the numbers differ")

st.caption(
    "Company-wide account bridge (first 20 IDs for generated profiles); region filters affect the six cards only. Historical ownership differs across event dates."
)

bridge = []

for item in result["bridge"]:
    bridge.append(
        {
            "Account": item.get("account_name", item["account_id"]),
            **{
                label: f"${float(item[key]):,.0f}"
                for key, label in [
                    ("bookings", "Bookings"),
                    ("invoiced", "Net invoices"),
                    ("cash", "Cash"),
                    ("bookings_less_invoices", "Bookings minus invoices"),
                    ("invoices_less_cash", "Invoice / cash gap"),
                ]
            },
        }
    )

st.dataframe(pd.DataFrame(bridge), hide_index=True, width="stretch")

st.write(
    "The first gap reflects signed commitments, billing schedules and amendments. "
    "The second compares quarterly invoice and cash flows; collections may settle opening invoices. It is not an accounts-receivable balance."
)

for item in result["bridge"][:3]:
    st.caption(f"{item.get('account_name', item['account_id'])}: {item['explanation']}.")


if "dataset" in result:
    with st.expander("Subscription portfolio · segment and offering"):
        portfolio = pd.DataFrame(result["dataset"]["portfolio"])

        segment = st.selectbox("Portfolio segment", ["ALL", *sorted(portfolio["segment"].unique())])

        product = st.selectbox(
            "Portfolio offering", ["ALL", *sorted(portfolio["product"].unique())]
        )

        if segment != "ALL":
            portfolio = portfolio[portfolio["segment"] == segment]

        if product != "ALL":
            portfolio = portfolio[portfolio["product"] == product]

        st.caption(
            "Company-wide period-end contract counts and governed ARR fact rollups. These selectors filter this table only; the six commercial cards use reporting region."
        )

        st.dataframe(portfolio, hide_index=True, width="stretch")


st.subheader("Coverage and confidence")

coverage = pd.DataFrame(result["coverage"])[["source_name", "complete_from", "complete_through"]]

coverage.columns = ["Source", "Covered from", "Covered through"]

st.dataframe(coverage, hide_index=True, width="stretch")

st.caption(
    "Coverage is a supplied source assertion checked before release, not independent proof of upstream completeness."
)

st.write(f"Immature opportunities excluded: {by_name['cohort_win_rate']['excluded']}")

st.write(
    "Source corrections restate historical results; the release identifier preserves the reviewed version."
)


st.subheader("Cohorts need time to mature")

st.dataframe(pd.DataFrame(result["cohorts"]), hide_index=True, width="stretch")

st.subheader("Definitions used in this review")

for key, label in labels:
    row = by_name[key]

    definition = next(
        d
        for d in result["definitions"]
        if d["metric_id"] == key and d["version"] == row["definition_version"]
    )

    with st.expander(f"{label} · v{definition['version']} · {definition['owner']}"):
        st.write(definition["definition"])

        for field in ("grain", "time_basis", "filters", "null_behavior", "additivity", "caveats"):
            st.write(f"**{field.replace('_', ' ').capitalize()}:** {definition[field]}")

st.download_button(
    "Download analyst CSV — same metrics",
    analyst_csv(result, region),
    file_name="commercial-review.csv",
    mime="text/csv",
)

st.caption(f"Validated release: {result['release_id']}")
