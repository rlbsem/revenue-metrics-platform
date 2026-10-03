"""Create a clean source ZIP and verify its file manifest; no GitHub publishing."""

import hashlib
import json
import re
import zipfile

from revenue_platform.runtime import ROOT, canonical

EXCLUDED = {
    ".git",
    ".venv",
    ".local",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "target",
    "logs",
    "dbt_packages",
}
MANIFEST = "docs/evidence/package-manifest.json"


def delivered_files():
    return sorted(
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and not any(
            part in EXCLUDED or part.endswith(".egg-info") for part in p.relative_to(ROOT).parts
        )
        and p.suffix not in (".pyc", ".p8", ".pem")
        and p.name not in (".env", "secrets.toml")
    )


def scan(files):
    signatures = [
        re.compile(r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    ]
    for path in files:
        if path.suffix.lower() in (".jpg", ".png"):
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in signatures):
            raise ValueError(f"Potential secret in {path.relative_to(ROOT)}")
    # Also reject actual local credential files even though they would not be packaged.
    assert not (ROOT / ".env").exists(), "Move local secrets outside the deliverable repository"


def main():
    verification = json.loads((ROOT / "docs/evidence/verification.json").read_text())
    assert verification["local_execution"] == "passed"
    assert verification["enterprise"]["status"] == "passed"
    files = delivered_files()
    assert not any(p.stat().st_size > 2_000_000 for p in files), (
        "Unexpected large deliverable; generated sources belong under .local"
    )
    scan(files)
    inventory = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if p.relative_to(ROOT).as_posix() != MANIFEST
    }
    (ROOT / MANIFEST).write_text(
        canonical(
            {
                "algorithm": "SHA-256",
                "scope": "Every packaged file except this manifest itself (no self-referential hash).",
                "files": inventory,
            }
        ),
        encoding="utf-8",
    )
    output = ROOT.parent / "Richard_Butts_Revenue_Metrics_Platform_Established_Enterprise.zip"
    files = delivered_files()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            name = "revenue-metrics-platform/" + path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        actual = {
            n.removeprefix("revenue-metrics-platform/"): hashlib.sha256(archive.read(n)).hexdigest()
            for n in archive.namelist()
            if n != "revenue-metrics-platform/" + MANIFEST
        }
        assert actual == inventory
        assert len(archive.namelist()) == len(set(archive.namelist()))
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(sha + "  " + output.name + "\n", encoding="utf-8")
    print(
        canonical(
            {
                "zip": str(output),
                "files": len(files),
                "sha256": sha,
                "manifest": "all packaged file hashes verified",
                "secret_scan": "passed narrow signature and credential-file checks; manual review also required",
            }
        )
    )


if __name__ == "__main__":
    main()
