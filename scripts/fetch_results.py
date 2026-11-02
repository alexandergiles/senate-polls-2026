#!/usr/bin/env python3
"""Pull the official election-night count for every 2026 Senate race and write data/results-live.json.

Runs on GitHub Actions every few minutes on election night (.github/workflows/live-results.yml); can also be
run by hand:  python3 scripts/fetch_results.py [--dry-run] [--only GA,OH-special]

Sources are configured per race in data/live-sources.json (one adapter + URL per race, all official state
election offices). Hand-entered numbers and race calls go in data/results-manual.json and win over the feed.

Output schema (data/results-live.json):
{
  "updated": ISO timestamp, "note": str,
  "races": { "<race id>": {
      "source": str, "source_url": str, "fetched": ISO, "stale": bool,
      "reporting": "1,912 of 2,708 precincts" | null, "reporting_pct": float | null,
      "status": "counting" | "called" | "no results", "called_for": "D" | "R" | "I" | null,
      "candidates": [ {"key": str, "name": str, "party": "D"|"R"|"I"|"L"|"O", "votes": int}, ... ] } }
}
"""
import csv, io, json, re, sys, time, datetime, pathlib, urllib.request, urllib.error, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = "senate-polls-2026 results fetcher (github.com/alexandergiles/senate-polls-2026)"
TIMEOUT = 25

PARTY_WORDS = {"dem": "D", "democrat": "D", "democratic": "D", "dfl": "D", "rep": "R", "republican": "R", "gop": "R",
               "ind": "I", "independent": "I", "npa": "I", "nonpartisan": "I", "non-partisan": "I", "lib": "L", "libertarian": "L",
               "grn": "O", "green": "O", "wi": "O", "write-in": "O", "con": "O", "constitution": "O", "oth": "O", "other": "O"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read()
    return data if binary else data.decode("utf-8-sig", errors="replace")


def party_code(s):
    """'Jon Ossoff (Dem)' / 'DEM' / 'Republican Party' -> 'D' / 'R' ..."""
    if not s:
        return None
    t = s.strip().lower()
    m = re.search(r"\(([^)]+)\)\s*$", t)
    if m:
        t = m.group(1)
    t = t.replace("party", "").strip(" .")
    for k, v in PARTY_WORDS.items():
        if t == k or t.startswith(k + " ") or t.endswith(" " + k):
            return v
    if t[:1] in ("d", "r") and len(t) <= 4:
        return t[0].upper()
    return None


def surname(name):
    n = re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()
    n = re.sub(r",?\s+(jr\.?|sr\.?|ii|iii|iv)$", "", n, flags=re.I)
    parts = n.replace(",", " ").split()
    return parts[-1].lower() if parts else ""


def match_candidates(race, found):
    """Map fetched rows [{name, party, votes}] onto the site's candidate keys for the race (by surname, then party)."""
    out = []
    site = race["candidates"]
    used = set()
    for row in found:
        sn = surname(row["name"])
        hit = None
        for c in site:
            if c["key"] and c["key"] not in used and (surname(c["name"]) == sn or sn in c["name"].lower().split()):
                hit = c; break
        if hit is None and row.get("party"):
            cands = [c for c in site if c["key"] and c["key"] not in used and c["party"] == row["party"]]
            if len(cands) == 1 and sn and sn not in ("write-in", "writein"):
                hit = cands[0]
        key = hit["key"] if hit else None
        party = row.get("party") or (hit["party"] if hit else "O")
        if hit:
            used.add(key)
        out.append({"key": key or surname(row["name"]).capitalize(), "name": hit["name"] if hit else row["name"], "party": party, "votes": int(row["votes"] or 0)})
    out.sort(key=lambda c: -c["votes"])
    return out


# ---------- adapters: each returns {"candidates": [{name, party, votes}], "reporting": str|None, "reporting_pct": float|None} ----------

def adapter_clarity(cfg):
    """Scytl/Clarity ENR. cfg.url = election base, e.g. https://results.enr.clarityelections.com/GA/123456/345678
    Reads current_ver.txt, then <ver>/json/en/summary.json (contests: A name, C candidates, V votes, P precincts?)."""
    base = cfg["url"].rstrip("/")
    ver = get(base + "/current_ver.txt").strip()
    summary = json.loads(get(f"{base}/{ver}/json/en/summary.json"))
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat"), re.I)
    contests = [c for c in summary if pat.search(c.get("A", "")) and not re.search(cfg.get("exclude", r"\b(District|Dist\.?)\b"), c.get("A", ""))]
    if not contests:
        raise RuntimeError("contest not found in summary.json: " + "; ".join(c.get("A", "") for c in summary[:40]))
    c = contests[0]
    cands = [{"name": n, "party": party_code(n), "votes": v} for n, v in zip(c.get("C", []), c.get("V", []))]
    rep, pct = None, None
    # precinct totals live in the contest ("P"/"PR") on newer versions, or in the election settings
    tot, rep_n = c.get("PT") or c.get("P"), c.get("PR") or c.get("R")
    if isinstance(tot, (int, float)) and isinstance(rep_n, (int, float)) and tot:
        rep, pct = f"{int(rep_n):,} of {int(tot):,} precincts", round(100.0 * rep_n / tot, 1)
    else:
        try:
            es = json.loads(get(f"{base}/{ver}/json/en/electionsettings.json"))
            s = es.get("settings", {}).get("electiondetails", es.get("electiondetails", {}))
            t, r = s.get("totalprecincts") or s.get("precinctsTotal"), s.get("precinctsreporting") or s.get("precinctsReported")
            if t:
                rep, pct = f"{int(r or 0):,} of {int(t):,} precincts", round(100.0 * (r or 0) / t, 1)
        except Exception:
            pass
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def walk(obj, path):
    """Follow a dotted path through dicts/lists: 'a.b.0.c' ; '*' flattens a list."""
    for part in [p for p in path.split(".") if p]:
        if part == "*":
            obj = [x for lst in (obj if isinstance(obj, list) else [obj]) for x in (lst if isinstance(lst, list) else [lst])]
        elif isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[part]
    return obj


def adapter_json(cfg):
    """Generic JSON. cfg: url, contests (path to list), contest_name (field), contest (regex), candidates (field),
    name, party, votes (fields on each candidate), reporting (optional path on the contest or root)."""
    doc = json.loads(get(cfg["url"]))
    contests = walk(doc, cfg.get("contests", ""))
    if isinstance(contests, dict):
        contests = list(contests.values())
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat"), re.I)
    sel = [c for c in contests if pat.search(str(walk(c, cfg.get("contest_name", "name")) or "")) and not re.search(cfg.get("exclude", r"\bDistrict\b"), str(walk(c, cfg.get("contest_name", "name")) or ""))]
    if not sel:
        raise RuntimeError("contest not found")
    c = sel[0]
    rows = walk(c, cfg.get("candidates", "candidates"))
    cands = []
    for r in rows:
        name = str(walk(r, cfg.get("name", "name")))
        party = party_code(str(walk(r, cfg["party"]))) if cfg.get("party") else party_code(name)
        votes = walk(r, cfg.get("votes", "votes"))
        cands.append({"name": name, "party": party, "votes": int(str(votes).replace(",", "") or 0)})
    rep = pct = None
    if cfg.get("reporting"):
        try:
            rep = str(walk(c, cfg["reporting"]))
        except Exception:
            try: rep = str(walk(doc, cfg["reporting"]))
            except Exception: rep = None
    if cfg.get("reporting_pct"):
        try: pct = float(str(walk(c, cfg["reporting_pct"])).rstrip("%"))
        except Exception:
            try: pct = float(str(walk(doc, cfg["reporting_pct"])).rstrip("%"))
            except Exception: pct = None
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def adapter_csv(cfg):
    """Delimited text. cfg: url, delimiter (default ','), zip_member (optional), contest_col, contest (regex),
    name_col, party_col (optional), votes_col, header (true/false; false -> cols by index), reporting_cols (optional [reported, total])."""
    raw = get(cfg["url"], binary=True)
    if cfg.get("zip_member"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            member = next(n for n in z.namelist() if re.search(cfg["zip_member"], n))
            raw = z.read(member)
    text = raw.decode(cfg.get("encoding", "utf-8-sig"), errors="replace")
    delim = cfg.get("delimiter", ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if cfg.get("header", True):
        hdr = [h.strip() for h in rows[0]]; rows = rows[1:]
        col = lambda name: hdr.index(name)
    else:
        col = lambda idx: int(idx)
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat"), re.I)
    sel = [r for r in rows if len(r) > col(cfg["contest_col"]) and pat.search(r[col(cfg["contest_col"])]) and not re.search(cfg.get("exclude", r"\bDistrict\b"), r[col(cfg["contest_col"])])]
    if not sel:
        raise RuntimeError("contest rows not found")
    agg = {}
    for r in sel:
        name = r[col(cfg["name_col"])].strip()
        party = party_code(r[col(cfg["party_col"])]) if cfg.get("party_col") is not None else party_code(name)
        votes = int(re.sub(r"[^\d]", "", r[col(cfg["votes_col"])]) or 0)
        k = (name, party)
        agg[k] = agg.get(k, 0) + votes          # county-level files sum to the state total
    cands = [{"name": n, "party": p, "votes": v} for (n, p), v in agg.items()]
    rep = pct = None
    if cfg.get("reporting_cols"):
        a, b = cfg["reporting_cols"]
        try:
            rr, tt = int(sel[0][col(a)]), int(sel[0][col(b)])
            rep, pct = f"{rr:,} of {tt:,} precincts", round(100.0 * rr / tt, 1) if tt else None
        except Exception:
            pass
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def adapter_manual(cfg):
    raise RuntimeError("manual-only race")


ADAPTERS = {"clarity": adapter_clarity, "json": adapter_json, "csv": adapter_csv, "manual": adapter_manual}


def main():
    dry = "--dry-run" in sys.argv
    only = None
    for a in sys.argv:
        if a.startswith("--only="):
            only = set(a.split("=", 1)[1].split(","))
    site = json.load(open(DATA / "senate-polls-2026.json", encoding="utf-8"))
    races = {r["id"]: r for r in site["races"]}
    sources = json.load(open(DATA / "live-sources.json", encoding="utf-8")) if (DATA / "live-sources.json").exists() else {}
    manual = json.load(open(DATA / "results-manual.json", encoding="utf-8")) if (DATA / "results-manual.json").exists() else {}
    prev = {}
    try:
        prev = json.load(open(DATA / "results-live.json", encoding="utf-8")).get("races", {}) or {}
    except Exception:
        pass
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).isoformat(timespec="seconds")
    out = {}
    for rid, race in races.items():
        if only and rid not in only:
            if rid in prev: out[rid] = prev[rid]
            continue
        cfg = sources.get(rid) or {}
        entry = None
        if cfg.get("adapter") in ADAPTERS and cfg.get("adapter") != "manual":
            try:
                got = ADAPTERS[cfg["adapter"]](cfg)
                cands = match_candidates(race, got["candidates"])
                entry = {"source": cfg.get("source", "State election office"), "source_url": cfg.get("source_url", cfg.get("url")), "fetched": now, "stale": False,
                         "reporting": got.get("reporting"), "reporting_pct": got.get("reporting_pct"), "status": "counting" if any(c["votes"] for c in cands) else "no results",
                         "called_for": None, "candidates": cands}
                log(rid, "ok", [(c["key"], c["votes"]) for c in cands], got.get("reporting"))
            except Exception as e:
                log(rid, "FAILED", type(e).__name__, str(e)[:160])
                if rid in prev:
                    entry = dict(prev[rid]); entry["stale"] = True
        elif rid in prev:
            entry = prev[rid]
        # hand-entered numbers and calls win
        m = manual.get(rid)
        if m:
            if entry is None:
                entry = {"source": m.get("source", "Entered by hand"), "source_url": m.get("source_url"), "fetched": m.get("as_of", now), "stale": False, "reporting": None, "reporting_pct": None, "status": "no results", "called_for": None, "candidates": []}
            if m.get("candidates"):
                entry["candidates"] = match_candidates(race, m["candidates"]); entry["source"] = m.get("source", entry["source"]); entry["fetched"] = m.get("as_of", now)
                entry["status"] = "counting" if any(c["votes"] for c in entry["candidates"]) else "no results"
            for k in ("reporting", "reporting_pct", "source_url"):
                if m.get(k) is not None: entry[k] = m[k]
            if m.get("called_for"):
                entry["called_for"] = m["called_for"]; entry["status"] = "called"
        if entry:
            out[rid] = entry
    doc = {"updated": now, "note": "Counts from state election offices, refreshed every few minutes on election night; calls entered by hand.", "races": out}
    if dry:
        print(json.dumps(doc, indent=1)[:4000]); return
    json.dump(doc, open(DATA / "results-live.json", "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    log("wrote results-live.json:", len(out), "races")


if __name__ == "__main__":
    main()
