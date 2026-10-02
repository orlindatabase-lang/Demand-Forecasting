"""
Daily batch job (2026-10-02): all the heavy work, then exit.

    1. restore api/.cache from Cloud Storage (cache_sync; no-op without CACHE_BUCKET)
    2. load the data (BigQuery + ERP) and publish the plan - data.rebuild()
    3. wait for model training (if this snapshot's models aren't saved yet);
       training also records the weekly production log
    4. save the serving snapshot the small API loads (data.save_serving_snapshot)
    5. upload everything that changed back to Cloud Storage

Run (from the api/ directory):  ..\\venv\\Scripts\\python.exe job.py
On Cloud Run it runs as a Cloud Run Job (same image, command: python job.py),
triggered daily by Cloud Scheduler. Exit code 0 = success, 1 = data load failed.
"""
from __future__ import annotations

import os
import sys
import time

# Load synchronously in this process; no daily-refresh timer (the job IS the refresh).
os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
os.environ.setdefault("DAILY_REFRESH", "off")

import cache_sync  # noqa: E402
import data  # noqa: E402


def main() -> int:
    started = time.time()
    print("[job] start", file=sys.stderr)
    cache_sync.download_recent()

    result = data.rebuild()
    print(f"[job] data loaded: {result}", file=sys.stderr)
    if result.get("source") == "mock":
        print("[job] FAILED: real data could not be loaded", file=sys.stderr)
        return 1

    training = data._LGBM_THREAD
    if training is not None and training.is_alive():
        print("[job] waiting for model training...", file=sys.stderr)
        training.join()
    print(f"[job] model: {data.model_status()}", file=sys.stderr)

    data.save_serving_snapshot()
    cache_sync.upload_changed()
    print(f"[job] done in {(time.time() - started) / 60:.1f} min", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
