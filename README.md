# grad-job-hunt-engine

A discovery-first job-search system for running a focused hunt on one candidate's behalf.
It finds genuinely-fitting roles across every channel, ranks them by how well they match the
candidate's CV, tracks the application funnel, and (optionally) drafts tailored cover letters and
drives ATS forms. Built for a graduate sustainability search; **field-agnostic once you edit the
config** (see below).

It is deliberately split into two tiers, because they have very different portability:

| Tier | What it does | Portability |
|------|--------------|-------------|
| **Discovery** (`engine/sweep.py` + `fitscore.py`) | Pull ATS JSON feeds + LinkedIn guest-API + Adzuna, filter to the candidate's criteria, score fit, de-dupe, write a dated digest, notify on new roles. | **High.** Pure Python stdlib, public APIs, no browser. Runs day one. |
| **Applying** (`ATS-PLAYBOOK.md` + a browser tool) | Fill and submit each ATS as the candidate. | **Low.** Needs a browser-automation layer (this system used [chromeflow](https://chromeflow.run)) + an LLM agent. You will likely rebuild this half. |

So you get an excellent **finder** out of the box; the **auto-applier** is a documented recipe book you drive with your own tooling.

## Quick start

```bash
# 1. Discovery needs nothing but Python 3.9+ (stdlib only).
cd engine
python3 sweep.py --all          # print every current match for the shipped config
python3 funnel.py               # funnel report from data/applications-log.md

# 2. Make it yours (see SETUP.md for the full walkthrough):
#    - engine/sweep.py     → edit the "CONTEXT / CRITERIA" block (queries, locations, tier regexes, employers)
#    - engine/fitscore.py  → edit the weighted SKILLS/ROLE vocabulary for your field
#    - cover-letter/candidate-facts.md → the candidate's real facts
#    - data/*.example.md/json → copy to the un-suffixed name and fill in
#    - add a free Adzuna API key at data/.adzuna.json (biggest coverage lever)
```

## Layout

```
engine/          sweep.py (discovery), fitscore.py (CV↔JD score), funnel.py (tracker), backtest.py
cover-letter/    cover_letter.py + candidate-facts.md (grounding fact sheet)
data/            candidate data + tracking files (real files gitignored; *.example shipped)
results/         dated digest output (gitignored)
ATS-PLAYBOOK.md  per-ATS apply recipes (Workday, SmartRecruiters, iCIMS, SuccessFactors, ...)
CLAUDE.md        runbook for driving this with an LLM agent → points to SETUP.md
SETUP.md         full setup: every context file, chromeflow/browser tooling, scheduling, notifications
```

## Important

- **This is a template.** Nothing in it is anyone's real data. The moment you add a real
  candidate's CV/contacts/logins, keep your repo **private** — it is then PII.
- `data/credentials.md`, `application-brief.md`, `.adzuna.json` and runtime state are **gitignored**.
- Read **[SETUP.md](SETUP.md)** before running the applying half.
