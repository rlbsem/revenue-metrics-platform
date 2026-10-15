"""Owner handoff safety contracts; these tests do not execute Snowflake SQL."""

import importlib.util
import io
import json

import pytest
from snowflake.connector.util_text import split_statements

from revenue_platform.runtime import ROOT


@pytest.fixture
def setup_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_access", ROOT / "scripts/prepare_snowflake_access.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conflicts_stop_inside_one_block_before_any_creation():
    sql = (ROOT / "snowflake/bootstrap.sql").read_text()
    statements = [item for item in split_statements(io.StringIO(sql)) if item[1] is not None]
    assert len(statements) == 2
    assert "USE ROLE ACCOUNTADMIN" in statements[0][0]
    block = statements[1][0]
    assert "EXECUTE IMMEDIATE $$" in block
    first_create = block.index("CREATE RESOURCE MONITOR RMP_FIXTURE_BUDGET")
    for kind in ("database", "warehouse", "monitor", "role", "user"):
        assert block.index(f"RAISE {kind}_conflict;") < first_create
    # Never silently adopt, replace or drop an unknown resource or registered key.
    commands = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("--"))
    assert "IF NOT EXISTS" not in commands and "OR REPLACE" not in commands
    assert "DROP " not in commands and "ALTER USER" not in commands


def test_bounded_settings_checked_before_compute_and_minimal_warehouse_grants():
    sql = (ROOT / "snowflake/bootstrap.sql").read_text()
    commands = [line.strip() for line in sql.splitlines() if not line.lstrip().startswith("--")]
    grants = [line for line in commands if "ON WAREHOUSE" in line and "GRANT " in line]
    assert grants == [
        "GRANT USAGE ON WAREHOUSE RMP_WH TO ROLE RMP_LOADER;",
        "GRANT USAGE, OPERATE ON WAREHOUSE RMP_WH TO ROLE RMP_BUILD;",
        "GRANT USAGE ON WAREHOUSE RMP_WH TO ROLE RMP_CONSUMER;",
    ]
    assert sql.index("RAISE monitor_invalid;") < sql.index("USE WAREHOUSE RMP_WH;")
    for expected in (
        "wh:auto_suspend::INTEGER=60",
        "wh:resource_monitor::VARCHAR='RMP_FIXTURE_BUDGET'",
        "rm:credit_quota::NUMBER(38,9)=1",
        "UPPER(rm:frequency::VARCHAR)='NEVER'",
        "rm:level::VARCHAR='WAREHOUSE'",
        "rm:suspend_at::VARCHAR, '%', ''))=90",
        "rm:suspend_immediately_at::VARCHAR, '%', ''))=100",
        "ALTER WAREHOUSE RMP_WH SUSPEND;",
    ):
        assert expected in sql
    assert sql.index("ALTER WAREHOUSE RMP_WH SUSPEND;") < sql.index("'READY_FOR_FIXTURE'")


def test_refresh_preserves_existing_config_and_keys(tmp_path, setup_module, monkeypatch):
    # Emulate an external private directory even when pytest's base temp is inside ROOT.
    source = tmp_path / "repository"
    (source / "snowflake").mkdir(parents=True)
    (source / "snowflake/bootstrap.sql").write_bytes(
        (ROOT / "snowflake/bootstrap.sql").read_bytes()
    )
    monkeypatch.setattr(setup_module, "ROOT", source)
    directory = tmp_path / "private"
    setup_module.prepare(directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != "owner-setup.sql"}
    (directory / "owner-setup.sql").write_text("old SQL")
    result = setup_module.refresh_owner_sql(directory)
    assert result["executed_sql"] is False and result["keys_and_config"] == "unchanged"
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.name in before}
    sql = (directory / "owner-setup.sql").read_text()
    assert "__RUNNER_PUBLIC_KEY__" not in sql and "__READER_PUBLIC_KEY__" not in sql
    assert sql.count("TYPE=SERVICE RSA_PUBLIC_KEY='") == 2
    assert sql.count("DEFAULT_SECONDARY_ROLES=()") == 2
    for role in ("RMP_LOADER", "RMP_BUILD"):
        assert f"GRANT ROLE {role} TO USER RMP_ACCEPTANCE_RUNNER;" in sql
    assert "GRANT ROLE RMP_CONSUMER TO USER RMP_ACCEPTANCE_READER;" in sql
    with pytest.raises(FileExistsError):
        setup_module.prepare(directory)
    config = directory / "runtime.json"
    settings = json.loads(config.read_text())
    settings["SNOWFLAKE_CONSUMER_PRIVATE_KEY_PATH"] = settings["SNOWFLAKE_PRIVATE_KEY_PATH"]
    settings["SNOWFLAKE_CONSUMER_PRIVATE_KEY_PASSPHRASE"] = settings[
        "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
    ]
    config.write_text(json.dumps(settings))
    safe_sql = (directory / "owner-setup.sql").read_bytes()
    with pytest.raises(ValueError, match="independent keys"):
        setup_module.refresh_owner_sql(directory)
    assert (directory / "owner-setup.sql").read_bytes() == safe_sql
