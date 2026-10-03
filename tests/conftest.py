import shutil

import pytest

from revenue_platform.runtime import load_local, run_dbt


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("built")
    db = root / "warehouse.duckdb"
    load_local(db, late=True)
    run_dbt(db, root)
    return db


@pytest.fixture
def database(built, tmp_path):
    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(built, db)
    return db
