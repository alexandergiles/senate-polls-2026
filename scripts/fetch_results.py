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

def clarity_base(cfg):
    """Election base URL: cfg.url as given, or resolved from the state root (the root redirects / links to the current election)."""
    if cfg.get("url"):
        return cfg["url"].rstrip("/")
    root = cfg["root"]; st = cfg.get("state", "")
    req = urllib.request.Request(root, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        final, body = r.geturl(), r.read().decode("utf-8", errors="replace")
    for text in (final, body):
        m = re.search(r"/" + re.escape(st) + r"/(\d{5,7})(?:/|\b)", text)
        if m:
            host = re.match(r"https?://[^/]+", final or root).group(0)
            return f"{host}/{st}/{m.group(1)}"
    raise RuntimeError("could not find the current election id at " + root)


def adapter_clarity(cfg):
    """Scytl/Clarity ENR. cfg.url = election base, e.g. https://results.enr.clarityelections.com/GA/123456
    (the web id segment is optional). Reads current_ver.txt, then <ver>/json/en/summary.json. Older sites return a
    bare list of contests; newer ones {"Contests": [...]} — each contest: A name, C candidate names, P parties
    (sometimes), V votes; precinct counts vary by version, so they are read where present."""
    base = clarity_base(cfg)
    ver = get(base + "/current_ver.txt").strip()
    doc = json.loads(get(f"{base}/{ver}/json/en/summary.json"))
    summary = doc.get("Contests") if isinstance(doc, dict) else doc
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat|United States Senat"), re.I)
    name_of = lambda c: str(c.get("A") or c.get("Name") or "")
    contests = [c for c in summary if pat.search(name_of(c)) and not re.search(cfg.get("exclude", r"\b(District|Dist\.?)\b"), name_of(c))]
    if not contests:
        raise RuntimeError("contest not found in summary.json: " + "; ".join(name_of(c) for c in summary[:40]))
    c = contests[0]
    names, votes, parties = c.get("C", []), c.get("V", []), c.get("P") or []
    cands = [{"name": n, "party": (party_code(parties[i]) if i < len(parties) and isinstance(parties[i], str) else None) or party_code(n), "votes": v} for i, (n, v) in enumerate(zip(names, votes))]
    rep, pct = None, None
    # precinct totals: in the contest on some versions, in the election settings on others
    tot, rep_n = c.get("PT") or c.get("TP"), c.get("PR") or c.get("RP")
    if isinstance(c.get("P"), str) and "/" in c["P"]:
        rep_n, tot = [int(x) for x in c["P"].split("/")[:2]]
    if isinstance(tot, (int, float)) and isinstance(rep_n, (int, float)) and tot:
        rep, pct = f"{int(rep_n):,} of {int(tot):,} precincts", round(100.0 * rep_n / tot, 1)
    else:
        try:
            es = json.loads(get(f"{base}/{ver}/json/en/electionsettings.json"))
            s = es.get("settings", {}).get("electiondetails", es.get("electiondetails", es))
            t = s.get("totalprecincts") or s.get("precinctsTotal") or s.get("PrecinctsTotal")
            r = s.get("precinctsreporting") or s.get("precinctsReported") or s.get("PrecinctsReporting") or 0
            if t:
                rep, pct = f"{int(r):,} of {int(t):,} precincts", round(100.0 * int(r) / int(t), 1)
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


def auto_json(doc, pat, exclude):
    """Unknown layout: find the contest whose name matches, then the list of candidate-like objects inside it."""
    def strings(o):
        return [v for v in o.values() if isinstance(v, str)] if isinstance(o, dict) else []
    def cand_lists(o, depth=0):
        if depth > 6: return
        if isinstance(o, list) and o and all(isinstance(x, dict) for x in o):
            keys = set(k.lower() for x in o for k in x)
            if any(re.search(r"name|candidate|choice", k) for k in keys) and any(re.search(r"votes|total", k) for k in keys):
                yield o
        if isinstance(o, dict):
            for v in o.values(): yield from cand_lists(v, depth + 1)
        elif isinstance(o, list):
            for v in o: yield from cand_lists(v, depth + 1)
    def contests(o, depth=0):
        if depth > 8: return
        if isinstance(o, dict):
            if any(pat.search(v) and not re.search(exclude, v) for v in strings(o)):
                yield o
            for v in o.values(): yield from contests(v, depth + 1)
        elif isinstance(o, list):
            for v in o: yield from contests(v, depth + 1)
    for c in contests(doc):
        for lst in cand_lists(c):
            rows = []
            for x in lst:
                nk = next((k for k in x if re.search(r"candidate|choice|name", k, re.I) and isinstance(x[k], str)), None)
                vk = next((k for k in x if re.search(r"^(total_?votes|votes|voteTotal|total)$", k, re.I)), None) or next((k for k in x if re.search(r"votes", k, re.I) and isinstance(x[k], (int, float, str))), None)
                pk = next((k for k in x if re.search(r"party", k, re.I) and isinstance(x[k], str)), None)
                if nk and vk:
                    rows.append({"name": x[nk], "party": party_code(x[pk]) if pk else party_code(x[nk]), "votes": int(re.sub(r"[^\d]", "", str(x[vk])) or 0)})
            if rows:
                return rows, c
    raise RuntimeError("no contest/candidate structure found; top-level keys: " + ", ".join(list(doc.keys())[:20] if isinstance(doc, dict) else ["<list>"]))


def adapter_json(cfg):
    """Generic JSON. cfg: url, contests (path to list), contest_name (field), contest (regex), candidates (field),
    name, party, votes (fields on each candidate), reporting (optional path on the contest or root). Without
    'contests' the layout is discovered (auto_json). base64: true decodes a base64 body first."""
    raw = get(cfg["url"])
    if cfg.get("base64"):
        import base64
        raw = base64.b64decode(raw.strip()).decode("utf-8", errors="replace")
    doc = json.loads(raw)
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat|United States Senat"), re.I)
    if "contests" not in cfg:
        cands, c = auto_json(doc, pat, cfg.get("exclude", r"\bDistrict\b"))
        rep = None
        for k, v in (c.items() if isinstance(c, dict) else []):
            if re.search(r"precinct|report", k, re.I) and isinstance(v, (str, int, float)):
                rep = (rep + " · " if rep else "") + f"{k}: {v}"
        return {"candidates": cands, "reporting": rep, "reporting_pct": None}
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
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r]
    cfg = dict(cfg)
    if cfg.get("header", True):
        hdr = [h.strip() for h in rows[0]]; rows = rows[1:]
        def col(name):
            if isinstance(name, int): return name
            for i, h in enumerate(hdr):
                if h.lower() == str(name).lower(): return i
            raise RuntimeError(f"column {name!r} not in header {hdr[:20]}")
        # columns not given: detect them from the header
        def find(pats, avoid=r"$^"):
            for pt in pats:
                for i, h in enumerate(hdr):
                    if re.search(pt, h, re.I) and not re.search(avoid, h, re.I): return i
            return None
        if "contest_col" not in cfg: cfg["contest_col"] = find([r"contest", r"^race", r"office"], r"id$|type|group")
        if "name_col" not in cfg: cfg["name_col"] = find([r"candidate", r"choice", r"^name$"], r"party|id$|county|contest")
        if "party_col" not in cfg: cfg["party_col"] = find([r"party"], r"id$")
        if "votes_col" not in cfg: cfg["votes_col"] = find([r"^total ?votes$", r"^votes$", r"^total$", r"votes"], r"percent|pct|%|for office|registered")
        if "county_col" not in cfg: cfg["county_col"] = find([r"^county", r"parish", r"jurisdiction"], r"id$|code")
        if cfg.get("contest_col") is None or cfg.get("name_col") is None or cfg.get("votes_col") is None:
            raise RuntimeError("could not detect columns in header " + str(hdr[:25]))
    else:
        col = lambda idx: int(idx)
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat"), re.I)
    sel = [r for r in rows if len(r) > col(cfg["contest_col"]) and pat.search(r[col(cfg["contest_col"])]) and not re.search(cfg.get("exclude", r"\bDistrict\b"), r[col(cfg["contest_col"])])]
    if not sel:
        raise RuntimeError("contest rows not found; contests seen: " + "; ".join(sorted({r[col(cfg["contest_col"])] for r in rows if len(r) > col(cfg["contest_col"])})[:30]))
    # county-level files: use the statewide/total rows if the file has them, otherwise sum the counties
    if cfg.get("county_col") is not None:
        cc = col(cfg["county_col"])
        tot = [r for r in sel if len(r) > cc and (not r[cc].strip() or re.fullmatch(r"(total|statewide|state|all)", r[cc].strip(), re.I))]
        if tot:
            sel = tot
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


def adapter_html(cfg):
    """Server-rendered results page. cfg: url, section (regex locating the contest; text from the first match to
    cfg.section_end regex or 60k chars is searched), row (regex with named groups name, votes and optionally party),
    reporting (optional regex with one group)."""
    html = get(cfg["url"])
    if cfg.get("require") and not re.search(cfg["require"], html[:20000], re.I):
        raise RuntimeError("page does not carry the required text " + cfg["require"])
    text = html
    if cfg.get("section"):
        m = re.search(cfg["section"], html, re.I | re.S)
        if not m:
            raise RuntimeError("contest section not found")
        text = html[m.start():]
        if cfg.get("section_end"):
            e = re.search(cfg["section_end"], text[1:], re.I | re.S)
            if e:
                text = text[:e.start() + 1]
        else:
            text = text[:60000]
    cands = []
    for m in re.finditer(cfg["row"], text, re.I | re.S):
        g = m.groupdict()
        name = re.sub(r"<[^>]+>", "", g.get("name") or "").strip()
        if not name:
            continue
        party = party_code(re.sub(r"<[^>]+>", "", g.get("party") or "")) if g.get("party") else party_code(name)
        if not party and cfg.get("parties"):
            party = next((p for sn, p in cfg["parties"].items() if sn.lower() in name.lower()), None)
        votes = int(re.sub(r"[^\d]", "", g.get("votes") or "0") or 0)
        cands.append({"name": name, "party": party, "votes": votes})
    if not cands:
        raise RuntimeError("no candidate rows matched")
    rep = pct = None
    if cfg.get("reporting"):
        m = re.search(cfg["reporting"], text if cfg.get("reporting_in_section") else html, re.I | re.S)
        if m:
            rep = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def adapter_manual(cfg):
    raise RuntimeError("manual-only race")


ADAPTERS = {"clarity": adapter_clarity, "json": adapter_json, "csv": adapter_csv, "html": adapter_html, "manual": adapter_manual}


def probe(sources):
    """--probe: fetch each configured feed and record what came back (status, type, the first bytes), so the
    adapters can be fitted to layouts that could not be read before election night. Writes data/live-probe.json."""
    out = {}
    for rid, cfg in sources.items():
        if rid.startswith("_") or cfg.get("adapter") in (None, "manual"):
            continue
        rec = {"adapter": cfg["adapter"]}
        try:
            url = cfg.get("url")
            if cfg["adapter"] == "clarity":
                base = clarity_base(cfg); rec["base"] = base
                ver = get(base + "/current_ver.txt").strip(); rec["version"] = ver
                url = f"{base}/{ver}/json/en/summary.json"
            rec["url"] = url
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read(); rec["status"] = r.status; rec["content_type"] = r.headers.get("Content-Type"); rec["final_url"] = r.geturl()
            rec["bytes"] = len(raw)
            if raw[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    rec["zip_members"] = z.namelist()[:10]
                    first = z.read(z.namelist()[0])
                    rec["head"] = first[:1500].decode("utf-8", errors="replace")
            else:
                text = raw.decode("utf-8-sig", errors="replace")
                if cfg.get("base64"):
                    try:
                        import base64; text = base64.b64decode(text.strip()).decode("utf-8", errors="replace"); rec["base64_decoded"] = True
                    except Exception as e:
                        rec["base64_error"] = str(e)
                rec["head"] = text[:2500]
                if cfg["adapter"] == "html" and cfg.get("section"):
                    m = re.search(cfg["section"], text, re.I | re.S)
                    rec["section_found"] = bool(m)
                    if m: rec["section_head"] = text[m.start():m.start() + 3000]
            try:
                got = ADAPTERS[cfg["adapter"]](cfg); rec["parsed"] = got
            except Exception as e:
                rec["parse_error"] = type(e).__name__ + ": " + str(e)[:300]
        except Exception as e:
            rec["error"] = type(e).__name__ + ": " + str(e)[:300]
        out[rid] = rec
        log(rid, rec.get("status"), rec.get("error") or rec.get("parse_error") or "parsed")
    json.dump(out, open(DATA / "live-probe.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log("wrote live-probe.json")


def main():
    dry = "--dry-run" in sys.argv
    only = None
    for a in sys.argv:
        if a.startswith("--only="):
            only = set(a.split("=", 1)[1].split(","))
    site = json.load(open(DATA / "senate-polls-2026.json", encoding="utf-8"))
    races = {r["id"]: r for r in site["races"]}
    sources = json.load(open(DATA / "live-sources.json", encoding="utf-8")) if (DATA / "live-sources.json").exists() else {}
    if "--probe" in sys.argv:
        probe(sources); return
    manual = json.load(open(DATA / "results-manual.json", encoding="utf-8")) if (DATA / "results-manual.json").exists() else {}
    prev = {}
    try:
        prev = json.load(open(DATA / "results-live.json", encoding="utf-8")).get("races", {}) or {}
    except Exception:
        pass
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).isoformat(timespec="seconds")
    out = {}; errors = {}
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
                errors[rid] = type(e).__name__ + ": " + str(e)[:200]
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
    doc = {"updated": now, "note": "Counts from state election offices, refreshed every few minutes on election night; calls entered by hand.", "races": out, "errors": errors}
    if dry:
        print(json.dumps(doc, indent=1)[:4000]); return
    json.dump(doc, open(DATA / "results-live.json", "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    log("wrote results-live.json:", len(out), "races")


if __name__ == "__main__":
    main()
