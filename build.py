#!/usr/bin/env python3
"""Build index.html from src/page.html + data/senate-polls-2026.json.

    python3 build.py              -> writes index.html (full document, for GitHub Pages)
    python3 build.py --fragment OUT -> also writes the page fragment (no html/head/body) to OUT
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
data = json.load(open(ROOT / "data" / "senate-polls-2026.json", encoding="utf-8"))
page = (ROOT / "src" / "page.html").read_text(encoding="utf-8")

# Inline the data; escape "</" so a stray "</script>" inside a string can never end the data block.
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
fragment = page.replace("__DATA__", blob)

doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Interactive tracker of 2026 U.S. Senate polls: every public poll, race averages, trend charts and the seat math, updated {data['generated']}.">
{fragment}
</head>
<body></body>
</html>
"""
# The fragment's markup must live in <body>; move everything after </style> there.
head_end = fragment.index("</style>") + len("</style>")
head_part, body_part = fragment[:head_end], fragment[head_end:]
doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Interactive tracker of 2026 U.S. Senate polls: every public poll, race averages, trend charts and the seat math, updated {data['generated']}.">
{head_part}
</head>
<body>
{body_part}
</body>
</html>
"""
(ROOT / "index.html").write_text(doc, encoding="utf-8")
print("wrote index.html", len(doc) // 1024, "KB")
if len(sys.argv) > 2 and sys.argv[1] == "--fragment":
    pathlib.Path(sys.argv[2]).write_text(fragment, encoding="utf-8")
    print("wrote fragment", sys.argv[2])
