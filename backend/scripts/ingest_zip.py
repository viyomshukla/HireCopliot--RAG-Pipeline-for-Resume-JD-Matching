"""
Ingests a ZIP of resumes (or a folder) into chunks.

    python scripts/ingest_zip.py --zip resumes.zip
    python scripts/ingest_zip.py --dir data/sample_resumes --job-id demo
    python scripts/ingest_zip.py --dir data/sample_resumes --out data/chunks.json

Writes chunks to data/chunks_<job_id>.json unless --out is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.ingestion.batch import format_report, ingest_directory, ingest_zip  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--zip", type=Path)
    p.add_argument("--dir", type=Path)
    p.add_argument("--job-id", default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    if not args.zip and not args.dir:
        sys.exit("give --zip or --dir")

    try:
        result = (ingest_zip(args.zip, args.job_id) if args.zip
                  else ingest_directory(args.dir, args.job_id))
    except ValueError as exc:
        sys.exit(f"rejected: {exc}")

    print(format_report(result))

    out = args.out or BASE / "data" / f"chunks_{result.job_id}.json"
    out.write_text(json.dumps(result.chunks, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nwritten to {out}")

if __name__ == "__main__":
    main()