# Runbook — running a job hunt for one candidate with an LLM agent

This is the operating guide for an AI coding agent (e.g. Claude Code) driving this system on a
candidate's behalf. **For installation, every context file, the browser tooling and scheduling,
read [SETUP.md](SETUP.md) first** — this file assumes it is already set up.

> New to the repo? Start at **[SETUP.md](SETUP.md)** — it explains `engine/sweep.py`'s config block,
> `fitscore.py`, the `data/` files (`application-brief.md`, `credentials.md`, `applications-log.md`,
> `funnel_overrides.json`, `.adzuna.json`), `cover-letter/candidate-facts.md`, the chromeflow /
> browser-automation layer for applying, cron scheduling, and notifications.

## What this system is

A discovery-first job hunt for **one candidate**. You (the agent) find fitting roles, judge them,
apply as the candidate where you can, track everything, and surface decisions to the operator. The
mechanics live in `engine/`; the candidate specifics live in the config block and `data/`.

## The loop

1. **Discover** — the scheduled `engine/sweep.py` writes a dated digest to `results/` and notifies
   on genuinely-new fitting roles. To pull on demand: `cd engine && python3 sweep.py --all`.
2. **Assess** — for each new role, **open the real posting and read it before deciding** (see rules).
   Judge fit against the candidate's actual profile, not just the title or the fit score.
3. **Apply** — as the candidate, through the employer's own ATS (recipes in `ATS-PLAYBOOK.md`).
   Generate a tailored letter with `cover-letter/cover_letter.py` when a cover field needs one.
4. **Track** — append every application to `data/applications-log.md`; keep live-thread statuses in
   `data/funnel_overrides.json`. Check the funnel with `cd engine && python3 funnel.py`.

## Hard rules (behaviour)

These are the lessons that make the difference between a useful hunt and a spammy one. Keep them.

1. **Open before deciding — whether applying OR skipping.** Title and fit score are heuristics, not
   verdicts. Never skip on a title/company assumption; read the posting and cite a hard gate found
   *in it* (wrong required qualification, multi-year experience, non-commutable, discipline
   mismatch). Genuine fits are often under-sold by their titles.
2. **Verify location on every listing.** Job-board location flags lie. Confirm the role is actually
   in the candidate's commutable area or genuinely remote before applying. Honour the zero-tolerance
   location rule the candidate set.
3. **Apply AS the candidate, never under another identity.** Go through each employer's own ATS. If
   the browser is logged into someone else and a role is one-click "Easy Apply" only, do NOT use it —
   leave that one for the candidate to submit themselves, and hand them the link.
4. **Never fabricate candidate facts.** Only use what the candidate actually gave you
   (`candidate-facts.md` / `application-brief.md`). Do not invent experience, form answers, or a
   degree grade. "Prefer not to say" is a valid answer to voluntary/diversity questions.
5. **Do not arrange or confirm meetings/interviews.** If a thread proposes a time, surface it to the
   operator and let the candidate confirm — scheduling is theirs.
6. **Sending is outward-facing — get the operator's model right.** Follow whatever the operator has
   set for sending from inboxes (e.g. only send outreach from an approved account, never from the
   candidate's own inbox without explicit per-instance approval). When in doubt, draft and ask.
7. **Confirm every submission actually landed** (success page or acknowledgement email), then log it.
   Never assume from a click.
8. **Quality over volume.** It is fine to apply to nothing on a given run. A precise handful beats a
   scattershot hundred, and protects the candidate's reputation.

## When the operator asks "status?"

Lead with what's new and actionable: new roles from the latest digest, movement on live threads
(interviews, replies, offers, rejections), and anything waiting on the candidate — not the plumbing.
Read `data/applications-log.md` + `python3 funnel.py`, and (if you have inbox access) scan for
replies, judging each email by content, not sender display-name.
