# SETUP

How to stand this system up for a candidate, what every context file is for, and the browser
tooling the applying half needs. Read this once end-to-end before your first real run.

---

## 1. Dependencies

**Discovery (required):**
- Python **3.9+**. That's it — `engine/` is standard-library only (no `pip install`). This is
  deliberate: a scheduled discovery run never breaks on a missing/updated ML dependency.

**Applying + cover letters (optional, only if you want automation):**
- A headless LLM agent CLI on `PATH` for `cover_letter.py` (this build used Anthropic's `claude`
  CLI with `CLAUDE_CODE_OAUTH_TOKEN`). Any equivalent works — you'd adapt `cover_letter.py`'s
  `generate()` call. Interactively, you don't need the script at all: an agent can draft the letter
  straight from `candidate-facts.md` + the JD.
- A **browser-automation tool** to fill ATS forms (see §5).

---

## 2. Configure the search — `engine/sweep.py`

Everything candidate/field-specific is in the banner-commented **CONTEXT / CRITERIA** block near
the top. Edit:

- **`QUERIES_CORE` / `QUERIES_ADJACENT`** — the role × sector search matrix fired at LinkedIn and
  Adzuna. `CORE` = obvious titles; `ADJACENT` = hidden-fit titles that don't carry a domain word
  but sit in the candidate's lane (this is what stops a real fit being missed on a title filter).
- **`ACCEPT_LOCATIONS` / `REJECT_LOCATIONS`** — the zero-tolerance location gate. A role is kept
  only if its location contains an ACCEPT token and no REJECT token (the anchor city always wins).
  Put every non-commutable place — domestic **and** overseas — in REJECT, so a "City (Remote)" tag
  on an office-based post is rejected rather than slipping through on the word "remote". The anchor
  city (`"london"` in the shipped example) is also hardcoded in `in_scope()` — change it there too.
- **Tier regexes** — `SENIOR` (block too-senior), `JUNIOR` (entry-level positive signal), `ENV`
  (domain word in title = core relevance), `SECTOR` (broad sector for tier-C review), `FUNC_BLOCK`
  (never-a-fit functions), `NOISE` (finance/medical/etc. false positives), `FIELD` (non-desk /
  fieldwork downgrade), `ECOLOGIST` (a specialist-discipline cap). Retune the words to your field.
- **`BIG_NAMES`** — preferred employers (a fit-tier signal, substring match).
- **`EMPLOYERS`** — curated ATS feeds `(platform, slug, display-name)` for firms whose whole mission
  is on-theme, so any junior role there is in scope. Platforms wired up: `pinpoint`, `ashby`,
  `greenhouse`, `workable`, `teamtailor`, `smartrecruiters`. Find a firm's slug from its careers-page
  URL and add a row.

Then tune the **fit scorer** — `engine/fitscore.py`:
- **`SKILLS`** (weight 3), **`ROLE`** (weight 2), **`GENERAL`** (weight 1): the candidate's weighted
  vocabulary. **`NEGATIVE`**: penalised phrases (multi-year experience, senior-as-role-descriptor,
  fieldwork, financial-trading). `backtest.py` holds labelled cases — run it after edits to make sure
  a known good role still scores high and a known bad one stays low.

---

## 3. The context / data files (`data/` + `cover-letter/`)

| File | Committed? | What it is |
|------|-----------|------------|
| `cover-letter/candidate-facts.md` | template | The candidate's REAL facts — the ONLY source `cover_letter.py` may use. Fill it in; keep it strictly true. |
| `data/application-brief.md` | **gitignored** (copy from `.example`) | The recurring ATS answers (name, email, phone, address, RTW, sponsorship, availability, salary, licence, "how did you hear"). The apply agent reads this so it never re-asks. |
| `data/credentials.md` | **gitignored** (copy from `.example`) | Per-portal account logins. Use a per-candidate password that does NOT contain their first name (several ATSs reject that). Prefer a password manager. |
| `data/applications-log.md` | template | One row per application. **The sweep reads this to detect "new-to-us" employers** (stops re-surfacing places you've engaged); `funnel.py` parses it into a funnel. Keep it current — it is the source of truth. |
| `data/funnel_overrides.json` | **gitignored** (copy from `.example`) | Live-thread statuses the log prose can't infer cleanly (e.g. an off-portal recruiter call). Merged over the parsed log. |
| `data/STATUS.md` | optional template | Human-readable running status; also scanned for already-engaged employers. |
| `data/.adzuna.json` | **gitignored** (copy from `.example`) | Free Adzuna API key — the single biggest coverage lever. Get one at developer.adzuna.com. Skipped gracefully if absent. |

**Golden rule for all candidate content: only ever record facts the candidate actually gave you.
Never fabricate experience, answers, or degree grades.** The cover-letter prompt and the apply
playbook both enforce this; keep it true at the source.

---

## 4. Scheduling the sweep

Run `engine/sweep.py` on a schedule — twice a day (start + end of the working day) catches
same-day postings. Any scheduler works:

```cron
# crontab -e   (paths absolute; adjust to your checkout)
0 8,18 * * *  cd /path/to/grad-job-hunt-engine/engine && /usr/bin/python3 sweep.py >> ../data/sweep.log 2>&1
```

(macOS launchd or a systemd timer are equally fine.) Modes: `sweep.py` (rank + digest + notify on
new), `--digest` (write digest, no notify), `--all` (print everything, no state change).

---

## 5. Browser tooling for applying (the coupled half)

The discovery half needs no browser. **Applying does**, and this is the part you will most likely
rebuild, because the original was welded to a specific tool:

- **What this system used: `chromeflow`** — a browser-guidance tool that drives the operator's own
  logged-in Chrome (so the candidate's real ATS/Gmail sessions are intact) and is itself steered by
  an LLM coding agent. `ATS-PLAYBOOK.md` is written against its verbs (`fill_input`, `type_text`,
  `set_file_input`, `click_element`, `click_at_coordinates`, `execute_script`, ...).
  Get it at **https://chromeflow.run** (or search "chromeflow" in the Chrome Web Store).
- **What you need to provide:** any equivalent that can (a) drive a real browser with the candidate's
  sessions, (b) upload files to file inputs (incl. inside shadow DOM / iframes), (c) do a *trusted*
  click for reCAPTCHA/anti-bot submit buttons, and (d) run arbitrary JS in the page. Playwright or
  Puppeteer with a persistent profile can do most of it; the reCAPTCHA/anti-bot cases are the hard
  part and sometimes just need the candidate to finish in their own browser.
- **Translating the playbook:** the recipes are tool-agnostic in spirit — "native value setter +
  dispatched input/change", "full pointer-event sequence for stubborn submits", "park the photo file
  input so the CV targets the real resume field", "some tenants fingerprint automation — hand the
  candidate the link". Map those to your tool's verbs.

**Identity rule:** whatever drives the browser, applications must go out as the **candidate**. If
the browser session is signed into someone else (e.g. the operator's LinkedIn), never use one-click
"Easy Apply" — it submits under the wrong identity. Apply through each employer's own ATS instead.

---

## 6. Notifications

`sweep.py` calls the executable in the `JOB_HUNT_NOTIFY` env var (default
`~/.local/bin/notify-cmd`) with the alert text as the final argument — wire it to whatever channel
you want (a Telegram bot, `ntfy`, email, Slack webhook). If the executable is absent it degrades to
printing, so nothing breaks. Point it at your own notifier or unset it for quiet local runs.
