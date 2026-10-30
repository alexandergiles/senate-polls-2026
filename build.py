#!/usr/bin/env python3
"""Build index.html from src/page.html + data/senate-polls-2026.json.

    python3 build.py              -> writes index.html (full document, for GitHub Pages)
    python3 build.py --fragment OUT -> also writes the page fragment (no html/head/body) to OUT
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
data = json.load(open(ROOT / "data" / "senate-polls-2026.json", encoding="utf-8"))
page = (ROOT / "src" / "page.html").read_text(encoding="utf-8")


def mean_of_recent(polls, keys, n=5):
    """The site's average: plain mean of the n most recent polls (by last day of fieldwork)."""
    recent = sorted(polls, key=lambda p: (p["end"], p["start"] or p["end"]))[-n:]
    vals = {}
    for k in keys:
        xs = [p["results"].get(k) for p in recent if p["results"].get(k) is not None]
        vals[k] = round(sum(xs) / len(xs), 1) if xs else None
    items = sorted(((k, v) for k, v in vals.items() if v is not None), key=lambda kv: -kv[1])
    lead_key, margin = (items[0][0], round(items[0][1] - items[1][1], 1)) if len(items) >= 2 else (None, None)
    md = lambda d: f"{int(d[5:7])}/{int(d[8:10])}"
    return {"source": "mean", "n": len(recent), "values": vals, "leader_key": lead_key, "margin": margin,
            "dates": f"{md(min(p['start'] or p['end'] for p in recent))} - {md(max(p['end'] for p in recent))}"}


# Averages are derived from the poll lists at build time, so adding a poll to the JSON is all an update needs.
for race in data["races"]:
    race["polls"].sort(key=lambda p: (p["end"], p["start"] or p["end"]))
    keys = [c["key"] for c in race["candidates"] if c.get("key")]
    race["average"] = mean_of_recent(race["polls"], keys) if race["polls"] else None
    rcv = race.get("rcv_first_round")
    if rcv:
        rcv["polls"].sort(key=lambda p: (p["end"], p["start"] or p["end"]))
        rcv["average"] = mean_of_recent(rcv["polls"], [c["key"] for c in rcv["candidates"]])
json.dump(data, open(ROOT / "data" / "senate-polls-2026.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)

# Inline the data; escape "</" so a stray "</script>" inside a string can never end the data block.
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
fragment = page.replace("__DATA__", blob)

# The fragment's markup must live in <body>; everything after </style> goes there.
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
