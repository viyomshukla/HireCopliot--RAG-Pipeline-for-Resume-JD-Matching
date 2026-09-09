"""
Runs LLM extraction over every resume in chunks.json.

    python scripts/run_extraction.py                # all resumes
    python scripts/run_extraction.py --limit 5      # try 5 first
    python scripts/run_extraction.py --no-cache     # ignore cached results

Needs at least one key in backend/.env:
    GOOGLE_API_KEY, GOOGLE_API_KEY_2, GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY

The run is RESUMABLE. If a provider's daily quota runs out mid-batch, run the
command again -- everything already extracted is served from the cache for free
and only the missing resumes cost quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.extraction.extractor import extract_batch, group_chunks_by_resume  # noqa: E402

CHUNKS_FILE = BASE / "data" / "chunks.json"
OUTPUT_FILE = BASE / "data" / "extractions.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the first N resumes")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.exit("python-dotenv not installed. Run: pip install python-dotenv")

    env_path = BASE / ".env"
    if not env_path.exists():
        sys.exit(f"no .env file at {env_path}")
    load_dotenv(env_path)

    if not CHUNKS_FILE.exists():
        sys.exit(f"missing {CHUNKS_FILE} - run the chunker first")

    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    grouped = group_chunks_by_resume(chunks)
    if args.limit:
        grouped = dict(list(grouped.items())[:args.limit])

    print(f"extracting {len(grouped)} resumes "
          f"({sum(len(v) for v in grouped.values())} chunks)\n")

    records, failures = extract_batch(
        grouped, workers=args.workers, use_cache=not args.no_cache
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps([r.model_dump(mode="json") for r in records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    flagged = [r for r in records if r.metadata.issues]
    print(f"\nextracted : {len(records)}/{len(grouped)}")
    print(f"flagged   : {len(flagged)} with completeness issues")
    if failures:
        print(f"failed    : {len(failures)}")
        for name, err in failures[:5]:
            print(f"  {name}: {err[:160]}")
        if len(failures) > 5:
            print(f"  ... and {len(failures) - 5} more")
        print("\nRe-run the same command to retry only the missing ones.")
    print(f"\nwritten to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()