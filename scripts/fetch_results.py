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
               "ind": "I", "independent": "I", "npa": "I", "nonpartisan": "I", "non-partisan": "I", "pet": "I", "petition": "I", "una": "I", "unaffiliated": "I", "non": "I", "und": "I", "nop": "I", "lib": "L", "libertarian": "L",
               "grn": "O", "green": "O", "wi": "O", "write-in": "O", "con": "O", "constitution": "O", "oth": "O", "other": "O"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr)


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def opener_for(cfg):
    """urllib opener honoring per-source options: user_agent 'browser' (some state sites refuse non-browser agents)
    and tls_verify false (one state serves an incomplete certificate chain; public read-only data)."""
    import ssl
    handlers = []
    if cfg and cfg.get("tls_verify") is False:
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    op = urllib.request.build_opener(*handlers)
    ua = BROWSER_UA if (cfg or {}).get("user_agent") == "browser" else UA
    op.addheaders = [("User-Agent", ua), ("Accept", "*/*"), ("Cache-Control", "no-cache"), ("Accept-Language", "en-US,en;q=0.9")] + list(((cfg or {}).get("headers") or {}).items())
    return op


def get(url, binary=False, cfg=None):
    with opener_for(cfg).open(url, timeout=TIMEOUT) as r:
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
    if len(t) == 1 and t in "drli":
        return t.upper()
    if t[:1] in ("d", "r") and len(t) <= 4:
        return t[0].upper()
    return None


def surname(name):
    import unicodedata
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    n = re.sub(r",?\s+(jr\.?|sr\.?|ii|iii|iv)$", "", n, flags=re.I)
    if "," in n:                      # "Sullivan, Dan S." -> Sullivan
        n = n.split(",")[0]
    parts = n.replace(",", " ").split()
    return parts[-1].lower() if parts else ""


def match_candidates(race, found):
    """Map fetched rows [{name, party, votes}] onto the race's candidates (by surname, then by a unique party).
    Works for unpolled races too, whose candidates have no poll key: their key becomes the surname."""
    out = []
    site = race["candidates"]
    used = set()
    for row in found:
        sn = surname(row["name"])
        hit = None
        for c in site:
            if c["name"] not in used and sn and (surname(c["name"]) == sn or sn in [surname(w) for w in c["name"].split()]):
                hit = c; break
        if hit is None and row.get("party") and sn and sn not in ("write-in", "writein", "in"):
            cands = [c for c in site if c["name"] not in used and c["party"] == row["party"]]
            if len(cands) == 1:
                hit = cands[0]
        if hit:
            used.add(hit["name"])
        key = (hit.get("key") or hit["name"]) if hit else surname(row["name"]).capitalize()
        party = row.get("party") or (hit["party"] if hit else "O")
        out.append({"key": key, "name": hit["name"] if hit else row["name"], "party": party, "votes": int(row["votes"] or 0),
                    "matched": bool(hit), "side": (("R" if hit["party"] == "R" else "D") if hit else None)})
    out.sort(key=lambda c: -c["votes"])
    return out


def is_this_race(race, cands):
    """Guard against a feed serving another election (Montana's site still served the June primary): the result
    must name at least two of this race's candidates, the Republican and a Democrat or independent among them."""
    hit = [c for c in cands if c.get("matched")]
    if not any("matched" in c for c in cands):          # entries written before the guard existed: re-match by name
        hit = [c for c in match_candidates(race, [{"name": c["name"], "party": c.get("party"), "votes": c.get("votes", 0)} for c in cands]) if c["matched"]]
    sides = {c.get("side") or ("R" if c.get("party") == "R" else "D") for c in hit}
    return len(hit) >= 2 and sides == {"R", "D"}


# ---------- adapters: each returns {"candidates": [{name, party, votes}], "reporting": str|None, "reporting_pct": float|None} ----------

def clarity_base(cfg):
    """Election base URL. cfg.url as given; otherwise the state's elections.json (e.g. .../IA/elections.json) names the
    current election, picked by cfg.election_date (default Nov 3, 2026)."""
    if cfg.get("url"):
        return cfg["url"].rstrip("/")
    root = cfg["root"].rstrip("/")
    lst = json.loads(get(root + "/elections.json", cfg=cfg))
    want = cfg.get("election_date", "11/3/2026")
    for e in lst:
        if str(e.get("Date", "")).startswith(want) and not e.get("County"):
            return f"{root}/{e['EID']}"
    raise RuntimeError("no election dated " + want + " in " + root + "/elections.json: " + "; ".join(f"{e.get('EID')} {e.get('ElectionName')}" for e in lst[:5]))


def adapter_clarity(cfg):
    """Scytl/Clarity ENR. Reads current_ver.txt, then <ver>/json/en/summary.json. Two layouts exist: older sites give
    contest A, candidates C, votes V (parties P); newer ones give contest C, choices CH, parties P, votes V, with
    TP/PR = reporting units participating/reported."""
    base = clarity_base(cfg)
    ver = get(base + "/current_ver.txt", cfg=cfg).strip()
    if not ver:
        raise RuntimeError("no results version published yet at " + base)
    doc = json.loads(get(f"{base}/{ver}/json/en/summary.json", cfg=cfg))
    summary = doc.get("Contests") if isinstance(doc, dict) else doc
    pat = re.compile(cfg.get("contest", r"U\.?\s?S\.?\s*Senat|United States Senat"), re.I)
    def name_of(c):
        if "CH" in c: return str(c.get("C") or "")
        return str(c.get("A") or c.get("Name") or "")
    contests = [c for c in summary if pat.search(name_of(c)) and not re.search(cfg.get("exclude", r"\b(District|Dist\.?|State)\b"), name_of(c))]
    if not contests:
        raise RuntimeError("contest not found in summary.json: " + "; ".join(name_of(c) for c in summary[:40]))
    c = contests[0]
    names = c.get("CH") if "CH" in c else c.get("C", [])
    votes, parties = c.get("V", []), c.get("P") or []
    cands = []
    for i, (n, v) in enumerate(zip(names, votes)):
        party = party_code(parties[i]) if i < len(parties) and isinstance(parties[i], str) else None
        n2 = re.sub(r"^(REP|DEM|LIB|CON|GRN|IND|NON|WFP|UNA)\s+", "", str(n))   # "REP Darline Graham"
        cands.append({"name": n2, "party": party or party_code(str(n)), "votes": v})
    rep, pct = None, None
    tot, rep_n = c.get("TP"), c.get("PR")
    if isinstance(tot, (int, float)) and isinstance(rep_n, (int, float)) and tot:
        unit = "counties" if tot <= 120 else "precincts"
        rep, pct = f"{int(rep_n):,} of {int(tot):,} {unit} fully reported", round(100.0 * rep_n / tot, 1)
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def get_first(cfg):
    """cfg.url, or the first of cfg.url_candidates that answers 200 (election keys that could not be confirmed in advance)."""
    urls = [cfg["url"]] if cfg.get("url") else []
    if cfg.get("key_from") and cfg.get("url_template"):
        try:
            urls = [cfg["url_template"].replace("{key}", k) for k in election_keys(cfg)] + urls
        except Exception as e:
            cfg["_keys_error"] = str(e)[:200]
    urls += [u for u in cfg.get("url_candidates", []) if u not in urls]
    last = None
    for u in urls:
        try:
            return get(u, cfg=cfg)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (403, 404): raise
    raise last or RuntimeError("no url")


def resolve_url(cfg):
    """cfg.url, with {eid} filled from a page that links to the current election (cfg.eid_from: page url, cfg.eid_pattern)."""
    url = cfg["url"]
    if "{eid}" in url:
        page = get(cfg["eid_from"], cfg=cfg)
        ids = re.findall(cfg.get("eid_pattern", r"eid=(\d+)"), page)
        if not ids:
            raise RuntimeError("no election id on " + cfg["eid_from"])
        url = url.replace("{eid}", max(ids, key=int))
    return url


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
            if any(re.search(r"name|candidate|choice", k) for k in keys) and any(re.search(r"vote|total", k) for k in keys):
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
                vk = next((k for k in x if re.search(r"^(total_?votes|votes|voteCount|voteTotal|total)$", k, re.I)), None) or next((k for k in x if re.search(r"vote", k, re.I) and isinstance(x[k], (int, float, str)) and not re.search(r"percent|pct|for$", k, re.I)), None)
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
    raw = get_first(cfg)
    if cfg.get("base64"):
        import base64
        body = raw.strip()
        if body[:1] == '"':                      # a JSON string literal holding the base64 text
            body = json.loads(body)
        raw = base64.b64decode(body).decode("utf-8", errors="replace")
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
    if cfg.get("reporting_pair"):
        try:
            a, b = (walk(c, cfg["reporting_pair"][0]), walk(c, cfg["reporting_pair"][1]))
            if b:
                rep, pct = f"{int(a or 0):,} of {int(b):,} precincts", round(100.0 * int(a or 0) / int(b), 1)
        except Exception:
            pass
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
    raw = get(resolve_url(cfg), binary=True, cfg=cfg)
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
        if "reporting_col" not in cfg: cfg["reporting_col"] = find([r"precincts?.?report"])
        if cfg.get("contest_col") is None or cfg.get("name_col") is None or cfg.get("votes_col") is None:
            raise RuntimeError("could not detect columns in header " + str(hdr[:25]))
    else:
        col = lambda idx: int(idx)
    pat = re.compile(cfg.get("contest", r"U\.?S\.? Senat|United States Senat"), re.I)
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
    elif cfg.get("reporting_col") is not None:
        m = re.search(r"(\d+)\s*/\s*(\d+)", sel[0][col(cfg["reporting_col"])])   # "0/1322", sometimes wrapped as ="0/1322"
        if m:
            rr, tt = int(m.group(1)), int(m.group(2))
            rep, pct = f"{rr:,} of {tt:,} precincts", round(100.0 * rr / tt, 1) if tt else None
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def adapter_html(cfg):
    """Server-rendered results page. cfg: url, section (regex locating the contest; text from the first match to
    cfg.section_end regex or 60k chars is searched), row (regex with named groups name, votes and optionally party),
    reporting (optional regex with one group), require (regex the page must contain), parties (surname -> party)."""
    return parse_text_rows(get(resolve_url(cfg), cfg=cfg), cfg)


def adapter_pdf(cfg):
    """A results report published as PDF (Alaska's Dominion summary). Text is extracted with pypdf, then parsed
    like an html source: cfg.section / section_end / row / reporting / require."""
    from pypdf import PdfReader
    raw = get(resolve_url(cfg), binary=True, cfg=cfg)
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40])
    return parse_text_rows(text, cfg)


def parse_text_rows(html, cfg):
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
            mm = re.search(r"(\d[\d,]*)\s*(?:of|/)\s*(\d[\d,]*)", rep)
            if mm:
                a, b = int(mm.group(1).replace(",", "")), int(mm.group(2).replace(",", ""))
                if b: pct = round(100.0 * a / b, 1)
    return {"candidates": cands, "reporting": rep, "reporting_pct": pct}


def adapter_civix(cfg):
    """Texas's Civix IVIS feed: a JSON object whose values are base64-encoded JSON sections
    ({OffType, Races: [{N: race, Candidates: [{N: name, P: party, V: votes}]}]})."""
    import base64
    doc = json.loads(get(cfg["url"], cfg=cfg))
    pat = re.compile(cfg.get("contest", r"U\.?\s?S\.?\s*SENAT|UNITED\s+STATES\s+SENAT"), re.I)
    sections = []
    for k, v in doc.items():
        if isinstance(v, str) and len(v) > 50:
            try:
                sections.append(json.loads(base64.b64decode(v + "=" * (-len(v) % 4)).decode("utf-8", errors="replace")))
            except Exception:
                pass
    for sec in sections:
        for race in (sec.get("Races") or []) if isinstance(sec, dict) else []:
            name = str(race.get("N") or race.get("Name") or "")
            if pat.search(name) and not re.search(cfg.get("exclude", r"DISTRICT"), name):
                cands = []
                for c in race.get("Candidates") or []:
                    nm = re.sub(r"\s*\(I\)\s*$", "", str(c.get("N") or "")).strip()
                    cands.append({"name": nm.title() if nm.isupper() else nm, "party": party_code(str(c.get("P") or "")), "votes": int(c.get("V") or 0)})
                rep = None
                for k in ("PR", "PollingLocationsReporting", "PrecinctsReporting", "PLR"):
                    if k in race: rep = f"{k}: {race[k]}"
                return {"candidates": cands, "reporting": rep, "reporting_pct": None}
    seen = [str(r.get("N")) for sec in sections if isinstance(sec, dict) and re.search(r"FEDERAL|STATEWIDE", str(sec.get("OffType"))) for r in (sec.get("Races") or [])]
    raise RuntimeError("Senate race not found; federal/statewide races: " + "; ".join(seen)[:400])


POLIWAVE_URL = "https://www.poliwave.com/api/us/election-night?office=senate"
_poliwave = {}


def poliwave():
    """Poliwave's election-night API: per-state votes by candidate, AP race calls, and a reporting share where the
    state publishes one. Fetched once per run. Secondary source: AP's count where the state feed is unusable."""
    if not _poliwave:
        d = json.loads(get(POLIWAVE_URL, cfg={"user_agent": "browser"}))
        _poliwave["meta"] = d["meta"]["regions"]; _poliwave["latest"] = d["latest"]["regions"]; _poliwave["time"] = d["latest"].get("sourceTime")
    return _poliwave


def poliwave_race(abbr):
    pw = poliwave()
    m, r = pw["meta"].get(abbr), pw["latest"].get(abbr)
    if not m or not r or not r.get("votes"):
        return None
    byid = {c["id"]: c for c in m.get("candidates", [])}
    cands = [{"name": byid[i]["name"], "party": party_code(byid[i].get("party")), "votes": v} for i, v in (r.get("cands") or {}).items() if i in byid]
    if not cands:
        cands = [{"name": p, "party": party_code(p), "votes": v} for p, v in r["votes"].items()]
    call = (r.get("callControl") or {}).get("party") if r.get("called") else None
    call = {"DEM": "D", "REP": "R", "IND": "I"}.get(call) if call else None
    src = "Unofficial count, " + ("the Associated Press" if r.get("source") == "ap" else "civicAPI") + " via Poliwave"
    pct = float(r["counted"]) if r.get("reportingKnown") and r.get("counted") is not None else None
    return {"candidates": cands, "reporting": (f"{pct:.0f}% of precincts" if pct is not None else None), "reporting_pct": pct,
            "called_for": call, "source": src, "source_url": "https://www.poliwave.com/us/live/midterms-2026/senate", "updated": r.get("updated")}


def adapter_manual(cfg):
    raise RuntimeError("manual-only race")


ADAPTERS = {"clarity": adapter_clarity, "json": adapter_json, "csv": adapter_csv, "html": adapter_html, "pdf": adapter_pdf, "civix": adapter_civix, "manual": adapter_manual}


def probe(sources):
    """--probe: fetch each configured feed and record what came back (status, type, the first bytes), so the
    adapters can be fitted to layouts that could not be read before election night. Writes data/live-probe.json."""
    out = {}
    for rid, cfg in sources.items():
        if rid.startswith("_") or cfg.get("adapter") in (None, "manual"):
            continue
        rec = {"adapter": cfg["adapter"]}
        try:
            for pu in cfg.get("probe_urls", []):
                try:
                    with opener_for(cfg).open(pu, timeout=TIMEOUT) as pr:
                        txt = pr.read().decode("utf-8-sig", errors="replace"); pfinal, pstatus = pr.geturl(), pr.status
                    rec.setdefault("probe_pages", {})[pu] = {"final_url": pfinal, "status": pstatus, "head": txt[:1500], "tables": [t[:2500] for t in re.findall(r"<table.*?</table>", txt, re.I | re.S)[:6]], "eids": re.findall(r"eid=(\d+)", txt)[:10], "paths": sorted(set(re.findall(r"/[A-Z]{2}/\d{5,7}", txt)))[:10],
                                                                "hints": sorted(set(re.findall(r"[\w./-]*(?:cdn/results|export-|/api/|\.json)[\w./?=-]*", txt)))[:40]}
                    if cfg.get("grep_js"):
                        host = re.match(r"https?://[^/]+", pu).group(0)
                        bm = re.search(r"<base\s+href=[\"']([^\"']+)[\"']", txt, re.I)
                        basep = (bm.group(1) if bm else pu[len(host):].rsplit("/", 1)[0] + "/")
                        for js in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", txt)[:6]:
                            jsu = js if js.startswith("http") else host + (js if js.startswith("/") else basep + js)
                            try:
                                code = get(jsu, cfg=cfg)
                                rec["probe_pages"][pu].setdefault("js", {})[jsu] = sorted(set(re.findall(r"[\w./-]*(?:cdn/results|export-|/api/)[\w./?${}=-]{0,80}", code)))[:40]
                                ctx = []
                                for needle in cfg.get("grep_js_context", []):
                                    for mm in list(re.finditer(re.escape(needle), code))[:6]:
                                        ctx.append(code[max(0, mm.start() - 260):mm.end() + 260])
                                if ctx: rec["probe_pages"][pu].setdefault("js_context", {})[jsu] = ctx
                            except Exception as e:
                                rec["probe_pages"][pu].setdefault("js", {})[jsu] = "error " + str(e)[:80]
                except Exception as e:
                    rec.setdefault("probe_pages", {})[pu] = {"error": str(e)[:200]}
            url = resolve_url(cfg) if cfg.get("url") else None
            if cfg["adapter"] == "clarity":
                base = clarity_base(cfg); rec["base"] = base
                ver = get(base + "/current_ver.txt", cfg=cfg).strip(); rec["version"] = ver
                url = f"{base}/{ver}/json/en/summary.json"
            rec["url"] = url
            if cfg["adapter"] == "json" and cfg.get("url_candidates"):
                for u in [url] + cfg["url_candidates"]:
                    try:
                        with opener_for(cfg).open(u, timeout=TIMEOUT) as r:
                            r.read(1); rec.setdefault("candidates_tried", {})[u] = r.status; url = u; break
                    except Exception as e:
                        rec.setdefault("candidates_tried", {})[u] = str(e)[:80]
            with opener_for(cfg).open(url, timeout=TIMEOUT) as r:
                raw = r.read(); rec["status"] = r.status; rec["content_type"] = r.headers.get("Content-Type"); rec["final_url"] = r.geturl()
            rec["bytes"] = len(raw)
            if raw[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    rec["zip_members"] = z.namelist()[:10]
                    first = z.read(z.namelist()[0])
                    rec["head"] = first[:1500].decode("utf-8", errors="replace")
            else:
                text = raw.decode("utf-8-sig", errors="replace")
                rec["raw_head"] = text[:300]
                if cfg.get("base64"):
                    try:
                        import base64; body = text.strip(); body = json.loads(body) if body[:1] == '"' else body
                        text = base64.b64decode(body).decode("utf-8", errors="replace"); rec["base64_decoded"] = True
                    except Exception as e:
                        rec["base64_error"] = str(e)
                rec["head"] = text[:2500]
                if cfg["adapter"] == "html":
                    rec["tables"] = [t[:2500] for t in re.findall(r"<table.*?</table>", text, re.I | re.S)[:6]]
                    if cfg.get("section"):
                        m = re.search(cfg["section"], text, re.I | re.S)
                        rec["section_found"] = bool(m)
                        if m: rec["section_head"] = text[m.start():m.start() + 12000]
            if cfg.get("_root_seen"): rec["root_seen"] = cfg["_root_seen"]
            if cfg.get("_keys_seen") is not None: rec["keys_seen"] = cfg["_keys_seen"]
            if cfg.get("_keys_error"): rec["keys_error"] = cfg["_keys_error"]
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
                if not is_this_race(race, cands):
                    raise RuntimeError("feed does not carry this race's general-election candidates (another election?): " + ", ".join(c["name"] for c in cands[:5]))
                entry = {"source": cfg.get("source", "State election office"), "source_url": cfg.get("source_url", cfg.get("url")), "fetched": now, "stale": False,
                         "reporting": got.get("reporting"), "reporting_pct": got.get("reporting_pct"), "status": "counting" if any(c["votes"] for c in cands) else "no results",
                         "called_for": None, "candidates": cands}
                log(rid, "ok", [(c["key"], c["votes"]) for c in cands], got.get("reporting"))
            except Exception as e:
                log(rid, "FAILED", type(e).__name__, str(e)[:160])
                errors[rid] = type(e).__name__ + ": " + str(e)[:200]
                if rid in prev and is_this_race(race, prev[rid].get("candidates") or []):
                    entry = dict(prev[rid]); entry["stale"] = True
        elif rid in prev and (manual.get(rid) or is_this_race(race, prev[rid].get("candidates") or [])):
            entry = prev[rid]
        # no usable state feed: the AP/civicAPI count via Poliwave
        pw = None
        try:
            pw = poliwave_race(race["abbr"])
        except Exception as e:
            log(rid, "poliwave failed", str(e)[:120])
        pw_total = sum(c["votes"] for c in pw["candidates"]) if pw else 0
        have_total = sum(c["votes"] for c in (entry.get("candidates") or [])) if entry else 0
        if pw and (entry is None or entry.get("stale") or cfg.get("adapter") in (None, "manual") or pw_total > have_total * 1.1):
            cands = match_candidates(race, pw["candidates"])
            if is_this_race(race, cands) and any(c["votes"] for c in cands):
                entry = {"source": pw["source"], "source_url": pw["source_url"], "fetched": pw.get("updated") or now, "stale": False,
                         "reporting": pw["reporting"], "reporting_pct": pw["reporting_pct"], "status": "counting", "called_for": None, "candidates": cands}
                errors.pop(rid, None)
        if pw and pw.get("called_for") and entry is not None and not entry.get("called_for"):
            entry["called_for"] = pw["called_for"]; entry["status"] = "called"
            entry["source"] = entry["source"] + "; race call by the Associated Press (via Poliwave)" if "Associated Press" not in entry["source"] else entry["source"]
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
