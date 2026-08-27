#!/usr/bin/env python3
"""
fitscore — a dependency-free semantic-lite fit scorer for the candidate's job search.
=============================================================================

Given a job's text (title + company + description if we have it), returns a
0-100 FIT SCORE against the candidate's profile, plus the matched skills (explainability)
and any seniority/field flags that dragged it down.

Why not real embeddings? The sweep is stdlib-only so the launchd cron never breaks
on a missing/broken ML dependency (no numpy/torch/sentence-transformers installed).
For keyword-heavy job descriptions a weighted-coverage model is transparent, fast,
deterministic, and easy to calibrate/back-test — and unlike a black-box embedding it
tells you WHICH of her skills matched. If we ever add a proper embedding model, it
can layer on top of (not replace) this.

Model: score = saturating function of the summed weights of DISTINCT profile terms the
job mentions (hard skills weighted highest, then role-type, then general), MINUS a
penalty for seniority / experience / heavy-field signals. Calibrated so a genuine
entry-level on-discipline role (dentsu GHG reporting) lands ~80+, a senior-mislabelled
one (Radley Yeldar) is pulled down, and an off-discipline/field one scores low.

    from fitscore import fit_score
    r = fit_score("Sustainability Reporting Analyst dentsu ... GHG Protocol ...")
    r["score"]   -> 0..100
    r["matched"] -> ["ghg", "sustainability reporting", "carbon", ...]
    r["flags"]   -> ["senior", "5+ years", ...]
"""
import re, math

# --- the candidate's weighted profile vocabulary -----------------------------------
# HARD SKILLS / DOMAIN (weight 3): strong positive fit signals from her CV + targets.
SKILLS = [
    "life cycle assessment", "lca", "carbon footprint", "carbon accounting",
    "greenhouse gas", "ghg", "scope 1", "scope 2", "scope 3", "emissions",
    "esg", "sustainability reporting", "sustainability report", "carbon",
    "net zero", "net-zero", "decarbon", "cdp", "secr", "sbti", "gri", "issb",
    "esrs", "tcfd", "ecovadis", "csrd", "double materiality",
    "environmental data", "data analysis", "data analyst", "data-driven",
    "excel", "power bi", "tableau", "python", "rstudio", "arcgis", "\\bgis\\b",
    "geospatial", "originpro", "iso 9001", "iso 14064", "iso 14001", "iso 14001",
    "environmental science", "environmental modelling", "sustainability",
    "climate", "biodiversity", "water quality", "air quality", "renewable",
    "circular economy", "life cycle", "reporting framework", "disclosure",
    "materiality", "stakeholder engagement", "supplier engagement", "modelling",
]
# ROLE TYPE (weight 2): the levels/titles she fits.
ROLE = [
    "analyst", "consultant", "co-ordinator", "coordinator", "advisor", "adviser",
    "associate", "graduate", "junior", "assistant", "entry level", "entry-level",
    "trainee", "officer",
]
# GENERAL (weight 1): softer supporting terms from her background.
GENERAL = [
    "research", "report", "analysis", "communicate", "stakeholder", "audit",
    "compliance", "framework", "dashboard", "validate", "quality assurance",
    "sustainable", "environment", "energy", "data", "spanish", "graduate scheme",
]
# NEGATIVE (penalty): CLEAR experience-requirement / heavy-field signals only.
# NOTE: bare "senior"/"manager"/"lead"/"director" are deliberately NOT here — they
# false-fire on entry-level JDs ("senior management team", "reporting to a Manager",
# "lead on projects"). Role-level seniority is already handled by the sweep's SENIOR
# title regex + the EXP_BODY body-check; fitscore only penalises unambiguous gates.
NEGATIVE = [
    # explicit multi-year experience requirements (3+; a 0-1 or 1-2 yr range is entry-acceptable
    # for the candidate's ~1yr placement, so those must NOT penalise)
    (r"\b[3-9]\s*\+?\s*years\b", 22), (r"\b\d+\s*-\s*([3-9]|[1-9]\d)\s*years\b", 20),
    (r"\b[3-9]\+\s*years\b", 22), (r"minimum of [3-9]\+?\s*years", 22),
    # senior-as-a-role-descriptor (precise: senior + a role noun), not incidental "senior X team"
    (r"senior\s+(consultant|analyst|associate|advisor|professional|specialist|"
     r"role|position|level|hire|candidate|appointment)", 18),
    (r"been in a senior", 22), (r"(experienced|seasoned)\s+(consultant|analyst|professional)", 16),
    # experience-depth language
    (r"bucketloads of experience", 22), (r"wealth of experience", 18),
    (r"line management responsibilit", 14), (r"demonstrable experience", 12),
    (r"proven track record", 12), (r"extensive experience", 14),
    (r"substantial experience", 14), (r"\bgravitas\b", 12),
    (r"\bchartered\b", 14), (r"\bcieem\b", 14), (r"\bciwem\b", 14),
    # heavy-field / non-desk signals
    (r"\bfieldwork\b", 16), (r"field-based", 16), (r"site-based", 16),
    (r"construction site", 16), (r"\boffshore\b", 14), (r"toolbox talk", 12),
    (r"ecological survey", 14), (r"regular travel to sites", 12),
    # finance / trading (energy-trading, market analytics, buy/sell-side) - NOT sustainability,
    # and the fit model otherwise over-rates them on "energy/analyst/data" (Shell trading desk,
    # Wood Mackenzie power-trading, Acadian buy-side). Note: carbon/emissions trading is on-lane
    # and NOT penalised (these patterns are specific to financial trading).
    (r"power\s+trading", 20), (r"energy\s+trading", 20), (r"trading\s+(desk|analytics|floor)", 20),
    (r"\btrader\b", 16), (r"buy-side", 18), (r"sell-side", 18), (r"investment\s+bank", 18),
    (r"trading\s+strateg", 16), (r"proprietary\s+trading", 18),
]

# Precompile: a term is a phrase (contains a space) -> substring on normalised text;
# else a word/regex -> already-\b-anchored or wrapped.
def _compile(term):
    if term.startswith("\\b") or "\\" in term:
        return re.compile(term, re.I)
    if " " in term or "-" in term:
        return re.compile(re.escape(term), re.I)
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)

_SKILLS = [(t, _compile(t)) for t in SKILLS]
_ROLE = [(t, _compile(t)) for t in ROLE]
_GENERAL = [(t, _compile(t)) for t in GENERAL]
_NEGATIVE = [(re.compile(p, re.I), w) for p, w in NEGATIVE]

# calibration: K controls how fast positive weight saturates toward 100.
_K = 22.0


def fit_score(text):
    """Return {score:int 0-100, matched:[terms], flags:[neg terms]}."""
    t = (text or "").lower()
    matched, pos = [], 0.0
    seen = set()

    def _scan(pairs, weight, label_cap=None):
        nonlocal pos
        hits = 0
        for term, rx in pairs:
            key = term.strip("\\b")
            if key in seen:
                continue
            if rx.search(t):
                seen.add(key)
                pos += weight
                matched.append(key)
                hits += 1
                if label_cap and hits >= label_cap:
                    break

    _scan(_SKILLS, 3.0)
    _scan(_ROLE, 2.0, label_cap=2)   # role-type saturates fast (one good title term is enough)
    _scan(_GENERAL, 1.0)

    # saturating positive base (diminishing returns) -> 0..100
    base = 100.0 * (1.0 - math.exp(-pos / _K))

    # penalties
    flags, penalty = [], 0.0
    for rx, w in _NEGATIVE:
        m = rx.search(t)
        if m:
            flags.append(m.group(0).strip())
            penalty += w
    score = max(0, min(100, round(base - penalty)))
    # de-dupe / tidy matched (keep most informative first: skills already added first)
    return {"score": int(score), "matched": matched[:12], "flags": flags[:6]}


if __name__ == "__main__":
    import sys
    print(fit_score(sys.stdin.read() if not sys.stdin.isatty() else " ".join(sys.argv[1:])))
