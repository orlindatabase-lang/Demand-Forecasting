"""
Keep api/.cache in a Cloud Storage bucket so it survives Cloud Run restarts
and redeploys (production log, frozen past weeks, trained-model forecasts,
similar-design graph).

Enabled only when the CACHE_BUCKET environment variable is set (on Cloud
Run); without it every function here does nothing, so local runs are
unchanged.

- download_recent(): at startup, copy the bucket's files into api/.cache
  (SQLite files, similar_design.json, and model forecasts from the last
  DOWNLOAD_DAYS days - older snapshots are only needed for backtesting).
- upload_changed(): after a refresh, upload every .json/.sqlite file that
  changed since the last upload. SQLite files are copied with SQLite's own
  backup API first, so a write happening at the same moment can't produce
  a half-written copy.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

BUCKET = os.getenv("CACHE_BUCKET", "").strip()
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
DOWNLOAD_DAYS = 35

_LOCK = threading.Lock()
_UPLOADED: dict[str, float] = {}  # file name -> mtime at its last upload/download
_DATED = re.compile(r"_(\d{4}-\d{2}-\d{2})\.json$")


def _bucket():
    from google.cloud import storage
    return storage.Client().bucket(BUCKET)


def _wanted(name: str) -> bool:
    if not name.endswith((".json", ".sqlite")):
        return False
    m = _DATED.search(name)
    if m is None:
        return True  # undated: weekly_production_log.sqlite, forecast_freeze.sqlite, similar_design.json
    return date.fromisoformat(m.group(1)) >= date.today() - timedelta(days=DOWNLOAD_DAYS)


def download_recent() -> None:
    """Copy the bucket's files into api/.cache (call once, before the first data load)."""
    if not BUCKET:
        return
    with _LOCK:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            count = 0
            for blob in _bucket().list_blobs():
                if "/" in blob.name or not _wanted(blob.name):
                    continue
                target = CACHE_DIR / blob.name
                blob.download_to_filename(str(target))
                _UPLOADED[blob.name] = target.stat().st_mtime
                count += 1
            print(f"[cache_sync] downloaded {count} files from gs://{BUCKET}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - start without history rather than not at all
            print(f"[cache_sync] download failed ({exc!r})", file=sys.stderr)


def upload_changed() -> None:
    """Upload every api/.cache .json/.sqlite file changed since its last upload."""
    if not BUCKET:
        return
    with _LOCK:
        try:
            bucket = _bucket()
            count = 0
            for path in sorted(CACHE_DIR.glob("*")):
                if not path.is_file() or path.suffix not in (".json", ".sqlite"):
                    continue
                mtime = path.stat().st_mtime
                if _UPLOADED.get(path.name) == mtime:
                    continue
                if path.suffix == ".sqlite":
                    with tempfile.TemporaryDirectory() as tmp:
                        copy = Path(tmp) / path.name
                        src, dst = sqlite3.connect(path), sqlite3.connect(copy)
                        try:
                            src.backup(dst)
                        finally:
                            dst.close()
                            src.close()
                        bucket.blob(path.name).upload_from_filename(str(copy))
                else:
                    bucket.blob(path.name).upload_from_filename(str(path))
                _UPLOADED[path.name] = mtime
                count += 1
            if count:
                print(f"[cache_sync] uploaded {count} changed files to gs://{BUCKET}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - next refresh retries
            print(f"[cache_sync] upload failed ({exc!r})", file=sys.stderr)
