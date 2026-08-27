#!/usr/bin/env python3
"""
Back-test — the safety net that stops the sweep dropping genuine jobs.
=====================================================================

Runs each labelled real posting through the FULL live decision pipeline
(`sweep.classify` -> body-check downgrade -> `fitscore.fit_score`) and checks the
outcome against its known verdict. **False negatives are the hard failure**: a
`fit` case (a genuine entry-level, on-lane role we would want to see) that the
pipeline EXCLUDES, drops to Tier C, or scores below the strong bar. Missing a good
job is the one thing we cannot do, so a single false negative fails the run.

Cases live in `backtest_cases.json` - 50+ real roles, each:
  id        LinkedIn jobPosting id (description fetched + CACHED so it reproduces after expiry)
  title/company/location   as the sweep sees them (classify uses all three)
  verdict   "fit"  = genuine entry-level on-lane -> pipeline MUST keep it Tier A/B with fit >= STRONG
            "skip" = field/senior/off-discipline/etc -> should be excluded, Tier C, or low fit
  note      why

Usage:
  python3 backtest.py            # run (uses cached descriptions; fetches+caches any missing)
  python3 backtest.py --refetch  # refresh all cached descriptions from LinkedIn
  python3 backtest.py --verbose  # also print PASSing rows

Add a case with just id/title/company/location/verdict/note; the description auto-caches on
the next run. Grow this whenever a real dud (false positive) or a real miss (false negative)
shows up - the labelled set is the regression net.
"""
import json, os, re, sys, time, urllib.request, html
import sweep
import fitscore

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "backtest_cases.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
STRONG = 50   # Tier A/B needs at least this fit to count as a strong surface
RESCUE = 65   # ...but a fit at/above this counts as strong from ANY tier (rescues a genuine fit the
              # title-tiering under-rated, e.g. "Geospatial Analyst" with no env word in the title)


def is_strong(final, fit):
    return final != "EXCLUDED" and (fit >= RESCUE or (final in ("A", "B") and fit >= STRONG))


def fetch_body(jid):
    try:
        u = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
        p = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read().decode("utf-8", "ignore")
        m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', p, re.S)
        return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""
    except Exception:
        return ""


def evaluate(case):
    """Return (final_tier ('A'|'B'|'C'|'EXCLUDED'), fit_score, note-of-why)."""
    body = case.get("body") or ""
    job = {"title": case["title"], "company": case["company"], "location": case["location"],
           "querykind": "core", "query": "", "source": "test", "desc": "",
           "url": f"https://uk.linkedin.com/jobs/view/x-{case['id']}"}
    tier, reason = sweep.classify(job)
    if tier is None:
        final = "EXCLUDED"
    else:
        final = tier
        # apply the body-check downgrade exactly as enrich_strong_roles does
        if final in ("A", "B") and body and (sweep.FIELD_BODY.search(body) or sweep.EXP_BODY.search(body)):
            final, reason = "C", "body-check downgrade"
    fit = fitscore.fit_score(f"{case['title']} {case['company']} {body}")["score"]
    return final, fit, reason


def main():
    refetch = "--refetch" in sys.argv
    verbose = "--verbose" in sys.argv
    cases = json.load(open(CASES))

    fetched = 0
    for c in cases:
        if refetch or not c.get("body"):
            b = fetch_body(c["id"]); time.sleep(0.4)
            if b:
                c["body"] = b
                fetched += 1
    if fetched:
        json.dump(cases, open(CASES, "w"), indent=2, ensure_ascii=False)
        print(f"(cached {fetched} description(s))\n")

    fits = [c for c in cases if c["verdict"] == "fit"]
    skips = [c for c in cases if c["verdict"] == "skip"]
    false_neg, false_pos, no_body = [], [], []
    rows = []
    for c in cases:
        if not c.get("body"):
            no_body.append(c); continue
        final, fit, why = evaluate(c)
        surfaced_strong = is_strong(final, fit)
        if c["verdict"] == "fit":
            ok = surfaced_strong
            if not ok:
                false_neg.append((c, final, fit, why))
        else:  # skip
            ok = not surfaced_strong
            if not ok:
                false_pos.append((c, final, fit, why))
        rows.append((c, final, fit, ok))

    def line(c, final, fit, ok, tag):
        return f"  {tag} {c['verdict']:<4} {final:<9} fit{fit:<4} {c['title'][:44]:<46} @ {c['company'][:22]}"

    print("FALSE NEGATIVES (genuine fit dropped/low - MUST be empty):")
    if false_neg:
        for c, final, fit, why in false_neg:
            print(line(c, final, fit, False, "X") + f"   <- {why}")
    else:
        print("  none")
    print("\nFALSE POSITIVES (skip surfaced as strong):")
    if false_pos:
        for c, final, fit, why in false_pos:
            print(line(c, final, fit, False, "!"))
    else:
        print("  none")
    if verbose:
        print("\nAll cases:")
        for c, final, fit, ok in rows:
            print(line(c, final, fit, ok, "." if ok else "X"))

    n = len(cases) - len(no_body)
    print("\n" + "=" * 60)
    print(f"cases: {len(cases)} ({len(fits)} fit, {len(skips)} skip){'  [%d no-body]' % len(no_body) if no_body else ''}")
    print(f"FALSE NEGATIVES (fit wrongly dropped): {len(false_neg)}/{len(fits)}   <-- MUST be 0")
    print(f"false positives (skip surfaced strong): {len(false_pos)}/{len(skips)}")
    if no_body:
        print(f"no cached body (fetch failed / bad id): {', '.join(c['id'] for c in no_body)}")

    if false_neg:
        print("\nFAIL: a genuine fit was dropped. This is the failure mode we cannot ship - "
              "loosen the filter or fix the fit model (or re-check the label).")
        sys.exit(1)
    print("\nPASS: no genuine fit was dropped by the pipeline.")


if __name__ == "__main__":
    main()
