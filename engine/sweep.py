#!/usr/bin/env python3
"""
job sweep — one uniform, comprehensive job-discovery engine.
============================================================

One run pulls EVERY channel (ATS JSON feeds + broad LinkedIn guest-API + Adzuna),
filters against the candidate's criteria, ranks each role by CV<->JD fit, de-dupes
against everything already seen/applied, writes a dated digest, and (optionally)
sends a notification with only the genuinely NEW fitting roles. Designed to run
on a schedule (e.g. cron, twice a day at the start + end of the working day).

CONFIGURE IT FOR YOUR CANDIDATE
-------------------------------
Everything candidate/field-specific lives in the "CONTEXT / CRITERIA" block below
(clearly banner-commented) and in the sibling data files. To adapt this from the
shipped sustainability/graduate example to a different candidate or field, EDIT:
  * QUERIES_CORE / QUERIES_ADJACENT   — the role x sector search matrix
  * ACCEPT_LOCATIONS / REJECT_LOCATIONS — your candidate's commutable/remote rules
  * SENIOR / JUNIOR / ENV / FUNC_BLOCK / FIELD / SECTOR / NOISE — the tier regexes
  * BIG_NAMES / EMPLOYERS               — preferred names + curated ATS employer feeds
  * fitscore.py                         — the weighted skills vocabulary for scoring
See SETUP.md for how these fit together, plus the data files and browser tooling.

HOW IT AVOIDS THE COMMON FAILURE MODES
--------------------------------------
1. Latency  — runs on a schedule with a broad query matrix + a 2-week LinkedIn
   window, so a role posted between runs is not missed.
2. Keyword blind spot — does NOT require a domain word in the title; a role
   surfaced by a sector search but with an off-title is kept as tier C (REVIEW)
   rather than discarded (tiering, not a hard title filter).
3. Channel gaps — ATS feeds + broad LinkedIn + Adzuna (which aggregates Reed /
   Indeed / CV-Library / company sites; needs a free API key in
   data/.adzuna.json, skipped gracefully if absent).

MODES
-----
  python3 sweep.py            # normal: rank, write digest, NOTIFY on new roles
  python3 sweep.py --all      # print every current match, no notify, no state change
  python3 sweep.py --digest   # write the digest file but do NOT notify (quiet run)
"""
import json, os, re, subprocess, sys, urllib.request, urllib.parse, html, datetime
import fitscore  # local, stdlib-only: CV<->JD fit score (0-100) + matched skills

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                          # repo root
DATA = os.path.join(ROOT, "data")                     # candidate data + tracking files
SEEN_FILE = os.path.join(DATA, "seen.json")
LOG_FILE = os.path.join(DATA, "sweep.log")
RESULTS_DIR = os.path.join(ROOT, "results")
ADZUNA_CRED = os.path.join(DATA, ".adzuna.json")      # {"app_id": "...", "app_key": "..."}
EXTRA_SEEN = os.path.join(DATA, "extra-seen.json")    # optional: import a prior history (skipped if absent)
# Notifier: any executable taking the message as its final arg (see SETUP.md). Empty = print to stdout.
NOTIFY = os.environ.get("JOB_HUNT_NOTIFY", os.path.expanduser("~/.local/bin/notify-cmd"))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# ---------------------------------------------------------------------------
# 1. CONTEXT / CRITERIA
# ---------------------------------------------------------------------------

# Comprehensive LinkedIn / Adzuna query matrix: role-types x sector-terms.
# The first block is core env/sustainability; the second block is the
# "hidden-fit" adjacent titles that don't carry an env word but sit in the candidate's
# lane (this is the block that would have caught the Capgemini utilities role).
QUERIES_CORE = [
    "sustainability analyst", "sustainability consultant", "sustainability graduate",
    "sustainability coordinator", "sustainability data analyst", "sustainability reporting analyst",
    "ESG analyst", "ESG data analyst", "ESG consultant",
    "carbon analyst", "carbon consultant", "carbon footprint analyst",
    "environmental consultant", "environmental analyst", "environmental graduate",
    "environmental advisor", "environmental scientist graduate",
    "climate analyst", "climate risk analyst",
    "net zero analyst", "net zero consultant",
    "LCA consultant", "life cycle assessment", "biodiversity consultant", "ecology consultant",
    "air quality consultant", "water quality analyst",
    "renewable energy analyst", "responsible investment analyst",
    "sustainable finance analyst", "stewardship analyst",
    "graduate sustainability consultant",
]
QUERIES_ADJACENT = [
    "utilities consultant graduate", "data analyst sustainability", "data consultant energy",
    "graduate analyst energy", "engineering science consultant", "energy transition analyst",
    "decarbonisation analyst", "graduate data analyst environment",
    # Adjacent FUNCTION at a climate company (the operator 2026-08-12): the sustainability factor is the
    # employer, not the title. Catches data/research/lab/CS/ops roles at carbon/climate firms.
    "carbon data analyst", "climate data analyst", "environmental data analyst",
    "ESG research analyst", "climate research analyst", "carbon accounting analyst",
    "greenhouse gas analyst", "carbon removal analyst", "climate solutions analyst",
    "sustainability operations analyst", "carbon project analyst", "climate research associate",
    "environmental laboratory analyst", "carbon markets analyst", "MRV analyst",
]
ALL_QUERIES = [(q, "core") for q in QUERIES_CORE] + [(q, "adjacent") for q in QUERIES_ADJACENT]

# ====================  LOCATION RULES — EDIT FOR YOUR CANDIDATE  ====================
# Zero-tolerance location gate. A role is ACCEPTED only if its location string contains an
# ACCEPT token AND no REJECT token (except the anchor city, which always wins — see below).
# The shipped example encodes "London or genuinely commutable from the NW-London/Herts belt,
# or genuinely remote". Replace both lists with your candidate's commutable ring + remote, and
# put every non-commutable city (domestic AND overseas) in REJECT so a "City (Remote)" tag on a
# city-based post is rejected rather than slipping through on the word "remote".
# NOTE: the anchor city "london" is also hardcoded in in_scope() below as the accept-override —
# change it there too if your anchor city differs.
ACCEPT_LOCATIONS = [
    "london", "greater london", "united kingdom", "england", "remote",
    "bushey", "watford", "borehamwood", "elstree", "radlett", "st albans", "st. albans",
    "hemel hempstead", "harrow", "wembley", "uxbridge", "ruislip", "pinner", "stanmore",
    "edgware", "barnet", "enfield", "rickmansworth", "chorleywood", "amersham", "chesham",
    "hertfordshire", "potters bar", "hatfield", "welwyn", "hertford", "cheshunt",
    "slough", "hayes", "ealing", "hounslow", "brent", "staines", "egham",
    "croydon", "bromley", "kingston", "richmond", "twickenham", "sutton", "epsom",
    "luton", "stevenage", "reading", "maidenhead", "windsor", "woking", "guildford", "wokingham",
]
# Reject if any of these appear and the anchor city does not. Checked BEFORE the "remote" accept,
# so a "Madrid (Remote)" / "Bristol Hybrid" style tag is hard-rejected rather than slipping through
# on the word "remote". Covers non-commutable domestic cities AND every overseas office (curated ATS
# feeds list global locations, which is the usual way a far-away office leaks in).
REJECT_LOCATIONS = [
    # non-southern / non-commutable UK
    "scotland", "edinburgh", "glasgow", "aberdeen", "dundee", "manchester", "liverpool",
    "leeds", "sheffield", "york", "newcastle", "leicester", "nottingham", "birmingham",
    "coventry", "stoke", "wolverhampton", "preston", "bolton", "warrington", "belfast",
    "northern ireland", "wales", "cardiff", "swansea", "wrexham", "durham", "middlesbrough",
    "sunderland", "hull", "grimsby", "lincoln", "worcester", "shrewsbury", "telford",
    "isle of wight", "jersey", "guernsey", "cornwall", "plymouth", "exeter", "wigan",
    "castleford", "wakefield", "yorkshire", "huddersfield", "halifax", "rotherham",
    "doncaster", "barnsley", "kendal", "carlisle", "cumbria",
    # in-country but NOT commutable from the anchor area (hard no under zero tolerance)
    "bristol", "bath", "swindon", "brighton", "southampton", "portsmouth", "winchester",
    "cambridge", "oxford", "oxfordshire", "milton keynes", "aylesbury", "high wycombe",
    "basingstoke", "abingdon", "harwell", "colchester", "chelmsford", "canterbury",
    "maidstone", "dover", "folkestone", "crawley", "horsham", "gatwick", "bournemouth",
    "poole", "peterborough", "norwich", "ipswich", "southend", "dorking", "leatherhead",
    "farnham", "redhill", "reigate", "sevenoaks", "bedford",
    # international (curated ATS feeds list global offices — all hard-no)
    "madrid", "barcelona", "spain", "amsterdam", "rotterdam", "netherlands", "paris",
    "france", "berlin", "munich", "germany", "dublin", "ireland", "brussels", "belgium",
    "lisbon", "portugal", "milan", "italy", "zurich", "geneva", "switzerland", "stockholm",
    "copenhagen", "denmark", "oslo", "helsinki", "warsaw", "poland", "sofia", "bulgaria",
    "new york", "san francisco", "boston", "washington", "chicago", "austin", "seattle",
    "usa", "united states", "singapore", "tokyo", "japan", "sao paulo", "brazil", "toronto",
    "canada", "sydney", "melbourne", "australia", "dubai", "abu dhabi", "india", "bangalore",
    "bengaluru", "mumbai", "hong kong", "shanghai", "beijing", "nairobi", "cape town",
]

# Grad-level: block obvious seniority.
SENIOR = re.compile(r"\b(senior|principal|lead\b|manager|managing|director|head\s+of|"
                    r"chief|vp\b|vice\s+president|staff\b|expert|8\+|10\+|associate\s+director)\b", re.I)
# Junior-positive signal (used to keep unknown-title roles at pure-sustainability employers).
JUNIOR = re.compile(r"\b(junior|graduate|grad\b|analyst|associate|assistant|entry[- ]?level|"
                    r"placement|trainee|consultant|advisor|adviser|officer|co[- ]?ordinator|intern)\b", re.I)
# Env keyword in the title => "core relevance".
ENV = re.compile(r"(sustainab|\besg\b|carbon|climate|net.?zero|environment|ecolog|biodivers|"
                 r"renewable|nature|decarbon|energy\s+transition|responsible\s+invest|stewardship|"
                 r"green\s+financ|sustainable\s+financ|impact\s+invest|\blca\b|life\s+cycle|"
                 r"\bghg\b|emission|circular|natural\s+capital|climate\s+risk|air\s+quality|"
                 r"water\s+quality|waste|conservation)", re.I)
# HARD-block functions that are never the candidate (sales / HR / pure-dev / customer service / etc).
# NOTE: "engineer/engineering" is deliberately NOT hard-blocked — env/energy/data engineering
# consultant roles can fit; they are handled by tiering instead of a blanket reject.
FUNC_BLOCK = re.compile(r"\b(sales|account\s+executive|business\s+development|\bbdr\b|\bsdr\b|"
                        r"\btax\b|\bhr\b|human\s+resources|payroll|recruit|talent\s+acquisition|"
                        r"legal\s+counsel|solicitor|paralegal|marketing|brand\s+manager|copywriter|"
                        r"receptionist|bookkeep|customer\s+(support|service|experience|success|care)|"
                        r"\bcx\b|help\s*desk|nurse|surgeon|anaesthet|physician|registrar|"
                        r"software\s+(developer|engineer)|devops|full[- ]?stack|front[- ]?end|back[- ]?end|"
                        r"accounts?\s+(receivable|payable)|learning\s+(and|&)\s+development|\bl&d\b|"
                        r"office\s+manager|executive\s+assistant|personal\s+assistant|facilities|"
                        r"health[,&\s]+safety|\behs\b|\bhse\b|\bsheq\b|\baccountant\b|"
                        # defence/aerospace product-testing engineering (false-friend on "environmental")
                        r"trials\s+engineer|test\s+engineer|environmental\s+trials|trials\s+and\s+analysis)\b", re.I)
# Targeted noise that a fuzzy env-query surfaces (finance/banking, medical, recruitment).
NOISE = re.compile(r"\b(investment\s+bank|capital\s+markets|\bm&a\b|equity\s+research|"
                   r"commodit(y|ies)\s+trad|trading\s+analyst|junior\s+trading|liquidity\s+risk|"
                   r"treasury|psychiatr|paediatric|intensivist|care\s+assistant|"
                   r"recruitment\s+consultant|trainee\s+recruitment|maternity\s+care|"
                   r"ai\s+(data\s+)?train(er|ing)?|data\s+train(er|ing)|data\s+annotat|annotator|data\s+labell|translation\s+review|ai\s+translation|"
                   r"loss\s+adjust|\badjuster\b)\b", re.I)
# Field / site / non-desk engineering signals. the candidate wants DESK-BASED work, so a role whose
# title screams fieldwork, site engineering or building-services (MEP) design gets downgraded
# one tier and flagged, rather than sitting in Tier B looking like a desk fit.
FIELD = re.compile(r"\b(site\s+engineer|design\s+engineer|construction|geo-?environmental|"
                   r"\bmarine\b|offshore|drilling|borehole|surveyor|ecological\s+survey|field\s+ecolog|"
                   r"site-based|driving\s+licen|full\s+uk\s+licen|installation|commissioning|"
                   r"maintenance\s+engineer|building\s+services|\bmep\b|inspector|fitter|"
                   # product/hardware ENVIRONMENTAL TESTING (defence/aerospace) — a false-friend
                   # on "environmental"; not sustainability (MBDA "Environmental Trials Engineer").
                   r"trials\s+(and\s+analysis\s+)?engineer|test\s+engineer|environmental\s+trials|"
                   r"environmental\s+test(ing)?\s+engineer)\b", re.I)
# Specialist ECOLOGY roles (ecologist / ecological consultant) are a distinct field discipline
# — protected-species surveys, CIEEM membership — not the candidate's desk env-science lane. Cap at
# Tier C (review) so they don't sit in A/B looking like desk fits.
ECOLOGIST = re.compile(r"(\becologists?\b|ecological\s+consultant|ornitholog|entomolog|\bbotanist)", re.I)
# Broad SECTOR signal: the candidate's lane (env + the energy/utilities/water/power hints that a pure
# "sustainability" keyword misses — this is what keeps the Capgemini utilities role in Tier C
# while dropping generic finance/IB/NHS analyst noise).
SECTOR = re.compile(r"(sustainab|\besg\b|carbon|climate|net.?zero|environment|ecolog|biodivers|"
                    r"renewable|nature|decarbon|\benergy\b|utilit|\bwater\b|\bpower\b|\bgrid\b|"
                    r"waste|circular|natural\s+capital|air\s+quality|emission|clean\s+energy|"
                    r"\bwind\b|\bsolar\b|hydrogen|sustainable\s+financ|responsible\s+invest|"
                    r"stewardship|net\s+zero|geospatial)", re.I)

# --- BROADENED "adjacent function at a climate company" matching (the operator, 2026-08-12) ---
# For CURATED climate/sustainability employers (the EMPLOYERS list below), the company's whole
# mission is climate, so ANY entry-level function is in scope for the candidate, not just env-titled roles:
# data/analytics, research/ratings, customer success/implementation/onboarding, operations/project
# coordination, marketing/content/comms, and LAB/R&D/technician roles (which uniquely use her
# XRF/ICP-OES/Geochemist's-Workbench experience). We therefore apply a NARROWER function block
# (only truly-never-the candidate functions) and a WIDER junior signal at curated employers. This is
# permanent: the sustainability factor comes from the EMPLOYER, not the job-title regex.
CURATED_FUNC_BLOCK = re.compile(r"\b(sales\b|account\s+executive|business\s+development|\bbdr\b|\bsdr\b|"
                        r"\bhr\b|human\s+resources|payroll|recruit|talent\s+acquisition|"
                        r"legal\s+counsel|solicitor|paralegal|receptionist|bookkeep|"
                        r"nurse|surgeon|anaesthet|physician|"
                        r"software\s+(developer|engineer)|devops|full[- ]?stack|front[- ]?end|back[- ]?end|"
                        r"office\s+manager|executive\s+assistant|personal\s+assistant|facilities|"
                        # NO customer service / success / support / experience — HARD (the operator 2026-08-13),
                        # even at a climate-mission company (the candidate dislikes CX; declined Fuse Energy).
                        r"customer\s+(support|service|experience|success|care|operations)|\bcx\b|"
                        r"help\s*desk|learning\s+(and|&)\s+development|\bl&d\b|"
                        r"\baccountant\b)\b", re.I)
# Wider junior/entry signal for curated employers: adds the lab/R&D/ops/CS/implementation/research
# titles the desk-title JUNIOR regex misses (technician, researcher, operations, onboarding, etc.).
JUNIOR_CURATED = re.compile(r"\b(junior|graduate|grad\b|analyst|associate|assistant|entry[- ]?level|"
                    r"placement|trainee|consultant|advisor|adviser|officer|co[- ]?ordinator|coordinator|"
                    r"intern|technician|researcher|research\b|specialist|operations|implementation|"
                    r"programme|program|executive)\b", re.I)

# Big-name employer signals (for scoring the fit tier). Substring match on company.
BIG_NAMES = [
    "arup", "aecom", "wsp", "mott macdonald", "ramboll", "atkins", "jacobs", "stantec",
    "erm", "anthesis", "ricardo", "slr", "rsk", "tetra tech", "buro happold", "rps",
    "sweco", "deloitte", "pwc", "kpmg", "ernst", "capgemini", "accenture", "mckinsey",
    "bcg", "bain", "unilever", "tesco", "national grid", "centrica", "sse", "drax",
    "severn trent", "thames water", "anglian water", "united utilities", "octopus",
    "shell", "bp", "edf", "s>", "veolia", "suez", "biffa", "legal & general", "lgim",
    "schroders", "aviva", "m&g", "abrdn", "hsbc", "barclays", "sylvera", "watershed",
    "systemiq", "baringa", "south pole", "cbre", "jll", "savills", "bureau veritas",
    "intertek", "sgs", "dnv",
]

# ---------------------------------------------------------------------------
# 2. CHANNELS
# ---------------------------------------------------------------------------

# ATS-fed employers (folded in from the old monitor). Every one is a
# sustainability/ESG/climate firm, so titles need only pass the junior+location
# gate, not the env-keyword gate.
EMPLOYERS = [
    ("smartrecruiters", "LegalAndGeneral", "Legal & General / LGIM"),
    ("pinpoint", "anthesisgroup", "Anthesis"), ("pinpoint", "menzies", "Menzies (ESG)"),
    ("pinpoint", "alcumus", "Alcumus / Planet Mark"), ("pinpoint", "bezerocarbon", "BeZero Carbon"),
    ("ashby", "sylvera", "Sylvera"), ("ashby", "Watershed", "Watershed"),
    ("ashby", "sweep", "Sweep"), ("ashby", "isometric", "Isometric"),
    ("ashby", "altruistiq", "Altruistiq"), ("greenhouse", "systemiq", "Systemiq"),
    ("greenhouse", "baringa", "Baringa"), ("greenhouse", "climatex", "Climate X"),
    ("greenhouse", "kaluza", "Kaluza"), ("workable", "supercritical", "Supercritical"),
    ("workable", "normative", "Normative"), ("workable", "plana", "Plan A"),
    ("workable", "kita", "Kita"), ("workable", "abatable", "Abatable"),
    ("workable", "cervest", "Cervest"), ("workable", "novata", "Novata"),
    ("workable", "pledge", "Pledge"), ("teamtailor", "southpole", "South Pole"),
    # Broadened climate-employer set (the operator 2026-08-12): surface ANY junior function here.
    ("greenhouse", "clarityai", "Clarity AI"), ("greenhouse", "patch", "Patch"),
]


def fetch_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.load(r)


def _clean_html(*parts):
    """Join + strip HTML/entities from JD fields the ATS feeds ship, so the fit-scorer
    reads real description text instead of scoring a bare title. Empty parts are ignored;
    returns "" when nothing usable is present (leaves the row at its title-only fit)."""
    t = " ".join(str(p) for p in parts if p)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def from_pinpoint(slug, company):
    data = fetch_json(f"https://{slug}.pinpointhq.com/postings.json")
    rows = data.get("data", data if isinstance(data, list) else [])
    out = []
    for p in rows:
        loc = p.get("location") or {}
        lt = " ".join(str(loc.get(k, "")) for k in ("name", "city", "province")) if isinstance(loc, dict) else str(loc)
        desc = _clean_html(p.get("description"), p.get("key_responsibilities"), p.get("skills_knowledge_expertise"))
        out.append({"company": company, "title": p.get("title", "") or "", "location": lt.strip(),
                    "url": p.get("url", "") or "", "desc": desc})
    return out


def from_ashby(slug, company):
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false")
    return [{"company": company, "title": j.get("title", "") or "", "location": j.get("location", "") or "",
             "url": j.get("jobUrl", "") or "",
             "desc": _clean_html(j.get("descriptionPlain") or j.get("descriptionHtml"))} for j in data.get("jobs", [])]


def from_greenhouse(slug, company):
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        lt = loc.get("name", "") if isinstance(loc, dict) else str(loc)
        out.append({"company": company, "title": j.get("title", "") or "", "location": lt or "",
                    "url": j.get("absolute_url", "") or "", "desc": _clean_html(j.get("content"))})
    return out


def from_workable(slug, company):
    data = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    out = []
    for j in data.get("jobs", []):
        lt = " ".join(str(j.get(k, "")) for k in ("city", "state", "country") if j.get(k))
        out.append({"company": company, "title": j.get("title", "") or "", "location": lt.strip(),
                    "url": j.get("url") or j.get("application_url") or j.get("shortlink") or "",
                    "desc": _clean_html(j.get("description"), j.get("requirements"))})
    return out


def from_teamtailor(slug, company):
    data = fetch_json(f"https://{slug}.teamtailor.com/jobs.json")
    items = data.get("items", data.get("jobs", data if isinstance(data, list) else []))
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        lt = ""
        jp = j.get("_jobposting") or {}
        if isinstance(jp, dict):
            jl = jp.get("jobLocation") or {}
            if isinstance(jl, list):
                jl = jl[0] if jl else {}
            addr = (jl.get("address") if isinstance(jl, dict) else {}) or {}
            if isinstance(addr, dict):
                lt = addr.get("addressLocality", "") or addr.get("addressRegion", "") or addr.get("addressCountry", "") or ""
        jp = j.get("_jobposting") if isinstance(j.get("_jobposting"), dict) else {}
        out.append({"company": company, "title": j.get("title", "") or "", "location": lt,
                    "url": j.get("url") or j.get("careersite-job-url") or j.get("apply_url") or "",
                    "desc": _clean_html(j.get("body"), jp.get("description"))})
    return out


def from_smartrecruiters(slug, company):
    data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    out = []
    for p in data.get("content", []):
        title = p.get("name", "") or ""
        if not ENV.search(title):   # big multi-sector firm: require env keyword in title
            continue
        loc = p.get("location") or {}
        lt = loc.get("city", "") if isinstance(loc, dict) else str(loc)
        out.append({"company": company, "title": title, "location": lt or "",
                    "url": f"https://jobs.smartrecruiters.com/{slug}/{p.get('id','')}"})
    return out


ATS_PARSERS = {"pinpoint": from_pinpoint, "ashby": from_ashby, "greenhouse": from_greenhouse,
               "workable": from_workable, "teamtailor": from_teamtailor, "smartrecruiters": from_smartrecruiters}


def _linkedin_pull(kw, kind, location, extra, source, out, seen):
    q = {"keywords": kw, "location": location, "f_TPR": "r1209600", "start": 0}
    q.update(extra)
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urllib.parse.urlencode(q)
    try:
        page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return
    for card in page.split("data-entity-urn")[1:]:
        t = re.search(r'base-search-card__title">\s*(.*?)\s*</h3>', card, re.S)
        co = re.search(r'base-search-card__subtitle">\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>', card, re.S)
        loc = re.search(r'job-search-card__location">\s*(.*?)\s*</span>', card, re.S)
        link = re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"?]+)', card)
        dt = re.search(r'datetime="([0-9-]+)"', card)
        if not (t and co and link):
            continue
        u = link.group(1)
        if u in seen:
            continue
        seen.add(u)
        loc_txt = html.unescape(loc.group(1).strip()) if loc else ""
        # Do NOT fabricate "(Remote)": LinkedIn's f_WT=2 guest filter is loose and returns
        # hybrid/on-site roles in non-London cities (Aggreko=Glasgow hybrid, Bruntwood=
        # Manchester hybrid). Keep the REAL card location so in_scope() drops the non-London
        # hybrids and only genuinely-remote/"United Kingdom"/London cards survive.
        out.append({"company": html.unescape(re.sub(r"<[^>]+>", "", co.group(1)).strip()),
                    "title": html.unescape(re.sub(r"<[^>]+>", "", t.group(1)).strip()),
                    "location": loc_txt, "url": u, "posted": dt.group(1) if dt else "",
                    "query": kw, "querykind": kind, "source": source})


def from_linkedin():
    """LinkedIn guest-API pull: London (entry + internship) across the full matrix, PLUS a
    dedicated country-wide REMOTE pass over the core queries (f_WT=2) — remote widens the market
    beyond the anchor city (a genuinely-remote role is in scope regardless of office city)."""
    out, seen = [], set()
    for kw, kind in ALL_QUERIES:
        _linkedin_pull(kw, kind, "London, United Kingdom", {"f_E": "2"}, "LinkedIn", out, seen)
        _linkedin_pull(kw, kind, "London, United Kingdom", {"f_E": "1"}, "LinkedIn", out, seen)
    for kw, kind in [(q, k) for q, k in ALL_QUERIES if k == "core"]:
        _linkedin_pull(kw, kind, "United Kingdom", {"f_WT": "2", "f_E": "2"}, "LinkedIn-remote", out, seen)
    return out


def from_adzuna():
    """Adzuna aggregates Reed / Indeed / CV-Library / company sites. Needs a free key
    in .adzuna.json; skipped gracefully if absent."""
    try:
        cred = json.load(open(ADZUNA_CRED))
        app_id, app_key = cred["app_id"], cred["app_key"]
    except Exception:
        return None  # signal "not configured"
    out, seen = [], set()
    for kw, kind in ALL_QUERIES:
        params = urllib.parse.urlencode({
            "app_id": app_id, "app_key": app_key, "what": kw, "where": "london",
            "distance": "40", "results_per_page": "20", "max_days_old": "14",
            "sort_by": "date", "content-type": "application/json"})
        url = f"https://api.adzuna.com/v1/api/jobs/gb/search/1?{params}"
        try:
            data = fetch_json(url)
        except Exception:
            continue
        for j in data.get("results", []):
            u = j.get("redirect_url", "") or j.get("adref", "")
            if not u or u in seen:
                continue
            seen.add(u)
            loc = (j.get("location") or {}).get("display_name", "")
            out.append({"company": (j.get("company") or {}).get("display_name", "") or "?",
                        "title": j.get("title", "") or "", "location": loc, "url": u,
                        "posted": (j.get("created", "") or "")[:10], "query": kw, "querykind": kind,
                        "source": "Adzuna", "desc": j.get("description", "") or ""})
    return out


# ---------------------------------------------------------------------------
# 3. FILTER + SCORE
# ---------------------------------------------------------------------------

def in_scope(loc, title=""):
    L = (loc + " " + title).lower()
    # Zero tolerance: a named non-commutable/overseas location is a hard reject FIRST — even if the
    # tag also says "remote"/"hybrid" (kills the "Madrid (Remote)" leak). "london" = anchor city override.
    if any(tok in L for tok in REJECT_LOCATIONS) and "london" not in L:
        return False
    if "remote" in L:            # genuinely remote (no bad-city tag) is fine
        return True
    return any(tok in L for tok in ACCEPT_LOCATIONS)


def grad_level(title):
    return not SENIOR.search(title)


def classify(job, ats_curated=False):
    """Return (tier, reason) or (None, why-dropped).
    tier A = strong (env title + big name), B = good (env title), C = review
    (surfaced by an env/sector search but no env word in title — worth a human look)."""
    title, company, loc = job["title"], job["company"], job["location"]
    blob = f"{title} {company}"
    if not grad_level(title):
        return None, "senior"
    func_block = CURATED_FUNC_BLOCK if ats_curated else FUNC_BLOCK
    if func_block.search(title) or NOISE.search(title):
        return None, "off-function"
    if not in_scope(loc, title):
        return None, "location"
    env_title = bool(ENV.search(title))
    big = any(n in company.lower() for n in BIG_NAMES)
    london = "london" in (loc + " " + title).lower()
    if ats_curated:
        # curated climate employer: ANY entry-level function is in scope (data/lab/research/CS/ops/
        # marketing), not just env-titled roles — the mission makes it the candidate's lane.
        if not JUNIOR_CURATED.search(title):
            return None, "not-junior"
        base = ("B", "curated-climate-employer" + ("" if env_title else " · adjacent function"))
    elif env_title:
        tier = "A" if (big and london) else "B"
        base = (tier, "env-title" + ("+big-name" if big else ""))
    elif JUNIOR.search(title) and (SECTOR.search(title) or SECTOR.search(company)):
        # No env word in title. Keep as REVIEW only if it reads junior AND carries a real
        # SECTOR signal in title or company — the Capgemini-utilities catch, gated so fuzzy
        # finance/NHS/recruitment noise is dropped.
        base = ("C", f"surfaced-by:{job.get('query','?')} (sector-adjacent, title lacks env word — REVIEW)")
    else:
        return None, "no-env-relevance"
    # Field / site / non-desk downgrade (the candidate wants desk-based work).
    tier, reason = base
    if FIELD.search(title):
        tier = {"A": "B", "B": "C", "C": "C"}[tier]
        reason = "⚠ possible FIELD/site/eng work — verify · " + reason
    if ECOLOGIST.search(title):
        tier = "C"
        reason = "⚠ specialist ecology role (surveys/CIEEM — not the candidate's desk lane) · " + reason
    return tier, reason


def score(job, tier):
    s = {"A": 100, "B": 60, "C": 30}[tier]
    L = (job["location"] + " " + job["title"]).lower()
    if "london" in L:
        s += 15
    if any(n in job["company"].lower() for n in BIG_NAMES):
        s += 10
    if job.get("posted"):
        s += 5  # has a fresh date
    return s


# ---------------------------------------------------------------------------
# 4. STATE
# ---------------------------------------------------------------------------

def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass
    print(f"{ts} {msg}")


def load_seen():
    urls = set()
    for path in (SEEN_FILE, EXTRA_SEEN):   # optionally inherit a prior history
        try:
            urls |= set(json.load(open(path)))
        except Exception:
            pass
    return urls


# ---- "new to us" = never applied-to or assessed (vs seen.json = new since last run) ----
# Any candidate tracking files in data/ that mention an employer you have already engaged.
# Missing files are skipped, so list whatever you keep; applications-log.md is the main one.
KNOWN_FILES = ["applications-log.md", "STATUS.md"]
_CO_STRIP = re.compile(r"\b(ltd|limited|llc|llp|plc|group|inc|uk|ireland|the|international|"
                       r"global|asset\s+management|careers|recruit\w*|consulting|consultancy)\b")


def _norm_company(c):
    c = _CO_STRIP.sub(" ", c.lower())
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    return re.sub(r"\s+", " ", c).strip()


def load_known():
    text = ""
    for fn in KNOWN_FILES:
        p = os.path.join(DATA, fn)
        try:
            text += open(p, encoding="utf-8", errors="ignore").read().lower() + "\n"
        except Exception:
            pass
    # normalise the same way as _norm_company (collapse punctuation to spaces) so
    # "AVK-SEG" / "ASOS.com" in the log match the normalised company we test against.
    return re.sub(r"[^a-z0-9]+", " ", text)


def is_new_to_us(job, known_text):
    """True if we have never applied to / assessed this employer before."""
    comp = _norm_company(job["company"])
    if comp and len(comp) >= 3 and comp in known_text:
        return False
    for tok in comp.split():
        if len(tok) >= 6 and tok in known_text:
            return False
    return True


# Body-level disqualifiers (only knowable from the full posting, not the title). We fetch
# the description for the handful of NEW-TO-US strong roles each run and downgrade any that
# turn out to be field/site work or need real experience — this is what would have caught
# the J.S. Held (3-5yr, Canada), Adler & Allan (field/shift), Accenture (experienced
# consultant) and Stantec (experienced ecologist/surveys/CIEEM) roles automatically.
EXP_BODY = re.compile(r"(\b[3-9]\s*\+?\s*years|\b\d+\s*[-–]\s*([3-9]|[1-9]\d)\+?\s*years|\b[3-9]\+\s*years|"
                      r"proven\s+(track\s+record|experience)|extensive\s+experience|"
                      r"significant\s+experience|minimum\s+of\s+\d+\s*years|\bchartered\b|"
                      r"\bcieem\b|\bciwem\b|experienced\s+(consultant|ecologist|professional|analyst)|"
                      # soft-seniority language that gates a role despite an "Analyst"/"entry-level"
                      # title (the Radley Yeldar case, 2026-08-01):
                      r"senior\s+(position|role|level)|bucketloads?\s+of\s+experience|"
                      r"wealth\s+of\s+experience|line\s+management\s+responsibilit|"
                      r"demonstrable\s+experience|\bgravitas\b|\bseasoned\b|"
                      r"been\s+in\s+a\s+senior|deep\s+(expertise|experience)|"
                      r"substantial\s+experience|track\s+record\s+of)", re.I)
FIELD_BODY = re.compile(r"(field[- ]?based|fieldwork|site[- ]?based|driving\s+licen|"
                        r"full\s+clean\s+uk\s+licen|shift\s+(rota|pattern|work)|working\s+outdoors|"
                        r"ecological\s+survey|protected\s+species\s+survey|\bon-call\b|"
                        r"oil\s*&?\s*gas\s+sector|canadian\s+(oil|regulat)|site\s+investigation|"
                        r"ground\s+investigation|site\s+walkover|intrusive\s+investigation|"
                        r"trial\s+pit|watching\s+brief|\bboreholes?\b)", re.I)


def _linkedin_id(url):
    m = re.search(r"/jobs/view/[^/]*?-(\d{6,})(?:\?|$)", url) or re.search(r"(\d{8,})", url)
    return m.group(1) if m else None


def enrich_strong_roles(jobs, cap=80):
    """For ALL Tier A/B roles, fetch the JD (LinkedIn) or use the Adzuna desc to (a) compute an
    accurate CV<->JD fit score and (b) downgrade to Tier C any that are actually field/site work
    or need real experience. Sorted new-to-us-first upstream, so if the fetch cap is ever hit the
    genuinely-new roles get the full read first. Defensive: any fetch/parse failure leaves the
    role untouched (it keeps its title-only fit)."""
    fetched = 0
    for j in jobs:
        if j["tier"] not in ("A", "B"):
            continue
        body = ""
        if "linkedin.com" in j["url"]:                     # fetch the LinkedIn description
            if fetched >= cap:
                continue
            jid = _linkedin_id(j["url"])
            if not jid:
                continue
            try:
                u = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
                page = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15).read().decode("utf-8", "ignore")
                m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
                body = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""
                fetched += 1
            except Exception:
                continue
        elif j.get("desc"):                                # Adzuna ships a description snippet
            body = j["desc"]
        if not body:
            continue
        # re-score fit now that we have the full JD body (more accurate than title-only)
        fs = fitscore.fit_score(f"{j['title']} {j['company']} {body}")
        j["fit"], j["fit_matched"], j["fit_flags"] = fs["score"], fs["matched"], fs["flags"]
        flags = []
        if FIELD_BODY.search(body):
            flags.append("FIELD/site work")
        if EXP_BODY.search(body):
            flags.append("needs experience")
        if flags:
            j["tier"] = "C"
            j["score"] = min(j["score"], 55)
            j["reason"] = f"⚠ body-check: {', '.join(flags)} — likely NOT entry-level desk · " + j["reason"]
    return jobs


def save_seen(seen):
    json.dump(sorted(seen), open(SEEN_FILE, "w"), indent=0)


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def gather():
    ranked = []
    # ATS feeds (curated sustainability employers)
    for ats, slug, company in EMPLOYERS:
        try:
            rows = ATS_PARSERS[ats](slug, company)
            n = 0
            for r in rows:
                r.setdefault("source", "ATS")
                tier, reason = classify(r, ats_curated=True)
                if tier:
                    ranked.append({**r, "tier": tier, "reason": reason, "score": score(r, tier)})
                    n += 1
            log(f"ATS {company}: {len(rows)} live, {n} match")
        except Exception as e:
            log(f"ATS {company}: ERROR {e}")
    # LinkedIn
    try:
        li = from_linkedin()
        n = 0
        for r in li:
            tier, reason = classify(r)
            if tier:
                ranked.append({**r, "tier": tier, "reason": reason, "score": score(r, tier)})
                n += 1
        log(f"LinkedIn: {len(li)} pulled, {n} match")
    except Exception as e:
        log(f"LinkedIn: ERROR {e}")
    # Adzuna
    adz = from_adzuna()
    if adz is None:
        log("Adzuna: no key (.adzuna.json absent) — SKIPPED. Add a key to widen coverage.")
    else:
        n = 0
        for r in adz:
            tier, reason = classify(r)
            if tier:
                ranked.append({**r, "tier": tier, "reason": reason, "score": score(r, tier)})
                n += 1
        log(f"Adzuna: {len(adz)} pulled, {n} match")
    # de-dupe by URL, keep best score
    best = {}
    for j in ranked:
        u = j["url"]
        if not u:
            continue
        if u not in best or j["score"] > best[u]["score"]:
            best[u] = j
    # second pass: collapse the SAME role posted across sources/locations
    # (e.g. one role via LinkedIn London + Adzuna London + LinkedIn Reading) by
    # (normalised company, normalised title), keeping the highest-scoring copy.
    by_role = {}
    for j in best.values():
        key = (_norm_company(j["company"]), re.sub(r"[^a-z0-9 ]", " ", j["title"].lower()).strip())
        if key not in by_role or j["score"] > by_role[key]["score"]:
            by_role[key] = j
    # annotate "new to us"
    known = load_known()
    out = list(by_role.values())
    for j in out:
        j["new_to_us"] = is_new_to_us(j, known)
        # baseline fit score from whatever text we have (title + company + any Adzuna desc);
        # enrich_new_to_us() re-scores on the full fetched body where available.
        fs = fitscore.fit_score(f"{j['title']} {j['company']} {j.get('desc','')}")
        j["fit"], j["fit_matched"], j["fit_flags"] = fs["score"], fs["matched"], fs["flags"]
    # body-check the new-to-us strong roles (downgrades field/experienced ones). Sort FIRST
    # so the highest-scored roles are always within the cap and get checked every run.
    out.sort(key=lambda j: (-int(j.get("new_to_us", False)), -j["score"]))
    enrich_strong_roles(out)
    # final ranking: tier first, then fit score (so the best-matching role leads each tier)
    tier_rank = {"A": 0, "B": 1, "C": 2}
    return sorted(out, key=lambda j: (tier_rank.get(j["tier"], 3), -j.get("fit", 0), -j["score"]))


def write_digest(current, new):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    path = os.path.join(RESULTS_DIR, f"{day}.md")
    tiers = {"A": "A — strong (env role + big name, London)", "B": "B — good (env/junior role)",
             "C": "C — REVIEW (surfaced by search, title lacks env word)"}
    new_urls = {n['url'] for n in new}
    n_new_to_us = sum(1 for j in current if j.get("new_to_us"))
    lines = [f"# the candidate job sweep — {stamp}", "",
             f"{len(current)} current matches · **{len(new)} new since last run** · "
             f"**{n_new_to_us} never applied-to/assessed (🌟 new-to-us)**.  ",
             "🆕 = new posting this run · 🌟 = employer/role we've never engaged · "
             "**fit NN** = CV↔JD match 0-100 (≥65 = strong desk fit; <50 = usually senior/off-lane) · "
             "⚠ = possible field/site work, verify · Tier C = needs a human eyeball.", ""]
    for tk in ("A", "B", "C"):
        group = [j for j in current if j["tier"] == tk]
        if not group:
            continue
        lines.append(f"## Tier {tiers[tk]} ({len(group)})")
        lines.append("")
        for j in group:
            flag = "🆕 " if j["url"] in new_urls else ""
            flag += "🌟 " if j.get("new_to_us") else ""
            posted = f" · posted {j['posted']}" if j.get("posted") else ""
            src = j.get("source", "")
            fit = j.get("fit")
            fitstr = f" · **fit {fit}**" if fit is not None else ""
            lines.append(f"- {flag}**{j['title']}** — {j['company']} · {j['location']}{posted}{fitstr} · _{src}_  ")
            matched = ", ".join(j.get("fit_matched", [])[:6])
            skills = f" · skills: {matched}" if matched else ""
            lines.append(f"  {j['reason']}{skills} · [apply]({j['url']})")
        lines.append("")
    open(path, "w").write("\n".join(lines))
    return path


def main():
    show_all = "--all" in sys.argv
    quiet = "--digest" in sys.argv
    current = gather()
    log(f"TOTAL current matches: {len(current)} (A={sum(1 for j in current if j['tier']=='A')}, "
        f"B={sum(1 for j in current if j['tier']=='B')}, C={sum(1 for j in current if j['tier']=='C')})")

    if show_all:
        for j in current:
            print(f"  [{j['tier']}|{j['score']}] {j['company']}: {j['title']} | {j['location']} | {j['url']}")
        return

    seen = load_seen()
    new = [j for j in current if j["url"] not in seen]
    path = write_digest(current, new)
    log(f"digest -> {path}")

    if new and not quiet:
        # "strong" must be Tier A/B AND clear the fit bar — a low-fit A/B role (e.g. a defence
        # "environmental trials" engineer or a geoenv field role that slipped the tier) should
        # never LEAD the alert. Those fall into review instead.
        # strong = Tier A/B with fit>=50, OR any tier with fit>=65 (the fit score rescues a genuine
        # fit the title-tiering under-rated, e.g. a "Geospatial Analyst" whose title lacks an env word).
        FIT_STRONG, FIT_RESCUE = 50, 65
        strong = [j for j in new if j.get("fit", 0) >= FIT_RESCUE
                  or (j["tier"] in ("A", "B") and j.get("fit", 0) >= FIT_STRONG)]
        review = [j for j in new if j not in strong]
        fresh = sum(1 for j in new if j.get("new_to_us"))
        head = (f"the candidate job sweep: {len(new)} new posting(s) ({len(strong)} strong, "
                f"{len(review)} review) — {fresh} never applied-to/assessed 🌟.")
        body = [head]
        for j in sorted(strong, key=lambda x: (-int(x.get('new_to_us', False)), -x.get('fit', 0)))[:8]:
            star = "🌟" if j.get("new_to_us") else ""
            body.append(f"• [{j['tier']}·fit{j.get('fit','?')}]{star} {j['title']} — {j['company']} ({j['location']}) {j['url']}")
        if len(strong) > 8:
            body.append(f"  (+{len(strong)-8} more strong)")
        if review:
            body.append(f"REVIEW ({len(review)}): " + "; ".join(f"{j['title']} @ {j['company']}" for j in review[:5]))
        body.append(f"Full ranked digest: {path}")
        try:
            subprocess.run([NOTIFY, "--alert", "--tag", "[the candidate jobs]", "\n".join(body)], timeout=30)
            log(f"ALERTED {len(new)} new ({len(strong)} strong, {len(review)} review)")
        except Exception as e:
            log(f"NOTIFY ERROR {e}")

    for j in current:
        if j["url"]:
            seen.add(j["url"])
    save_seen(seen)
    if not new:
        log("no new roles this run")


if __name__ == "__main__":
    main()
