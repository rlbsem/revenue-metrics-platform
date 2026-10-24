"""Offline evidence/source/preservation gates; does not execute dbt or cloud APIs."""

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs/evidence/performance"


def check():
    preserved = json.loads((FOLDER / "preserved-files.json").read_text())
    for name, expected in preserved["files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    for profile in ("enterprise", "stress"):
        folder = FOLDER / profile
        if not (folder / "completed.json").exists():
            continue
        protocol = json.loads((folder / "execution-protocol.json").read_text())
        for name, expected in protocol["source_sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
        pairs = [json.loads(p.read_text()) for p in folder.glob("pair-*.json")]
        assert len(pairs) >= 3
        for pair in pairs:
            for key in ("baseline_optimized_parity", "optimized_full_parity", "noop_parity"):
                assert len(pair[key]["models"]) == 15
                assert all(
                    r["differences"] == 0 and r["exact_multiset_equal"] for r in pair[key]["models"]
                )
            assert pair["consumer_payload_equal"]
            for name, stage in pair["stages"].items():
                if "nodes" in stage["result"]:
                    assert all(n["status"] in ("pass", "success") for n in stage["result"]["nodes"])
                    assert (
                        sum(n["node"].startswith("test.") for n in stage["result"]["nodes"]) == 30
                    )
    manifest = FOLDER / "manifest.json"
    if manifest.exists():
        record = json.loads(manifest.read_text())
        names = {
            str(p.relative_to(FOLDER)).replace("\\", "/")
            for p in FOLDER.rglob("*")
            if p.is_file() and p != manifest
        }
        assert names == set(record["files"]), "Evidence file set differs"
        for name, expected in record["files"].items():
            assert hashlib.sha256((FOLDER / name).read_bytes()).hexdigest() == expected, name
    links = 0
    for md in [ROOT / "README.md", ROOT / "docs/performance.md", *FOLDER.rglob("*.md")]:
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", md.read_text(encoding="utf-8")):
            if not target.startswith(("http:", "https:", "#")):
                assert (md.parent / target.split("#")[0]).exists(), (str(md), target)
                links += 1
    print(
        json.dumps({"preserved_files": len(preserved["files"]), "links": links, "status": "passed"})
    )


if __name__ == "__main__":
    check()
