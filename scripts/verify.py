"""Complete fixture and enterprise verification, with separate evidence layers."""

import hashlib
import json
import re
import shutil

from revenue_platform.consumer import review
from revenue_platform.runtime import ROOT, MODELS, assert_expected, canonical, source_fingerprint
from verify_fixture import main as verify_fixture
from verify_enterprise import verify as verify_enterprise


def check_links():
    broken = []
    for md in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in md.relative_to(ROOT).parts):
            continue
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", md.read_text(encoding="utf-8")):
            if link.startswith(("http:", "https:", "#", "mailto:")):
                continue
            path = link.split("#")[0]
            if path and not (md.parent / path).exists():
                broken.append(str(md.relative_to(ROOT)) + ": " + link)
    assert not broken, broken


def recorded_snowflake_fixture():
    """Validate archived native evidence offline; never imply CI executed Snowflake."""
    folder = ROOT / "docs/evidence/snowflake/fixture"
    path = folder / "verification.json"
    if not path.exists():
        return {"status": "not_recorded"}
    record = json.loads(path.read_text())
    assert record["status"] == "passed" and record["executed_cloud"] is True
    assert record["acceptance_passed"] is True and record["profile"] == "fixture"
    assert record["warehouse_final_state"] == "SUSPENDED"
    for name, expected in record["implementation_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    assert {r["model"] for r in record["model_comparison"]} == set(MODELS)
    assert all(
        r["exact_multiset_equal"] and r["column_contract_equal"] for r in record["model_comparison"]
    )
    # The earlier native publication is intentionally withdrawn from this USD-only
    # working tree. Native query IDs, comparisons and security evidence remain recorded,
    # but we cannot claim the revised USD model was rerun against Snowflake.
    assert len(record["query_ids"]) > 0
    return {
        "status": "historical_native_prior_unit",
        "scope": "Prior model's Snowflake comparisons are historical; current USD metrics require separate native acceptance",
        "evidence": "snowflake/fixture/verification.json",
        "finished_at": record["finished_at"],
        "implementation_hashes_match": True,
        "exact_original_native_publication_retained": False,
        "usd_native_execution": False,
    }


def main():
    evidence = ROOT / "docs/evidence"
    (evidence / "verification.json").write_text(
        canonical({"local_execution": "checks_in_progress"}), encoding="utf-8"
    )
    verify_fixture()
    enterprise = verify_enterprise()
    check_links()
    fixture = json.loads((evidence / "fixture/verification.json").read_text())
    shutil.copyfile(evidence / "fixture/test-results.json", evidence / "test-results.json")
    result = {
        "local_execution": "passed",
        "fixture": fixture,
        "enterprise": enterprise,
        "markdown_links": "passed",
        "snowflake": json.loads((evidence / "snowflake-status.json").read_text())["status"],
        "recorded_snowflake_fixture": recorded_snowflake_fixture(),
        "github_actions": "configured; not executed in GitHub during this local verification",
    }
    (evidence / "verification.json").write_text(canonical(result), encoding="utf-8")
    print(canonical(result))


if __name__ == "__main__":
    main()
