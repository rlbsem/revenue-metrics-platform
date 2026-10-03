import csv
import io
import json

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from revenue_platform.consumer import analyst_csv, metric_rows, review
from revenue_platform.runtime import (
    ROOT,
    assert_expected,
    local_snapshot,
    make_payload,
    publish,
    run_dbt,
    validate_and_publish,
)


def publish_test(db, folder):
    snapshot = local_snapshot(db)
    assert_expected(snapshot)
    return publish(make_payload(snapshot, "1.0", "duckdb"), folder)


def test_consumer_parity_and_streamlit(built, tmp_path, monkeypatch):
    pub = tmp_path / "published"
    release = publish_test(built, pub)
    monkeypatch.setenv("REVENUE_PUBLICATION", str(pub))
    result = review(pub)
    rows = list(csv.DictReader(io.StringIO(analyst_csv(result))))
    assert {r["metric_id"]: r["value"] for r in rows} == {
        r["metric_id"]: r["value"] or "" for r in metric_rows(result)
    }
    assert all(r["release_id"] == release for r in rows)
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    assert [m.value for m in app.metric] == [
        "$4,000",
        "$18,000",
        "$16,000",
        "$11,000",
        "$18,000",
        "20.0%",
    ]
    app.selectbox[0].select("East").run()
    assert not app.exception
    assert app.metric[0].value == "$4,000"
    assert app.metric[5].value == "Not mature"


def test_stale_consumer_and_tampered_payload_rejected(built, tmp_path):
    pub = tmp_path / "published"
    release = publish_test(built, pub)
    with pytest.raises(ValueError, match="Stale"):
        review(pub, "0" * 64)
    file = pub / f"{release}.json"
    body = json.loads(file.read_text())
    body["metrics"][0]["value"] = "999"
    file.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="content"):
        review(pub)


def test_metric_definition_change_is_new_release(database, tmp_path):
    pub = tmp_path / "published"
    first = publish_test(database, pub)
    run_dbt(database, tmp_path / "v2", policy="2.0")
    snap = local_snapshot(database)
    assert_expected(snap, "2.0")
    second = publish(make_payload(snap, "2.0", "duckdb"), pub)
    assert first != second
    assert (pub / f"{first}.json").exists()
    with pytest.raises(ValueError, match="Stale"):
        review(pub, first)
    assert (
        next(r for r in metric_rows(review(pub)) if r["metric_id"] == "period_end_arr")["value"]
        == "12000.00000000"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "delete from raw.coverage where source_name='booking_events'",
        "drop table raw.invoice_lines",
    ],
)
def test_failed_build_or_quality_gate_preserves_release(database, tmp_path, mutation):
    pub = tmp_path / "published"
    first = publish_test(database, pub)
    with duckdb.connect(str(database)) as con:
        con.execute(mutation)
    with pytest.raises(RuntimeError):
        validate_and_publish(database, tmp_path / "failed", pub)
    assert review(pub)["release_id"] == first


def test_unknown_region_and_path_traversal_refused(built, tmp_path):
    pub = tmp_path / "published"
    publish_test(built, pub)
    with pytest.raises(ValueError):
        metric_rows(review(pub), "UNKNOWN")
    pointer = json.loads((pub / "current.json").read_text())
    pointer["file"] = "../data.json"
    (pub / "current.json").write_text(json.dumps(pointer))
    with pytest.raises(ValueError, match="path"):
        review(pub)
