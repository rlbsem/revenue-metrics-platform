"""Source-level economic checks for generated profiles, independent of metric SQL."""

CHECKS = {
    "booking_event_totals": """with b as (
        select a.commercial_event_id,sum(b.amount_cents) cents,count(*) components
        from raw.booking_events b join raw.booking_attributes a using(booking_event_id)
        group by 1)
        select count(*) from raw.commercial_events e left join b using(commercial_event_id)
        where b.cents is null or b.cents<>e.amount_cents or b.components<>12""",
    "booking_lineage": """select count(*) from raw.booking_events b
        left join raw.booking_attributes a using(booking_event_id)
        left join raw.commercial_events e using(commercial_event_id)
        where e.commercial_event_id is null or b.contract_id<>e.contract_id
        or b.event_type<>e.event_type or b.event_date<>e.event_date""",
    "won_commercial_authority": """select count(*) from raw.commercial_events e
        join raw.subscription_contracts c using(contract_id)
        where not exists (select 1 from raw.opportunity_events o
          where o.opportunity_id=e.opportunity_id and o.account_id=c.account_id
          and o.stage='won' and o.effective_date<=e.event_date)
        or exists (select 1 from raw.opportunity_events o
          where o.opportunity_id=e.opportunity_id and o.stage='lost')""",
    "quarterly_event_dates": """select count(*) from raw.commercial_events
        where event_date<'2026-01-01' or event_date>'2026-03-31'
        or event_date<term_start or event_date>=term_end""",
    "commitment_economics": """select count(*) from raw.commercial_events
        where amount_cents <> case when event_type in ('new_business','renewal')
        then new_monthly_cents*12 else
        floor((new_monthly_cents-prior_monthly_cents)*12.0
        * datediff('day',event_date,term_end)/datediff('day',term_start,term_end)) end""",
    "booked_fee_matches_contract": """select count(*) from raw.commercial_events e
        left join raw.subscription_contracts c using(contract_id)
        where c.contract_id is null or
        (case when e.event_type='cancellation' then e.prior_monthly_cents
         else e.new_monthly_cents end) * c.billing_months <> c.fee_cents""",
    "invoice_fee_and_service_dates": """with i as (
        select invoice_id,contract_id,min(event_date) issued_on,sum(amount_cents) cents
        from raw.invoice_lines where line_type='charge' group by 1,2)
        select count(*) from i left join raw.subscription_contracts c using(contract_id)
        where c.contract_id is null or i.cents<>c.fee_cents
        or i.issued_on<c.start_date or i.issued_on>=c.end_date""",
    "descriptor_references": """select count(*) from raw.contract_attributes a
        left join raw.subscription_contracts c using(contract_id)
        where c.contract_id is null or not exists (
        select 1 from raw.opportunity_events o where o.opportunity_id=a.opportunity_id
        and o.account_id=c.account_id and o.stage='won')""",
    "installed_wins_outside_current_cohort": """select count(*) from raw.opportunity_events
        where origin in ('acquisition_history','renewal') and created_date>='2026-01-01'""",
    "fee_segments_do_not_overlap": """select count(*) from raw.subscription_contracts a
        join raw.contract_attributes aa on a.contract_id=aa.contract_id
        join raw.contract_attributes bb on aa.opportunity_id=bb.opportunity_id
        join raw.subscription_contracts b on b.contract_id=bb.contract_id
        where a.contract_id<b.contract_id and a.start_date<b.end_date and b.start_date<a.end_date""",
}


def validate_scenario(conn):
    errors = {name: conn.execute(sql).fetchone()[0] for name, sql in CHECKS.items()}
    for table, key in [
        ("account_attributes", "account_id"),
        ("contract_attributes", "contract_id"),
        ("commercial_events", "commercial_event_id"),
        ("booking_attributes", "booking_event_id"),
    ]:
        errors[table + "_keys"] = conn.execute(
            f"select count(*)-count(distinct {key}) from raw.{table}"
        ).fetchone()[0]
    if any(errors.values()):
        raise AssertionError(f"Generated source economics failed: {errors}")
    return errors
