import sys; sys.path.insert(0, ".")
from pathlib import Path
from app.chunking.chunker import parse_document, split_into_sections, chunk_resume

p = next(Path("data/uploads").rglob("*Cloud*"))
doc = parse_document(p)
print(f"{p.name}: {len(doc.lines)} lines, {doc.char_count} chars\n")

print("--- first 30 lines as parsed ---")
for l in doc.lines[:30]:
    print(f"  size={l.font_size:4.1f} bold={l.is_bold:d} bullet={l.is_bullet:d}  {l.text[:56]!r}")

sections, unknown = split_into_sections(doc)
print(f"\n--- sections found ---")
for name, lines in sections:
    print(f"  {name:16} {len(lines):3} lines")
print(f"  unrecognised headings: {unknown}")

chunks, report = chunk_resume(p)
sizes = sorted(c["metadata"]["char_count"] for c in chunks)
print(f"\n--- {len(chunks)} chunks, sizes {sizes[:8]} ... {sizes[-3:]} ---")
for c in chunks[:8]:
    print(f"  [{c['metadata']['section']:14}] {c['metadata']['char_count']:4}  {c['text'][:52]!r}")