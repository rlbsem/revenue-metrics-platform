"""Both consumers read the same tested dbt metric rows; no business formulas here."""

import csv
import io
import json
from pathlib import Path

from revenue_platform.runtime import digest


def review(publication: Path, expected_release=None):
    pointer = json.loads((publication / "current.json").read_text(encoding="utf-8"))
    release_id = pointer["release_id"]
    if len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
        raise ValueError("Invalid release identifier")
    if pointer["file"] != f"{release_id}.json":
        raise ValueError("Invalid release path")
    if expected_release and release_id != expected_release:
        raise ValueError("Stale consumer: expected release differs from current")
    payload = json.loads((publication / pointer["file"]).read_text(encoding="utf-8"))
    if digest(payload) != release_id:
        raise ValueError("Release content does not match its identifier")
    for row in payload["metrics"]:
        if (
            row["period_start"] != payload["period_start"]
            or row["period_end"] != payload["period_end"]
        ):
            raise ValueError("Metric reporting period differs")
        match = [
            d
            for d in payload["definitions"]
            if d["metric_id"] == row["metric_id"] and d["version"] == row["definition_version"]
        ]
        if len(match) != 1:
            raise ValueError("Missing or ambiguous metric definition")
    return {"release_id": release_id, **payload}


def metric_rows(result, region="ALL"):
    rows = [r for r in result["metrics"] if r["region"] == region]
    if len(rows) != 6:
        raise ValueError("Unknown region or incomplete metric set")
    return rows


def analyst_csv(result, region="ALL"):
    rows = [{"release_id": result["release_id"], **r} for r in metric_rows(result, region)]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
