"""Apply an aggregate memory guard to benchmark workers AND parent-side comparisons."""

import json
from pathlib import Path
import subprocess
import sys
import time

import psutil


def main():
    args = sys.argv[1:]
    workspace = Path(args[args.index("--workspace") + 1])
    evidence = workspace.parent / (workspace.name + "-supervisor.json")
    command = [sys.executable, str(Path(__file__).with_name("benchmark_performance.py")), *args]
    process = subprocess.Popen(command)
    root = psutil.Process(process.pid)
    peak, available, reason = 0, psutil.virtual_memory().available, "benchmark_exit"
    try:
        while process.poll() is None:
            try:
                members = [root, *root.children(recursive=True)]
            except psutil.NoSuchProcess:
                members = []
            rss = 0
            for member in members:
                try:
                    rss += member.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            peak = max(peak, rss)
            available = min(available, psutil.virtual_memory().available)
            if rss > 6 * 1024**3 or psutil.virtual_memory().available < 1024**3:
                reason = "aggregate_memory_guard"
                for member in root.children(recursive=True):
                    member.kill()
                process.kill()
                break
            time.sleep(0.2)
    finally:
        code = process.wait()
        record = {
            "scope": "Sampled aggregate benchmark tree including parent comparisons",
            "peak_sampled_rss_bytes": peak,
            "minimum_host_available_bytes": available,
            "rss_limit_bytes": 6 * 1024**3,
            "minimum_available_bytes": 1024**3,
            "returncode": code,
            "reason": reason,
            "sample_seconds": 0.2,
        }
        evidence.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record), flush=True)
    return code or (1 if reason == "aggregate_memory_guard" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
