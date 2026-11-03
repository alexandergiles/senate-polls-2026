#!/usr/bin/env python3
"""Append newly collected polls to data/senate-polls-2026.json.

    python3 scripts/add_polls.py new_polls_a.json [new_polls_b.json ...]

Each input is a JSON array of {race, pollster, start, end, sample, sample_type, moe, results, url, date_basis, notes}.
A poll is skipped as a duplicate when the race already has one with the same results whose end date is within
four days and whose pollster name shares a word, or the same pollster and identical fieldwork dates.
Candidate keys that are not in the race's candidate list are dropped from results (with a warning) so the
averages stay comparable. Run build.py afterwards.
"""
import json, re, sys, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "senate-polls-2026.json"
STOP = {"poll", "polls", "polling", "research", "college", "university", "group", "the", "of", "and", "news", "u", "inc", "strategies", "insights", "reports", "survey", "center", "co", "llc", "institute", "for", "public", "opinion"}


def toks(name):
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t and t not in STOP}


def d(s):
    return datetime.date.fromisoformat(s)


def main():
    data = json.load(open(DATA, encoding="utf-8"))
    races = {r["id"]: r for r in data["races"]}
    added, skipped, unknown = [], [], []
    for f in sys.argv[1:]:
        for p in json.load(open(f, encoding="utf-8")):
            race = races.get(p["race"])
            if not race:
                unknown.append(p["race"]); continue
            keys = {c["key"] for c in race["candidates"] if c.get("key")}
            results = {}
            for k, v in (p.get("results") or {}).items():
                kk = next((x for x in keys if x.lower() == k.lower() or x.lower().split()[-1] == k.lower().split()[-1]), None)
                if kk is None or v is None:
                    print(f"  {p['race']}: dropping key {k!r} (not a listed candidate)", file=sys.stderr); continue
                results[kk] = float(v) if not float(v).is_integer() else int(float(v))
            if len(results) < 2 or not p.get("end"):
                skipped.append((p["race"], p["pollster"], "incomplete")); continue
            end = p["end"]; start = p.get("start") or None
            dup = False
            for q in race["polls"]:
                same_res = all(q["results"].get(k) == v for k, v in results.items()) and len(q["results"]) == len(results)
                close = abs((d(q["end"]) - d(end)).days) <= 4
                shared = bool(toks(q["pollster"]) & toks(p["pollster"]))
                if (same_res and close and shared) or (shared and q.get("start") == start and q["end"] == end) or (same_res and q["end"] == end):
                    dup = True; break
            if dup:
                skipped.append((p["race"], p["pollster"], "duplicate")); continue
            entry = {"pollster": p["pollster"], "start": start, "end": end, "sample": p.get("sample"), "sample_type": p.get("sample_type"),
                     "moe": p.get("moe"), "results": results, "url": p.get("url"), "via": "270toWin/pollster releases, Nov 2 update"}
            if p.get("date_basis") == "release":
                entry["date_basis"] = "release"
            if p.get("notes"):
                entry["notes"] = p["notes"]
            race["polls"].append(entry); added.append((p["race"], p["pollster"], end, results))
    data["generated"] = datetime.date.today().isoformat()
    json.dump(data, open(DATA, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"added {len(added)}, skipped {len(skipped)}, unknown races {sorted(set(unknown))}")
    for a in added: print("  +", a)
    for s in skipped: print("  -", s)


if __name__ == "__main__":
    main()
