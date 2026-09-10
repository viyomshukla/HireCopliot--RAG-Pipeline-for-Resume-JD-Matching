"""
Parses every JD in data/sample_jds/ into a rubric.

    python scripts/run_jd_parsing.py                # all
    python scripts/run_jd_parsing.py --limit 1      # try one first
    python scripts/run_jd_parsing.py --file jd_002.txt
    python scripts/run_jd_parsing.py --no-cache

Needs an API key in backend/.env, same as extraction. Results are cached, so
re-running costs no quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.ranking.jd_parser import describe, parse_jd  # noqa: E402

JD_DIR = BASE / "data" / "sample_jds"
OUTPUT_FILE = BASE / "data" / "parsed_jds.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.exit("python-dotenv not installed. Run: pip install python-dotenv")
    env = BASE / ".env"
    if not env.exists():
        sys.exit(f"no .env at {env}")
    load_dotenv(env)

    files = sorted(JD_DIR.glob("*.txt"))
    if args.file:
        files = [JD_DIR / args.file]
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no .txt files in {JD_DIR} - run generate_sample_jds.py first")

    out = {}
    failures = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            parsed = parse_jd(text, use_cache=not args.no_cache)
        except Exception as exc:
            failures.append((path.stem, f"{type(exc).__name__}: {exc}"))
            print(f"{path.stem}  FAILED  {type(exc).__name__}")
            continue

        out[path.stem] = parsed.model_dump(mode="json")
        print("\n" + "=" * 64)
        print(f"{path.stem}")
        print("=" * 64)
        print(describe(parsed))

    OUTPUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nparsed {len(out)}/{len(files)} -> {OUTPUT_FILE}")
    for name, err in failures:
        print(f"  FAILED {name}: {err[:150]}")


if __name__ == "__main__":
    main()