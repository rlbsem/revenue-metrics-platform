"""Validate the public Git file set and exact delivery hashes before regenerating examples."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docs/evidence/package-manifest.json"


def main():
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    names = set(
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True
        ).splitlines()
    ) - {MANIFEST}
    if names != set(record["files"]):
        raise ValueError("Public package file set differs from Git inventory")
    for name, expected in record["files"].items():
        path = (ROOT / name).resolve()
        if (
            not path.is_relative_to(ROOT)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Package hash or boundary differs: " + name)
    print(json.dumps({"package_hashes": len(names), "status": "passed"}))


if __name__ == "__main__":
    main()
