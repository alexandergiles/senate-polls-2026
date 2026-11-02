#!/usr/bin/env python3
"""Score final Senate polling averages against results for 2018–2024 and write data/accuracy.json.

Input: hist/cycle_YYYY.json files produced from RealClearPolitics/RealClearPolling race pages
(final RCP average, last five polls, certified result from Wikipedia).

Conventions
- margin = (Democratic-side candidate) − (Republican candidate), in points. The Democratic side is the
  Democratic nominee, or the independent who was the main opposition to the Republican (Nebraska 2024,
  Utah 2022, Maine 2018 ...).
- miss = result margin − polled margin. Negative: the polls overrated the Democratic side.
  Positive: the polls overrated the Republican.
- competitive = polled margin within 10 points.
"""
import json, sys, glob, statistics, pathlib, re, datetime

SRC = sys.argv[1]
OUT = sys.argv[2]
ELECTION = {2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
RUNOFF = {2018: "2018-11-27", 2020: "2021-01-05", 2022: "2022-12-06"}
FRESH_DAYS = 21   # a race counts as "polled at the end" if its latest poll ended within three weeks of the vote

def side(values, candidates):
    """Return (d_key, r_key) for a race, or None when there is no D/I-vs-R pair."""
    keymap = {}
    for c in candidates:
        sur = c["name"].split()[-1]
        for k in values:
            if k.lower() == sur.lower() or k.lower() in c["name"].lower() or sur.lower() in k.lower():
                keymap[k] = c["party"]
    rs = [k for k, p in keymap.items() if p == "R"]
    ds = [k for k, p in keymap.items() if p == "D"]
    others = [k for k, p in keymap.items() if p == "I"]   # independents only; a Libertarian-vs-Republican race (Arkansas 2020) is not a D-vs-R contest
    if len(rs) != 1:
        return None
    pool = ds + others   # the Democratic side is whichever Democrat or independent the polls had highest (Maine 2024: King, not the Democrat)
    if not pool:
        return None
    d = max(pool, key=lambda k: values.get(k) or 0)
    return d, rs[0]

races = []
for f in sorted(glob.glob(f"{SRC}/cycle_*.json")):
    for r in json.load(open(f)):
        av = r.get("rcp_average") or {}
        res = (r.get("result_wiki") or r.get("rcp_result") or {})
        polls = [p for p in (r.get("last_five_polls") or []) if p.get("results")]
        if not res.get("values") or not (av.get("values") or polls):
            continue
        basis = av["values"] if av.get("values") else polls[0]["results"]
        sd = side(basis, r["candidates"])
        if not sd:
            continue
        d, rr = sd
        # result keys may differ in spelling; match by surname
        def rv(key):
            v = res["values"]
            if key in v: return v[key]
            for k in v:
                if k.lower().split()[-1] == key.lower().split()[-1]: return v[k]
            return None
        rd, rres = rv(d), rv(rr)
        if None in (rd, rres):
            continue
        rm = round(rd - rres, 1)
        # RCP's published average, where it published one
        pd_, pr = (av.get("values") or {}).get(d), (av.get("values") or {}).get(rr)
        rcp_m = round(pd_ - pr, 1) if None not in (pd_, pr) else None
        # the site's estimator: plain mean of the five most recent polls
        last5 = []; latest = None; dsum = rsum = 0
        for p in polls[:5]:
            vals = p["results"]; a, b = vals.get(d), vals.get(rr)
            if a is not None and b is not None:
                last5.append(a - b); dsum += a; rsum += b
                if p.get("end") and (latest is None or p["end"] > latest): latest = p["end"]
        if not last5:
            continue
        m5 = round(sum(last5) / len(last5), 1)
        pm = m5
        eday = RUNOFF[r["cycle"]] if r.get("runoff") else ELECTION[r["cycle"]]
        days = (datetime.date.fromisoformat(eday) - datetime.date.fromisoformat(latest)).days if latest else None
        races.append({
            "cycle": r["cycle"], "state": r["state"], "abbr": r["abbr"], "special": bool(r.get("special")), "runoff": bool(r.get("runoff")),
            "d": {"key": d, "name": next((c["name"] for c in r["candidates"] if d.lower() in c["name"].lower() or c["name"].split()[-1].lower() == d.lower()), d),
                  "party": next((c["party"] for c in r["candidates"] if d.lower() in c["name"].lower() or c["name"].split()[-1].lower() == d.lower()), "D")},
            "r": {"key": rr, "name": next((c["name"] for c in r["candidates"] if rr.lower() in c["name"].lower() or c["name"].split()[-1].lower() == rr.lower()), rr)},
            "poll": {"d": round(dsum / len(last5), 1), "r": round(rsum / len(last5), 1), "margin": pm, "n": len(last5), "latest": latest, "days_before": days,
                     "n_polls_listed": r.get("n_polls_listed")},
            "rcp": {"d": pd_, "r": pr, "margin": rcp_m, "dates": av.get("dates")} if rcp_m is not None else None,
            "result": {"d": rd, "r": rres, "margin": rm, "source": res.get("source")},
            "miss": round(rm - pm, 1),
            "miss_rcp": round(rm - rcp_m, 1) if rcp_m is not None else None,
            "abs_miss": round(abs(rm - pm), 1),
            "leader_correct": (pm > 0) == (rm > 0) if pm != 0 else None,
            "competitive": abs(pm) <= 10,
            "fresh": days is not None and days <= FRESH_DAYS,
            "url": r.get("url"), "notes": r.get("notes"),
        })

def summarize(rs):
    if not rs: return None
    ms = [x["miss"] for x in rs]; am = [x["abs_miss"] for x in rs]
    m5 = [x["miss_rcp"] for x in rs if x["miss_rcp"] is not None]
    calls = [x["leader_correct"] for x in rs if x["leader_correct"] is not None]
    return {
        "n": len(rs), "bias": round(statistics.mean(ms), 1), "median": round(statistics.median(ms), 1),
        "mae": round(statistics.mean(am), 1), "rmse": round((sum(m * m for m in ms) / len(ms)) ** 0.5, 1),
        "share_d_overrated": round(sum(1 for m in ms if m < 0) / len(ms), 2),
        "leader_correct": round(sum(calls) / len(calls), 2) if calls else None, "n_calls": len(calls),
        "bias_rcp": round(statistics.mean(m5), 1) if m5 else None, "mae_rcp": round(statistics.mean(abs(x) for x in m5), 1) if m5 else None, "n_rcp": len(m5),
        "worst": sorted(rs, key=lambda x: -x["abs_miss"])[:5],
    }

cycles = {}
for c in sorted({x["cycle"] for x in races}):
    rs = [x for x in races if x["cycle"] == c and not x["runoff"] and x["fresh"]]
    cycles[str(c)] = {"all": summarize(rs), "competitive": summarize([x for x in rs if x["competitive"]]),
                      "runoffs": summarize([x for x in races if x["cycle"] == c and x["runoff"]]),
                      "stale": [x["abbr"] for x in races if x["cycle"] == c and not x["runoff"] and not x["fresh"]]}
allrs = [x for x in races if not x["runoff"] and x["fresh"]]
cycles["all"] = {"all": summarize(allrs), "competitive": summarize([x for x in allrs if x["competitive"]])}

by_state = {}
for x in races:
    by_state.setdefault(x["abbr"], []).append(x)

json.dump({"races": races, "cycles": cycles, "by_state": {k: sorted(v, key=lambda x: (x["cycle"], x["runoff"])) for k, v in by_state.items()},
           "conventions": __doc__.strip(), "fresh_days": FRESH_DAYS}, open(OUT, "w"), indent=1, ensure_ascii=False)
print("races scored:", len(races))
for c, s in cycles.items():
    a, k = s["all"], s["competitive"]
    if a: print(f"{c:>5}: n={a['n']:2} bias={a['bias']:+.1f} mae={a['mae']:.1f} calls={a['leader_correct']} rcp(n={a['n_rcp']}) bias={a['bias_rcp']} | competitive n={k['n'] if k else 0} bias={k['bias'] if k else '-'} mae={k['mae'] if k else '-'} calls={k['leader_correct'] if k else '-'}  stale={s.get('stale')}")
