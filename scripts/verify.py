"""Complete fixture and enterprise verification, with separate evidence layers."""

import json
import re
import shutil

from revenue_platform.runtime import ROOT, canonical
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
        "github_actions": "configured; not executed in GitHub during this local verification",
    }
    (evidence / "verification.json").write_text(canonical(result), encoding="utf-8")
    print(canonical(result))


if __name__ == "__main__":
    main()
