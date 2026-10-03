"""Seeded source economics, streamed to local CSVs; no target metric is an input."""

import csv
from contextlib import ExitStack
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random

from revenue_platform.runtime import ROOT, TABLES, canonical, digest

ATTRIBUTES = {
    "account_attributes": "account_id varchar,segment varchar,sales_team varchar",
    "contract_attributes": "contract_id varchar,product varchar,opportunity_id varchar,acquired_on date,term_start date,renewal_date date",
    "commercial_events": "commercial_event_id varchar,contract_id varchar,opportunity_id varchar,event_date date,event_type varchar,amount_cents bigint,prior_monthly_cents bigint,new_monthly_cents bigint,term_start date,term_end date",
    "booking_attributes": "booking_event_id varchar,commercial_event_id varchar",
}


def configuration(profile):
    if profile not in ("enterprise", "stress"):
        raise ValueError("Generated profile must be enterprise or stress")
    return json.loads((ROOT / "profiles" / f"{profile}.json").read_text())


def file_hash(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def generate(destination: Path, profile="enterprise", *, config=None):
    """Refuse replacement. Same config and generator version produce identical files."""
    cfg = configuration(profile) if config is None else dict(config)
    destination.mkdir(parents=True, exist_ok=False)
    rng = random.Random(cfg["seed"])
    price_rng = random.Random(cfg["seed"])
    counts = {}
    jan = date(2026, 1, 1)
    end = date(2026, 3, 31)
    schemas = {**TABLES, **ATTRIBUTES, "late_opportunity_events": TABLES["opportunity_events"]}
    with ExitStack() as stack:
        writers = {}
        for name, schema in schemas.items():
            stream = stack.enter_context(
                (destination / f"{name}.csv").open("w", newline="", encoding="utf-8")
            )
            writers[name] = csv.writer(stream, lineterminator="\n")
            writers[name].writerow([field.split()[0] for field in schema.split(",")])
            counts[name] = 0

        def emit(name, *row):
            writers[name].writerow(row)
            counts[name] += 1

        def opportunity(oid, aid, created, closed, stage, amount, origin, corrected=False):
            events = [(oid + "_open", oid, aid, created, created, "open", amount, origin, 1)]
            if stage != "open" and closed <= end:
                events.append((oid + "_close", oid, aid, created, closed, stage, amount, origin, 1))
            for event in events:
                emit("opportunity_events", *event)
                if corrected:
                    revision = list(event)
                    revision[3] = created - timedelta(days=2)
                    if event[5] == "open":
                        revision[4] = revision[3]
                        revision[6] += amount // 20
                    revision[-1] = 2
                    emit("late_opportunity_events", *revision)

        def commercial(eid, cid, oid, when, kind, amount, old_fee, new_fee, term_start, term_end):
            emit(
                "commercial_events",
                eid,
                cid,
                oid,
                when,
                kind,
                amount,
                old_fee,
                new_fee,
                term_start,
                term_end,
            )
            for part in range(12):
                cents = amount // 12 if part < 11 else amount - (amount // 12) * 11
                bid = eid + f"B{part:02d}"
                emit("booking_events", bid, cid, aid, when, cents, kind)
                emit("booking_attributes", bid, eid)

        def next_month(d):
            return date(d.year + (d.month == 12), d.month % 12 + 1, d.day)

        for n in range(1, cfg["accounts"] + 1):
            aid = f"A{n:06d}"
            segment = "SMB" if n % 100 < 60 else ("Mid-market" if n % 100 < 90 else "Enterprise")
            region = ["North", "East", "West", "EMEA", "APAC"][n % 5]
            owner = f"Team {n % 12 + 1:02d} / Rep {n % 240 + 1:03d}"
            boundary = date(2026, 2, 15)
            changed = n % 7 == 0
            emit(
                "account_history",
                aid + "_1",
                aid,
                f"Synthetic Account {n:06d}",
                owner,
                region,
                "2018-01-01",
                boundary if changed else "2099-01-01",
            )
            if changed:
                emit(
                    "account_history",
                    aid + "_2",
                    aid,
                    f"Synthetic Account {n:06d}",
                    f"Team {(n + 1) % 12 + 1:02d} / Rep {(n + 17) % 240 + 1:03d}",
                    ["North", "East", "West", "EMEA", "APAC"][(n + 1) % 5],
                    boundary,
                    "2099-01-01",
                )
            emit("account_attributes", aid, segment, f"Team {n % 12 + 1:02d}")
            product = ["Core", "Growth", "Suite"][n % 3]
            monthly = (
                cfg["segment_monthly_cents"][segment]
                * cfg["product_percent"][product]
                * price_rng.randint(80, 120)
                // 10000
            )
            annual = monthly * 12
            bill_day = rng.randint(1, 28)
            new_customer = rng.random() < cfg["new_customer_probability"]
            renewal_month = rng.randint(1, 12)
            acquired = date(
                2026 if new_customer else rng.randint(2020, 2025),
                rng.randint(1, 3) if new_customer else renewal_month,
                bill_day,
            )
            term_start = (
                acquired
                if new_customer
                else date(2026 if renewal_month <= 3 else 2025, renewal_month, bill_day)
            )
            term_end = term_start.replace(year=term_start.year + 1)
            oid = f"O{n:06d}H"
            acquisition_created = acquired - timedelta(days=rng.randint(35, 100))
            prospects = []
            for j in range(2):
                poid = f"O{n:06d}P{j}"
                created = jan + timedelta(days=rng.randrange(2, 85))
                closed = created + timedelta(days=rng.randint(10, 45))
                stage = rng.choices(["open", "won", "lost"], cfg["prospect_outcome_weights"])[0]
                amount = annual * rng.randint(10, 50) // 100
                opportunity(
                    poid,
                    aid,
                    created,
                    closed,
                    stage,
                    amount,
                    "expansion" if j == 0 else "retention",
                    n % cfg["correction_every"] == 0,
                )
                if stage == "won" and closed <= end:
                    prospects.append((closed, poid, j))
            term = 1 if n % 10 < 7 else 12
            change = None
            if term == 1 and not new_customer and prospects:
                closed, poid, j = min(prospects)
                effective = date(2026, closed.month, bill_day)
                if effective < closed:
                    effective = next_month(effective)
                if effective <= end:
                    change = (
                        effective,
                        poid,
                        ("expansion" if n % 2 == 0 else "upgrade") if j == 0 else "contraction",
                    )
            old_monthly = (
                monthly
                if not change
                else monthly * (90 if change[2] in ("upgrade", "expansion") else 110) // 100
            )
            opportunity(
                oid,
                aid,
                acquisition_created,
                acquired,
                "won",
                old_monthly * 12,
                "new_business" if new_customer else "acquisition_history",
            )
            cid = f"C{n:06d}A"
            prior_cid = cid + "PRE"
            segments = [(cid, acquired, term_end, monthly)]
            if change:
                segments = [
                    (prior_cid, acquired, change[0], old_monthly),
                    (cid, change[0], term_end, monthly),
                ]
            for seg_id, starts, ends, fee in segments:
                emit(
                    "subscription_contracts",
                    seg_id,
                    aid,
                    starts,
                    ends,
                    fee * term,
                    term,
                    "recurring",
                    0,
                    int(n % 17 == 0),
                )
                emit("contract_attributes", seg_id, product, oid, acquired, term_start, term_end)
            if new_customer or term_start >= jan:
                event_fee = monthly if not change or change[0] <= term_start else old_monthly
                event_oid = oid
                if not new_customer:
                    event_oid = f"O{n:06d}R"
                    opportunity(
                        event_oid,
                        aid,
                        term_start - timedelta(days=100),
                        term_start,
                        "won",
                        event_fee * 12,
                        "renewal",
                    )
                event_cid = cid if not change or change[0] <= term_start else prior_cid
                commercial(
                    cid + "SIGN",
                    event_cid,
                    event_oid,
                    term_start,
                    "new_business" if new_customer else "renewal",
                    event_fee * 12,
                    0,
                    event_fee,
                    term_start,
                    term_end,
                )
            if change and change[0] != term_start:
                change_term_start = (
                    term_start if change[0] >= term_start else term_start.replace(year=2025)
                )
                change_term_end = change_term_start.replace(year=change_term_start.year + 1)
                delta = (
                    (monthly - old_monthly)
                    * 12
                    * (change_term_end - change[0]).days
                    // (change_term_end - change_term_start).days
                )
                commercial(
                    cid + "AMEND",
                    cid,
                    change[1],
                    change[0],
                    change[2],
                    delta,
                    old_monthly,
                    monthly,
                    change_term_start,
                    change_term_end,
                )

            def invoice(invoice_id, contract_id, issued, fee, billing_months, cancellation=None):
                gross = fee * billing_months
                parts = 3 if billing_months == 1 else 12
                for part in range(parts):
                    value = (
                        gross // parts
                        if part < parts - 1
                        else gross - (gross // parts) * (parts - 1)
                    )
                    emit(
                        "invoice_lines",
                        invoice_id + f"L{part:02d}",
                        invoice_id,
                        contract_id,
                        aid,
                        issued,
                        value,
                        "charge",
                    )
                cancellation_credit = 0
                if cancellation and cancellation < next_month(issued):
                    cancellation_credit = (
                        gross
                        * (next_month(issued) - cancellation).days
                        // (next_month(issued) - issued).days
                    )
                    if cancellation_credit:
                        emit(
                            "invoice_lines",
                            invoice_id + "CANCEL",
                            invoice_id,
                            contract_id,
                            aid,
                            cancellation,
                            -cancellation_credit,
                            "credit",
                        )
                credit = (gross - cancellation_credit) // 20 if n % 5 == 0 else 0
                if credit:
                    emit(
                        "invoice_lines",
                        invoice_id + "CR",
                        invoice_id,
                        contract_id,
                        aid,
                        issued + timedelta(days=3),
                        -credit,
                        "credit",
                    )
                net = gross - credit - cancellation_credit
                settle = net if n % 4 else net * 3 // 4
                first_pay = settle * 2 // 5
                for part, value in enumerate([first_pay, settle - first_pay]):
                    paid_on = issued + timedelta(days=7 + part * (12 + n % 19))
                    if paid_on <= end and value > 0:
                        emit(
                            "payment_allocations",
                            invoice_id + f"P{part}",
                            invoice_id + f"RCPT{part}",
                            invoice_id,
                            aid,
                            paid_on,
                            value,
                        )

            bills = (
                [date(2026, month, bill_day) for month in (1, 2, 3)] if term == 1 else [term_start]
            )
            if term == 1 and not new_customer:
                bills.insert(0, date(2025, 12, bill_day))
            for issued in bills:
                if issued < acquired:
                    continue
                segment_id = cid if not change or issued >= change[0] else prior_cid
                fee = monthly if not change or issued >= change[0] else old_monthly
                invoice(cid + issued.isoformat(), segment_id, issued, fee, term)
            if n % cfg["ended_contract_every"] == 0 and not new_customer:
                ended_id = f"C{n:06d}E"
                cancelled = date(2026, rng.randint(1, 3), rng.randint(1, 28))
                acquired_end = date(rng.randint(2020, 2024), rng.randint(4, 12), bill_day)
                prior_term = acquired_end.replace(year=2025)
                next_term = prior_term.replace(year=2026)
                historical_oid = f"O{n:06d}EH"
                opportunity(
                    historical_oid,
                    aid,
                    acquired_end - timedelta(days=60),
                    acquired_end,
                    "won",
                    annual,
                    "acquisition_history",
                )
                emit(
                    "subscription_contracts",
                    ended_id,
                    aid,
                    acquired_end,
                    cancelled,
                    monthly,
                    1,
                    "recurring",
                    0,
                    0,
                )
                emit(
                    "contract_attributes",
                    ended_id,
                    product,
                    historical_oid,
                    acquired_end,
                    prior_term,
                    next_term,
                )
                reversal = (
                    -monthly * 12 * (next_term - cancelled).days // (next_term - prior_term).days
                )
                commercial(
                    ended_id + "CANCEL",
                    ended_id,
                    historical_oid,
                    cancelled,
                    "cancellation",
                    reversal,
                    monthly,
                    0,
                    prior_term,
                    next_term,
                )
                for issued in [
                    date(2025, 12, bill_day),
                    *[date(2026, month, bill_day) for month in (1, 2, 3)],
                ]:
                    if issued < cancelled:
                        invoice(
                            ended_id + issued.isoformat(), ended_id, issued, monthly, 1, cancelled
                        )
        for day in range(90):
            current = jan + timedelta(days=day)
            emit(
                "calendar",
                current,
                current.replace(day=1),
                int((current + timedelta(days=1)).month != current.month),
            )
        for name in TABLES:
            if name not in ("calendar", "coverage"):
                emit("coverage", name, jan, end)
    files = {p.name: file_hash(p) for p in sorted(destination.glob("*.csv"))}
    manifest = {
        "generator_version": 2,
        "config": cfg,
        "row_counts": counts,
        "files": files,
        "source_fingerprint": digest(files),
    }
    (destination / "dataset-manifest.json").write_text(canonical(manifest), encoding="utf-8")
    return manifest
