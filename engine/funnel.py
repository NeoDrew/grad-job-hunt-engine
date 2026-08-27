#!/usr/bin/env python3
"""
funnel.py — structured application-funnel tracker for the candidate's job search.
========================================================================

Turns the prose `applications-log.md` into a queryable funnel so we can MEASURE
conversion (applied -> acknowledged -> screening/assessment -> interview -> offer)
instead of eyeballing it, spot silent failures (applied long ago, no response), and
see which channels/ATSs actually convert. Observability is the thing that sinks the
open-source auto-appliers (they log to a CSV and can't tell a silent failure from a
rejection); this closes that gap.

Source of truth stays the human `applications-log.md`. This script parses it and
infers each application's furthest STAGE from the notes, then merges
`funnel_overrides.json` for live threads whose true status isn't in the log
(e.g. the Hoare Lea assessment centre, a live offer).

    python3 funnel.py            # print the funnel report
    python3 funnel.py --json     # emit the structured records as JSON
    python3 funnel.py --telegram # one-line summary suitable for a Telegram ping

Stages (ordered): applied < acknowledged < screening < assessment < interview < offer
                  (rejected / withdrawn are terminal, tracked separately)
"""
import json, os, re, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
LOG = os.path.join(DATA, "applications-log.md")
OVERRIDES = os.path.join(DATA, "funnel_overrides.json")

STAGES = ["applied", "acknowledged", "screening", "assessment", "interview", "offer"]
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}

# note-keyword -> stage (checked most-advanced first). Terminal states handled separately.
#
# CRITICAL: these must match a GENUINE PROGRESSION SIGNAL (the candidate was invited / scheduled /
# shortlisted / progressed / completed a step), NOT the mere appearance of a word. Application
# notes are full of descriptive text - "environmental-assessment consultancy", "3-interview
# process", "carbon-project assessment", "tech-assessment=Yes" - that bare `\bassessment\b` /
# `\binterview\b` patterns wrongly read as advancement, hugely inflating the funnel (2026-08-03:
# they claimed 8 advanced / 2 interviews when the truth was 2 / 1). So every pattern here is
# anchored to a verb of progression ("invited to", "progressed to", "shortlisted for",
# "completed the", "passed the") or an unambiguous stage token ("SJT", "assessment centre").
# When in doubt UNDER-count: genuinely-advanced live threads are added explicitly to
# funnel_overrides.json, so a conservative inference never hides a real advancement.
STAGE_RULES = [
    ("offer",      r"\boffer\b|contract of employment|job offer|verbal offer|accepted the (role|offer)"),
    ("interview",  r"assessment cent(re|er)|invited to (a |an |the )?(call|interview)|"
                   r"interview (scheduled|invit|booked|confirmed|arranged)|"
                   r"(first|second|final)[- ]stage interview|shortlisted for (a |an )?interview|"
                   r"progressed to (an? )?interview|\b(l1|l2|l3) interview\b"),
    ("assessment", r"\bsjt\b|situational judgement|progressed to (the )?assessment|assessment stage|"
                   r"invited to (the |a |an )?(assessment|online assessment|test|online test|"
                   r"psychometric|numerical test|values test)|"
                   r"completed (the |an )?(assessment|screening|online test|sjt)|"
                   r"passed (the )?screening|screening (call|interview)"),
    ("acknowledged", r"under review|in progress|acknowledg|application received|we have received|thank you for (your |applying)|received your application|status[: ]+(under|in)"),
]
TERMINAL_RULES = [
    ("rejected",  r"\brejected\b|unfortunately|not (to )?(progress|move forward)|decided to move forward with other|unsuccessful|regret to inform"),
    ("withdrawn", r"\bwithdrawn\b|withdrew|pulled the application"),
]


def parse_log():
    rows = []
    for line in open(LOG, encoding="utf-8"):
        if not re.match(r"\|\s*2026-\d\d-\d\d\s*\|", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells: ['', date, role, company, location, portal, notes..., '']
        if len(cells) < 7:
            continue
        date, role, company, location, portal = cells[1], cells[2], cells[3], cells[4], cells[5]
        notes = " ".join(cells[6:-1]) if cells[-1] == "" else " ".join(cells[6:])
        rows.append({"date": date, "role": role, "company": _clean_co(company),
                     "portal": portal, "ats": _ats(portal), "notes": notes})
    return rows


def _clean_co(c):
    return re.sub(r"\s*\(.*", "", c).strip()  # drop the "(descriptor)" tail


def _ats(portal):
    p = portal.lower()
    for name in ["workday", "smartrecruiters", "oracle", "successfactors", "teamtailor",
                 "ashby", "pinpoint", "workable", "greenhouse", "vincere", "salesforce",
                 "networx", "gravity", "phenom", "reed", "linkedin"]:
        if name in p:
            return name
    return "other"


def stage_of(notes):
    low = notes.lower()
    for st, pat in TERMINAL_RULES:
        if re.search(pat, low):
            return st
    for st, pat in STAGE_RULES:
        if re.search(pat, low):
            return st
    return "applied"


def enrich(rows):
    ov = {}
    if os.path.exists(OVERRIDES):
        for o in json.load(open(OVERRIDES)):
            ov[(o["company"].lower(), o.get("role", "").lower())] = o
    for r in rows:
        r["stage"] = stage_of(r["notes"])
        # apply override (matched by company, optionally role substring)
        for (co, ro), o in ov.items():
            if co in r["company"].lower() and (not ro or ro in r["role"].lower()):
                r["stage"] = o["stage"]
                r["override_note"] = o.get("note", "")
    return rows


def _days_ago(d):
    try:
        return (datetime.date(2026, 8, 1) - datetime.date(*map(int, d.split("-")))).days
    except Exception:
        return None


def report(rows):
    live = [r for r in rows if r["stage"] not in ("rejected", "withdrawn")]
    counts = {s: 0 for s in STAGES}
    terminal = {"rejected": 0, "withdrawn": 0}
    for r in rows:
        if r["stage"] in terminal:
            terminal[r["stage"]] += 1
        else:
            counts[r["stage"]] += 1
    n = len(rows)
    print(f"APPLICATION FUNNEL  ({n} applications logged)\n" + "=" * 52)
    # funnel = cumulative (reached this stage OR beyond)
    print("\nFunnel (reached stage or further):")
    for i, s in enumerate(STAGES):
        reached = sum(1 for r in rows if r["stage"] not in ("rejected", "withdrawn")
                      and STAGE_RANK[r["stage"]] >= i) + \
                  sum(1 for r in rows if r["stage"] in ("rejected", "withdrawn"))  # rejected still 'applied+'
        # simpler: reached stage = any live role at >= i, plus rejected count only for 'applied'
        cur = counts[s]
        bar = "#" * cur
        print(f"  {s:<13} {cur:>3}  {bar}")
    print(f"  {'rejected':<13} {terminal['rejected']:>3}")
    if terminal["withdrawn"]:
        print(f"  {'withdrawn':<13} {terminal['withdrawn']:>3}")

    advanced = [r for r in rows if STAGE_RANK.get(r["stage"], 0) >= STAGE_RANK["screening"]]
    interviews = [r for r in rows if STAGE_RANK.get(r["stage"], -1) >= STAGE_RANK["interview"]]
    responded = [r for r in rows if r["stage"] != "applied"]
    print(f"\nKey metrics:")
    print(f"  Live (not rejected/withdrawn):   {len(live)}")
    print(f"  Any response (past 'applied'):   {len(responded)}/{n}  ({100*len(responded)//n if n else 0}%)")
    print(f"  Advanced (screening or beyond):  {len(advanced)}")
    print(f"  Interviews / assessment centres: {len(interviews)}")

    if interviews:
        print(f"\nFurthest along:")
        for r in sorted(interviews, key=lambda x: -STAGE_RANK.get(x["stage"], 0)):
            print(f"  [{r['stage']:<9}] {r['role'][:40]} @ {r['company']}"
                  + (f"  ({r['override_note']})" if r.get("override_note") else ""))

    # silent failures: applied/acknowledged only, and old
    stale = [r for r in rows if r["stage"] in ("applied", "acknowledged")
             and (_days_ago(r["date"]) or 0) >= 10]
    if stale:
        print(f"\nGoing quiet (>=10 days, no advancement) - likely silent no:")
        for r in sorted(stale, key=lambda x: _days_ago(x["date"]) or 0, reverse=True):
            print(f"  {r['date']} ({_days_ago(r['date'])}d) {r['role'][:38]} @ {r['company']}")

    # by ATS/channel
    by_ats = {}
    for r in rows:
        by_ats.setdefault(r["ats"], []).append(r)
    print(f"\nBy ATS/channel (count · advanced):")
    for ats, rs in sorted(by_ats.items(), key=lambda kv: -len(kv[1])):
        adv = sum(1 for r in rs if STAGE_RANK.get(r["stage"], 0) >= STAGE_RANK["screening"])
        print(f"  {ats:<16} {len(rs):>3}  ({adv} advanced)")


def telegram_line(rows):
    live = [r for r in rows if r["stage"] not in ("rejected", "withdrawn")]
    iv = [r for r in rows if STAGE_RANK.get(r["stage"], -1) >= STAGE_RANK["interview"]]
    adv = [r for r in rows if STAGE_RANK.get(r["stage"], 0) >= STAGE_RANK["screening"]]
    rej = sum(1 for r in rows if r["stage"] == "rejected")
    return (f"the candidate funnel: {len(rows)} applied · {len(live)} live · {len(adv)} advanced · "
            f"{len(iv)} interview/AC · {rej} rejected. Furthest: "
            + "; ".join(f"{r['company']} ({r['stage']})" for r in
                        sorted(iv, key=lambda x: -STAGE_RANK.get(x['stage'], 0))[:3]))


def main():
    rows = enrich(parse_log())
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif "--telegram" in sys.argv:
        print(telegram_line(rows))
    else:
        report(rows)


if __name__ == "__main__":
    main()
