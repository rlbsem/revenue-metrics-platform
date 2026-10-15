"""Cloud boundary controls. Unit tests are not evidence of Snowflake execution."""

from contextlib import contextmanager
import csv
from decimal import Decimal
from itertools import islice
import json
import os
from pathlib import Path


REQUIRED = (
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_CONSUMER_USER",
    "SNOWFLAKE_CONSUMER_PRIVATE_KEY_PATH",
)


def credentials(role):
    prefix = "SNOWFLAKE_CONSUMER_" if role == "RMP_CONSUMER" else "SNOWFLAKE_"
    return {
        "user": os.environ[prefix + "USER"],
        "path": Path(os.environ[prefix + "PRIVATE_KEY_PATH"]),
        "passphrase": os.environ.get(prefix + "PRIVATE_KEY_PASSPHRASE"),
    }


def connect(role):
    from cryptography.hazmat.primitives import serialization
    import snowflake.connector

    auth = credentials(role)
    key = serialization.load_pem_private_key(
        auth["path"].read_bytes(),
        password=auth["passphrase"].encode() if auth["passphrase"] else None,
    )
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=auth["user"],
        private_key=key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        role=role,
        database="REVENUE_METRICS_DEMO",
        warehouse="RMP_WH",
        login_timeout=30,
        network_timeout=60,
        session_parameters={
            "QUERY_TAG": os.environ.get("RMP_QUERY_TAG", "rmp_preflight"),
            "STATEMENT_TIMEOUT_IN_SECONDS": 300,
            "LOCK_TIMEOUT": 5,
        },
    )
    try:
        cursor = conn.cursor()
        cursor.execute("USE SECONDARY ROLES NONE")
        if cursor.execute("SELECT CURRENT_ROLE()").fetchone()[0] != role:
            raise AssertionError("Unexpected active role")
        if role == "RMP_CONSUMER":
            cursor.execute("SHOW GRANTS")  # current user's grants; no account identifier in SQL
            names = [column[0].lower() for column in cursor.description]
            roles = {dict(zip(names, row, strict=True))["role"] for row in cursor.fetchall()}
            if roles - {"PUBLIC", "RMP_CONSUMER"}:
                raise AssertionError("Consumer user has additional role grants")
    except Exception:
        conn.close()
        raise
    return conn


def distinct_credentials():
    """One key capable of logging into both users is not credential isolation."""
    from cryptography.hazmat.primitives import serialization

    identities = [credentials(role) for role in ("RMP_BUILD", "RMP_CONSUMER")]
    if identities[0]["user"].upper() == identities[1]["user"].upper():
        raise ValueError("Consumer requires a separate user")
    public = []
    for auth in identities:
        key = serialization.load_pem_private_key(
            auth["path"].read_bytes(),
            password=auth["passphrase"].encode() if auth["passphrase"] else None,
        )
        public.append(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    if public[0] == public[1]:
        raise ValueError("Consumer requires a separate key pair")


def load_csv(cursor, path, table, ddl, query_ids):
    """Validate the real source contract before constructing INSERT placeholders."""
    columns = [field.strip().split()[0] for field in ddl.split(",")]
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != columns:
            raise ValueError("CSV columns do not match the declared source contract")
        total = 0
        while rows := list(islice(reader, 10000)):
            if any(len(row) != len(columns) for row in rows):
                raise ValueError("CSV row width differs from source contract")
            cursor.executemany(
                f"INSERT INTO RAW.{table} ({','.join(columns)}) "
                f"VALUES ({','.join('%s' for _ in columns)})",
                rows,
            )
            query_ids.append(cursor.sfqid)
            total += len(rows)
    return total


def warehouse_preflight(cursor, *, owner_confirmed_fixture_monitor=False):
    cursor.execute("SHOW WAREHOUSES LIKE 'RMP_WH'")
    names = [column[0].lower() for column in cursor.description]
    warehouses = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    exact = [row for row in warehouses if row["name"] == "RMP_WH"]
    if len(exact) != 1:
        raise AssertionError("Dedicated warehouse not visible")
    row = exact[0]
    if (
        row["size"].upper().replace("-", "") != "XSMALL"
        or not 0 < int(row["auto_suspend"]) <= 60
        or int(row["max_cluster_count"]) != 1
        or str(row.get("type", "STANDARD")).upper() != "STANDARD"
        or str(row.get("enable_query_acceleration", "false")).lower() == "true"
    ):
        raise AssertionError("Warehouse exceeds bounded standard XSMALL configuration")
    monitor = row.get("resource_monitor")
    monitor_visible = bool(monitor) and str(monitor).lower() != "null"
    if monitor_visible and monitor != "RMP_FIXTURE_BUDGET":
        raise AssertionError("Unexpected dedicated fixture monitor")
    if not monitor_visible and not owner_confirmed_fixture_monitor:
        raise AssertionError("Assign the owner-approved resource monitor before execution")
    observed = {
        key: row.get(key)
        for key in (
            "size",
            "auto_suspend",
            "min_cluster_count",
            "max_cluster_count",
            "type",
            "enable_query_acceleration",
            "resource_monitor",
        )
    }
    observed["monitor_evidence"] = (
        "Runtime-visible attachment; monitor quota requires administrator evidence"
        if monitor_visible
        else "Not visible to runtime role; owner confirmed successful administrator postflight "
        "for RMP_FIXTURE_BUDGET, one credit, NEVER reset, 90/100 suspension thresholds"
    )
    return observed


@contextmanager
def exclusive_run(conn):
    """Hold a dedicated DML transaction; all DDL uses other connections.

    The administrator creates exactly one gate row. No application INSERT/DDL
    privilege is granted on this table. Competing supported verifiers time out.
    A process/connection failure releases the transaction; this is not a lease.
    """
    cursor = conn.cursor()
    cursor.execute("BEGIN")
    try:
        cursor.execute("UPDATE OPS.ACCEPTANCE_GATE SET holder='running' WHERE singleton=1")
        if cursor.rowcount != 1:
            raise AssertionError("Acceptance gate must contain exactly one administrator-owned row")
        yield
    finally:
        cursor.execute("ROLLBACK")


def expect_denied(cursor, name, sql, query_ids):
    from snowflake.connector.errors import ProgrammingError

    mutation = sql.lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE "))
    if mutation:
        cursor.execute("BEGIN")
    try:
        cursor.execute(sql)
    except ProgrammingError as error:
        # Snowflake intentionally conceals unauthorized object existence (002003).
        # Callers first establish the target with the authorized builder.
        if error.sqlstate != "42501" and error.errno not in (2003, 3001):
            raise
        if getattr(error, "sfqid", None):
            query_ids.append(error.sfqid)
        return {"operation": name, "denied": True, "sqlstate": error.sqlstate, "errno": error.errno}
    finally:
        # A mistakenly overprivileged reader must not corrupt the trusted release.
        if mutation:
            cursor.execute("ROLLBACK")
    raise AssertionError("Unexpected consumer authority: " + name)


def publication_state(cursor):
    rows = cursor.execute(
        "SELECT release_id, TO_JSON(payload) FROM CONSUMER.CURRENT_RELEASE ORDER BY release_id"
    ).fetchall()
    if len(rows) > 1:
        raise AssertionError("Current release is not a singleton")
    return [(key, json.loads(body)) for key, body in rows]


def promote(cursor, release_id, payload, *, fail_before_commit=False):
    """Only DML inside this boundary. View creation/grants happen before it."""
    cursor.execute("BEGIN")
    try:
        cursor.execute(
            "INSERT INTO CONSUMER.RELEASE_HISTORY "
            "SELECT %s,PARSE_JSON(%s) WHERE NOT EXISTS "
            "(SELECT 1 FROM CONSUMER.RELEASE_HISTORY WHERE release_id=%s)",
            (release_id, json.dumps(payload), release_id),
        )
        cursor.execute("DELETE FROM CONSUMER.CURRENT_RELEASE")
        cursor.execute(
            "INSERT INTO CONSUMER.CURRENT_RELEASE SELECT %s,PARSE_JSON(%s)",
            (release_id, json.dumps(payload)),
        )
        if fail_before_commit:
            raise RuntimeError("deliberate_publication_failure")
        cursor.execute("COMMIT")
    except Exception:
        cursor.execute("ROLLBACK")
        raise


def metric_projection(rows):
    """Compare numeric meaning without requiring identical adapter decimal scales."""
    result = {
        (row["metric_id"], row["region"]): None
        if row["value"] is None
        else Decimal(str(row["value"]))
        for row in rows
    }
    if len(result) != len(rows):
        raise AssertionError("Duplicate metric grain in consumer projection")
    return result
