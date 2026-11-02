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
import json, sys, glob, statistics, pathlib, re

SRC = sys.argv[1]
OUT = sys.argv[2]

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
    others = [k for k, p in keymap.items() if p not in ("R", "D")]
    if len(rs) != 1:
        return None
    if ds:
        d = max(ds, key=lambda k: values.get(k) or 0)
    elif others:
        d = max(others, key=lambda k: values.get(k) or 0)
    else:
        return None
    return d, rs[0]

races = []
for f in sorted(glob.glob(f"{SRC}/cycle_*.json")):
    for r in json.load(open(f)):
        av = r.get("rcp_average") or {}
        res = (r.get("result_wiki") or r.get("rcp_result") or {})
        if not av.get("values") or not res.get("values"):
            continue
        sd = side(av["values"], r["candidates"])
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
        pd_, pr = av["values"].get(d), av["values"].get(rr)
        rd, rres = rv(d), rv(rr)
        if None in (pd_, pr, rd, rres):
            continue
        pm = round(pd_ - pr, 1); rm = round(rd - rres, 1)
        last5 = []
        for p in r.get("last_five_polls") or []:
            vals = p.get("results") or {}
            a, b = vals.get(d), vals.get(rr)
            if a is not None and b is not None: last5.append(a - b)
        m5 = round(sum(last5) / len(last5), 1) if last5 else None
        races.append({
            "cycle": r["cycle"], "state": r["state"], "abbr": r["abbr"], "special": bool(r.get("special")), "runoff": bool(r.get("runoff")),
            "d": {"key": d, "name": next((c["name"] for c in r["candidates"] if d.lower() in c["name"].lower() or c["name"].split()[-1].lower() == d.lower()), d),
                  "party": next((c["party"] for c in r["candidates"] if d.lower() in c["name"].lower() or c["name"].split()[-1].lower() == d.lower()), "D")},
            "r": {"key": rr, "name": next((c["name"] for c in r["candidates"] if rr.lower() in c["name"].lower() or c["name"].split()[-1].lower() == rr.lower()), rr)},
            "poll": {"d": pd_, "r": pr, "margin": pm, "dates": av.get("dates"), "n_polls": r.get("n_polls_listed")},
            "last5_margin": m5,
            "result": {"d": rd, "r": rres, "margin": rm, "source": res.get("source")},
            "miss": round(rm - pm, 1),
            "miss5": round(rm - m5, 1) if m5 is not None else None,
            "abs_miss": round(abs(rm - pm), 1),
            "leader_correct": (pm > 0) == (rm > 0) if pm != 0 else None,
            "competitive": abs(pm) <= 10,
            "url": r.get("url"), "notes": r.get("notes"),
        })

def summarize(rs):
    if not rs: return None
    ms = [x["miss"] for x in rs]; am = [x["abs_miss"] for x in rs]
    m5 = [x["miss5"] for x in rs if x["miss5"] is not None]
    calls = [x["leader_correct"] for x in rs if x["leader_correct"] is not None]
    return {
        "n": len(rs), "bias": round(statistics.mean(ms), 1), "median": round(statistics.median(ms), 1),
        "mae": round(statistics.mean(am), 1), "rmse": round((sum(m * m for m in ms) / len(ms)) ** 0.5, 1),
        "share_d_overrated": round(sum(1 for m in ms if m < 0) / len(ms), 2),
        "leader_correct": round(sum(calls) / len(calls), 2) if calls else None, "n_calls": len(calls),
        "bias_last5": round(statistics.mean(m5), 1) if m5 else None, "mae_last5": round(statistics.mean(abs(x) for x in m5), 1) if m5 else None,
        "worst": sorted(rs, key=lambda x: -x["abs_miss"])[:5],
    }

cycles = {}
for c in sorted({x["cycle"] for x in races}):
    rs = [x for x in races if x["cycle"] == c and not x["runoff"]]
    cycles[str(c)] = {"all": summarize(rs), "competitive": summarize([x for x in rs if x["competitive"]]),
                      "runoffs": summarize([x for x in races if x["cycle"] == c and x["runoff"]])}
allrs = [x for x in races if not x["runoff"]]
cycles["all"] = {"all": summarize(allrs), "competitive": summarize([x for x in allrs if x["competitive"]])}

by_state = {}
for x in races:
    by_state.setdefault(x["abbr"], []).append(x)

json.dump({"races": races, "cycles": cycles, "by_state": {k: sorted(v, key=lambda x: (x["cycle"], x["runoff"])) for k, v in by_state.items()},
           "conventions": __doc__.strip()}, open(OUT, "w"), indent=1, ensure_ascii=False)
print("races scored:", len(races))
for c, s in cycles.items():
    a, k = s["all"], s["competitive"]
    if a: print(f"{c:>5}: n={a['n']:2} bias={a['bias']:+.1f} mae={a['mae']:.1f} calls={a['leader_correct']}  | competitive n={k['n'] if k else 0} bias={k['bias'] if k else '-'} mae={k['mae'] if k else '-'} calls={k['leader_correct'] if k else '-'}")
