#!/usr/bin/env python3
"""Merge the extracted RealClearPolling race files and the 35-seat roster into
data/senate-polls-2026.json (the single file the site is built from)."""
import json, re, sys, datetime, collections

SRC = sys.argv[1]            # directory holding polls_a.json, polls_b.json, roster.json
OUT = sys.argv[2]            # output path
TODAY = "2026-10-29"

a = json.load(open(f"{SRC}/polls_a.json")); b = json.load(open(f"{SRC}/polls_b.json"))
roster = json.load(open(f"{SRC}/roster.json"))
polled = {r["race_id"]: r for r in a + b}

STATE_ABBR = {r["race_id"].split("-")[0]: r["state"] for r in roster}

def norm_avg(av):
    """Both batches wrote rcp_average differently; return {values:{key:num}, dates:'M/D - M/D'}."""
    if not av: return None
    if "results" in av:
        vals = av["results"]
        s, e = av.get("start"), av.get("end")
        dates = f"{s[5:].replace('-0','-').replace('-','/')} - {e[5:].replace('-0','-').replace('-','/')}" if s and e else None
    else:
        vals = {k: v for k, v in av.items() if k not in ("dates", "spread", "start", "end")}
        dates = av.get("dates")
    return {"values": vals, "dates": dates}

def mean_of_recent(polls, keys, n=5):
    """Fallback where RCP publishes no average: plain mean of the n most recent polls."""
    recent = sorted(polls, key=lambda p: p["end"])[-n:]
    vals = {}
    for k in keys:
        xs = [p["results"].get(k) for p in recent if p["results"].get(k) is not None]
        vals[k] = round(sum(xs) / len(xs), 1) if xs else None
    lk, m = leader(vals)
    def md(d): return f"{int(d[5:7])}/{int(d[8:10])}"
    return {"source": "mean", "dates": f"{md(min(p['start'] or p['end'] for p in recent))} - {md(max(p['end'] for p in recent))}",
            "values": vals, "leader_key": lk, "margin": m, "n": len(recent)}

# Polls listed by 270toWin that RealClearPolling does not carry (read 2026-10-29).
# 270toWin shows a single field date per poll; start is left null.
SUPPLEMENTAL = {
    "NE": [
        {"pollster": "Echelon Insights", "start": None, "end": "2026-10-27", "sample": 407, "sample_type": "LV", "moe": 6.3,
         "results": {"Ricketts": 47, "Osborn": 46}, "url": "https://www.270towin.com/2026-senate-polls/nebraska"},
        {"pollster": "National Public Affairs", "start": None, "end": "2026-10-20", "sample": 800, "sample_type": "LV", "moe": None,
         "results": {"Ricketts": 46, "Osborn": 43}, "url": "https://backend.natpublicaffairs.com/media/Nebraska_General_Election_Memo_100926.pdf"},
        {"pollster": "Tavern Research", "start": None, "end": "2026-05-11", "sample": 1165, "sample_type": "LV", "moe": 3.5,
         "results": {"Ricketts": 42, "Osborn": 47}, "url": "https://www.tavernresearch.com/research-and-writings/nebraska-senate-poll"},
    ],
}

# Polls on RealClearPolling's latest-polls listing (read 2026-10-29) that the race pages, served from a
# days-old cache, did not yet carry. Toplines are RCP's; fieldwork dates and samples from the pollsters' releases.
LISTING = collections.defaultdict(list)
try:
    for m in json.load(open(f"{SRC}/missing_meta.json")):
        res = next(x[3] for x in json.load(open(f"{SRC}/missing.json")) if x[1] == m["race"] and x[2] == m["pollster"])
        url = next(x[4] for x in json.load(open(f"{SRC}/missing.json")) if x[1] == m["race"] and x[2] == m["pollster"])
        LISTING[m["race"]].append({"pollster": m["pollster"], "start": m["start"], "end": m["end"], "sample": m["sample"],
                                   "sample_type": m["sample_type"], "moe": m["moe"], "results": res, "url": url})
except FileNotFoundError:
    print("note: missing_meta.json not present; listing polls not added", file=sys.stderr)

def key_for(name):
    return name.split()[-1]

def leader(values):
    items = [(k, v) for k, v in values.items() if v is not None]
    if len(items) < 2: return None, None
    items.sort(key=lambda kv: -kv[1])
    return items[0][0], round(items[0][1] - items[1][1], 1)

races = []
for ro in roster:
    rid = ro["race_id"]
    pr = polled.get(rid)
    cands = []
    # candidates: roster is authoritative for the slate; poll keys are matched by surname
    for c in ro["candidates"]:
        cands.append({"name": c["name"], "party": c["party"], "key": None,
                      "incumbent": (c["name"] == ro["incumbent"] and ro["incumbent_running"])})
    polls = []; avg = None; rcv = None; sources = list(ro.get("source_urls", []))
    if pr:
        pkeys = set()
        for p in pr["polls"]: pkeys |= set(p["results"].keys())
        # map poll keys to roster candidates (explicit overrides first)
        OVERRIDE = {"Daniel J. Sullivan Jr.": "J. Sullivan", "Dan Sullivan": "Sullivan"}
        for c in cands:
            if c["name"] in OVERRIDE:
                c["key"] = OVERRIDE[c["name"]] if OVERRIDE[c["name"]] in pkeys else None
                continue
            for k in pkeys:
                if k.split()[-1].lower() == c["name"].split()[-1].lower() or k.lower() in c["name"].lower():
                    c["key"] = k
        # SC: RCP key "Graham Nordone" vs roster "Darline Graham"
        for c in cands:
            if c["key"] is None:
                for k in pkeys:
                    if c["name"].split()[-1].lower() in k.lower(): c["key"] = k
        # display name: use the poll file's fuller name where it extends the roster name (SC)
        for pc in pr["candidates"]:
            for c in cands:
                if c["key"] and pc["name"].split()[-1].lower() == c["key"].split()[-1].lower() and len(pc["name"]) > len(c["name"]) and c["name"].split()[0] == pc["name"].split()[0]:
                    c["name"] = pc["name"]
        for p in sorted(pr["polls"], key=lambda p: (p["end"], p["start"])):
            polls.append({"pollster": p["pollster"], "start": p["start"], "end": p["end"],
                          "sample": p["sample"], "sample_type": p["sample_type"], "moe": p["moe"],
                          "results": p["results"], "url": p["url"], "via": "RCP"})
        for p in SUPPLEMENTAL.get(rid, []):
            polls.append(dict(p, via="270toWin"))
        for p in LISTING.get(rid, []):
            polls.append(dict(p, via="RCP"))
        polls.sort(key=lambda p: (p["end"], p["start"] or p["end"]))
        rcp_avg = norm_avg(pr.get("rcp_average"))  # as published on the (CDN-cached) race page when read; reference only
        if polls:
            avg = mean_of_recent(polls, sorted(pkeys))
        sources.append(pr["source_url"])
    if rid == "AK" and "AK-rcv" in polled:
        rc = polled["AK-rcv"]
        rcv_polls = [{"pollster": p["pollster"], "start": p["start"], "end": p["end"], "sample": p["sample"],
                      "sample_type": p["sample_type"], "moe": p["moe"], "results": p["results"], "url": p["url"]}
                     for p in sorted(rc["polls"], key=lambda p: (p["end"], p["start"]))]
        rcv = {"candidates": [{"name": c["name"], "party": c["party"], "key": key_for(c["name"]) if c["name"] != "Daniel J. Sullivan Jr." else "J. Sullivan"} for c in rc["candidates"]],
               "polls": rcv_polls, "source_url": rc["source_url"],
               "average": mean_of_recent(rcv_polls, ["Peltola", "Sullivan", "Heikes", "J. Sullivan"])}
        # Heikes / J. Sullivan key mapping
        for c in rcv["candidates"]:
            if c["name"] == "Daniel J. Sullivan Jr.": c["key"] = "J. Sullivan"
            if c["name"] == "Dan Sullivan": c["key"] = "Sullivan"
        sources.append(rc["source_url"])
    # party override: SD Bengs runs as an independent (roster/Ballotpedia); RCP's header labels him (D)
    races.append({
        "id": rid, "abbr": rid.split("-")[0], "state": ro["state"], "special": ro["special"],
        "seat_party": ro["seat_holder_party"], "incumbent": ro["incumbent"],
        "incumbent_running": ro["incumbent_running"], "open_seat": ro["open_seat"],
        "rating": ro["rating"], "notes": ro.get("notes"),
        "candidates": cands, "average": avg, "rcp_average_when_read": rcp_avg if pr else None, "polls": polls, "rcv_first_round": rcv,
        "sources": sorted(set(sources)),
    })

# sanity: every poll key maps to a candidate
for r in races:
    keys = {c["key"] for c in r["candidates"] if c["key"]}
    for p in r["polls"]:
        for k in p["results"]:
            assert k in keys, (r["id"], k, keys)
    if r["rcv_first_round"]:
        rk = {c["key"] for c in r["rcv_first_round"]["candidates"]}
        for p in r["rcv_first_round"]["polls"]:
            for k in p["results"]: assert k in rk, (k, rk)

out = {
    "generated": TODAY,
    "election_day": "2026-11-03",
    "senate": {"current": {"D": 47, "R": 53}, "seats_up": {"D": 13, "R": 22}, "not_up": {"D": 34, "R": 31},
               "note": "D counts include the two independents who caucus with Democrats."},
    "sources": {
        "polls": "RealClearPolling race pages (realclearpolling.com), read 2026-10-29",
        "roster": "270toWin 2026 Senate map and Ballotpedia, read 2026-10-29",
        "ratings": "Cook Political Report ratings as shown on 270toWin (reflecting Cook's Oct. 20, 2026 update)",
        "reference": "https://www.nytimes.com/interactive/polls/senate-polls-2026.html",
    },
    "races": races,
}
json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
n = sum(len(r["polls"]) for r in races) + sum(len(r["rcv_first_round"]["polls"]) for r in races if r["rcv_first_round"])
print("races", len(races), "polls", n)
for r in races:
    if r["average"]:
        print(f'  {r["id"]:11} {r["average"]["source"]:4} {r["average"]["leader_key"]:15} +{r["average"]["margin"]}  n={len(r["polls"])}  keys={[c["key"] for c in r["candidates"]]}')
