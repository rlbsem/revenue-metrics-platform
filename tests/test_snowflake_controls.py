"""Local orchestration and SQL tests. No Snowflake success is inferred."""

from decimal import Decimal
import importlib.util
import json

import duckdb
import pytest
from snowflake.connector.errors import ProgrammingError

from revenue_platform.runtime import ROOT, TABLES
from revenue_platform.snowflake_acceptance import multiset_difference_sql
from revenue_platform.snowflake_controls import (
    REQUIRED,
    distinct_credentials,
    expect_denied,
    exclusive_run,
    load_csv,
    metric_projection,
    promote,
    warehouse_preflight,
)


class Cursor:
    def __init__(self, fail=None):
        self.statements = []
        self.sfqid = "unit-test-query"
        self.rowcount = 1
        self.fail = fail

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if self.fail and self.fail in sql:
            raise RuntimeError("injected")
        return self

    def executemany(self, sql, rows):
        self.statements.append((sql, rows))


def test_real_correction_csv_uses_nine_declared_columns():
    cursor, ids = Cursor(), []
    assert (
        load_csv(
            cursor,
            ROOT / "data/late_opportunity_events.csv",
            "opportunity_events",
            TABLES["opportunity_events"],
            ids,
        )
        == 2
    )
    statement, rows = cursor.statements[0]
    assert statement.count("%s") == len(rows[0]) == 9
    assert ids == ["unit-test-query"]


@pytest.mark.parametrize("reuse", ["user", "public_key", "neither"])
def test_consumer_requires_independent_identity_and_key(tmp_path, monkeypatch, reuse):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second = (
        first
        if reuse == "public_key"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    for prefix, user, key in [
        ("SNOWFLAKE_", "RUNNER", first),
        ("SNOWFLAKE_CONSUMER_", "RUNNER" if reuse == "user" else "READER", second),
    ]:
        path = tmp_path / (prefix + ".p8")
        # Different encryption passwords must not hide the same underlying key.
        password = prefix.encode()
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            )
        )
        monkeypatch.setenv(prefix + "USER", user)
        monkeypatch.setenv(prefix + "PRIVATE_KEY_PATH", str(path))
        monkeypatch.setenv(prefix + "PRIVATE_KEY_PASSPHRASE", password.decode())
    if reuse == "neither":
        distinct_credentials()
    else:
        with pytest.raises(ValueError, match="separate"):
            distinct_credentials()


@pytest.mark.parametrize("contents", ["wrong\n1\n", "a,b\n1\n"])
def test_source_contract_rejected_before_insert(tmp_path, contents):
    path = tmp_path / "invalid.csv"
    path.write_text(contents)
    cursor = Cursor()
    with pytest.raises(ValueError):
        load_csv(cursor, path, "test", "a varchar,b varchar", [])
    assert not cursor.statements


def test_duplicate_aware_sql_executes_with_null_and_multiplicity():
    # Execute the comparison algorithm against actual tables, not canned counts.
    # This proves its relational behavior on DuckDB, not Snowflake compatibility.
    with duckdb.connect() as conn:
        conn.execute("create schema build_a; create schema full_b")
        for schema in ("build_a", "full_b"):
            conn.execute(f"create table {schema}.model as select 1 a,NULL::varchar b")
        query = multiset_difference_sql("build_a", "full_b", "model")
        assert conn.execute(query).fetchone()[0] == 0
        conn.execute("insert into build_a.model values (1,NULL)")
        assert conn.execute(query).fetchone()[0] == 1
        assert (
            conn.execute(multiset_difference_sql("full_b", "build_a", "model")).fetchone()[0] == 1
        )


def test_publication_rollback_precedes_any_commit():
    cursor = Cursor()
    with pytest.raises(RuntimeError, match="deliberate_publication_failure"):
        promote(cursor, "release", {"synthetic": True}, fail_before_commit=True)
    commands = [sql for sql, _ in cursor.statements]
    assert commands[0] == "BEGIN" and commands[-1] == "ROLLBACK"
    assert "COMMIT" not in commands
    assert not any("CREATE" in sql or "GRANT" in sql for sql in commands)


def test_database_error_rolls_back_and_never_commits():
    cursor = Cursor(fail="INSERT INTO CONSUMER.CURRENT_RELEASE")
    with pytest.raises(RuntimeError, match="injected"):
        promote(cursor, "release", {})
    assert cursor.statements[-1][0] == "ROLLBACK"
    assert all(sql != "COMMIT" for sql, _ in cursor.statements)


def test_gate_rolls_back_after_failure_and_refuses_invalid_cardinality():
    class Connection:
        def __init__(self):
            self.value = Cursor()

        def cursor(self):
            return self.value

    conn = Connection()
    with pytest.raises(RuntimeError, match="work failed"):
        with exclusive_run(conn):
            raise RuntimeError("work failed")
    assert conn.value.statements[-1][0] == "ROLLBACK"
    conn.value.rowcount = 0
    with pytest.raises(AssertionError, match="exactly one"):
        with exclusive_run(conn):
            pytest.fail("Invalid gate admitted a run")


@pytest.mark.parametrize(
    "errno,state,accepted",
    [
        (2003, "42S02", True),
        (3001, "42501", True),
        (1003, "42000", False),
        (250001, "08001", False),
    ],
)
def test_only_authorization_denials_are_permission_evidence(errno, state, accepted):
    class Denied:
        def execute(self, sql):
            raise ProgrammingError(msg="test", errno=errno, sqlstate=state, sfqid="denial-query")

    if accepted:
        ids = []
        assert expect_denied(Denied(), "raw_read", "SELECT", ids)["denied"]
        assert ids == ["denial-query"]
    else:
        with pytest.raises(ProgrammingError):
            expect_denied(Denied(), "raw_read", "SELECT", [])


def test_consumer_values_compare_decimals_without_hiding_duplicates():
    assert metric_projection([{"metric_id": "arr", "region": "ALL", "value": "18.00"}]) == {
        ("arr", "ALL"): Decimal("18")
    }
    with pytest.raises(AssertionError, match="Duplicate"):
        metric_projection([{"metric_id": "arr", "region": "ALL", "value": 1}] * 2)


def test_unexpected_consumer_write_permission_cannot_commit_probe():
    cursor = Cursor()
    with pytest.raises(AssertionError, match="Unexpected consumer authority"):
        expect_denied(
            cursor, "release_write", "UPDATE CONSUMER.CURRENT_RELEASE SET release_id='bad'", []
        )
    assert [sql for sql, _ in cursor.statements][::2] == ["BEGIN", "ROLLBACK"]


@pytest.mark.parametrize(
    "change",
    [
        {"size": "LARGE"},
        {"auto_suspend": 0},
        {"max_cluster_count": 2},
        {"resource_monitor": None},
        {"enable_query_acceleration": "true"},
    ],
)
def test_warehouse_preflight_refuses_cost_expansion(change):
    values = {
        "name": "RMP_WH",
        "size": "X-Small",
        "auto_suspend": 60,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "resource_monitor": "RMP_FIXTURE_BUDGET",
        "type": "STANDARD",
    }
    values.update(change)

    class Warehouse(Cursor):
        description = [(key,) for key in values]

        def fetchall(self):
            return [tuple(values.values())]

    with pytest.raises(AssertionError):
        warehouse_preflight(Warehouse())


@pytest.mark.parametrize("monitor", [None, "null", "OTHER_MONITOR"])
def test_owner_confirmation_does_not_invent_live_monitor_visibility(monitor):
    values = {
        "name": "RMP_WH",
        "size": "X-Small",
        "auto_suspend": 60,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "resource_monitor": monitor,
        "type": "STANDARD",
    }

    class Warehouse(Cursor):
        description = [(key,) for key in values]

        def fetchall(self):
            return [tuple(values.values())]

    if monitor == "OTHER_MONITOR":
        with pytest.raises(AssertionError, match="Unexpected dedicated"):
            warehouse_preflight(Warehouse(), owner_confirmed_fixture_monitor=True)
    else:
        result = warehouse_preflight(Warehouse(), owner_confirmed_fixture_monitor=True)
        assert result["resource_monitor"] == monitor
        assert "owner confirmed" in result["monitor_evidence"]
        values["size"] = "LARGE"
        with pytest.raises(AssertionError, match="bounded"):
            warehouse_preflight(Warehouse(), owner_confirmed_fixture_monitor=True)


def test_owner_fixture_confirmation_cannot_authorize_enterprise(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cloud_verifier", ROOT / "scripts/verify_snowflake.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in REQUIRED:
        monkeypatch.setenv(name, "unit-test-only")
    monkeypatch.setenv("RMP_ENTERPRISE_APPROVED", "yes")
    monkeypatch.setattr(module, "connect", lambda _: pytest.fail("Cloud connection attempted"))
    with pytest.raises(ValueError, match="cannot authorize another profile"):
        module.verify(tmp_path, profile="enterprise", owner_confirmed_fixture_monitor=True)


def test_missing_credentials_never_calls_cloud_and_clears_stale_success(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cloud_verifier", ROOT / "scripts/verify_snowflake.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "connect", lambda _: pytest.fail("Cloud connection attempted"))
    (tmp_path / "verification.json").write_text(json.dumps({"executed_cloud": True}))
    assert module.verify(tmp_path, require_cloud=True, profile="fixture") == 2
    result = json.loads((tmp_path / "verification.json").read_text())
    assert result["status"] == "pending_credentials" and result["executed_cloud"] is False


@pytest.mark.parametrize("reason", ["approval", "credentials"])
def test_preflight_failure_replaces_old_success_without_cloud(tmp_path, monkeypatch, reason):
    spec = importlib.util.spec_from_file_location(
        "cloud_verifier", ROOT / "scripts/verify_snowflake.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in REQUIRED:
        monkeypatch.setenv(name, "unit-test-only")
    monkeypatch.delenv("RMP_ENTERPRISE_APPROVED", raising=False)
    monkeypatch.setattr(module, "connect", lambda _: pytest.fail("Cloud connection attempted"))

    def invalid_credentials():
        raise ValueError("separate identity required")

    monkeypatch.setattr(module, "distinct_credentials", invalid_credentials)
    (tmp_path / "verification.json").write_text(json.dumps({"executed_cloud": True}))
    with pytest.raises(ValueError):
        module.verify(
            tmp_path,
            require_cloud=True,
            profile="enterprise" if reason == "approval" else "fixture",
        )
    result = json.loads((tmp_path / "verification.json").read_text())
    assert result["status"] == "failed" and result["executed_cloud"] is False
