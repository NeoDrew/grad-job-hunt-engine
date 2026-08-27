# Application tracker

Every application submitted on the candidate's behalf. The sweep reads this file to detect
"new-to-us" employers (so it stops re-surfacing places you have already engaged), and `funnel.py`
parses it into a funnel. Keep one row per application. Free text after the pipes is fine — the
funnel matches on stage keywords (applied / acknowledged / screening / assessment / interview /
offer / rejected / withdrawn) and company names.

## Submitted
| Date | Role | Company | Location | ATS / route | Notes (status, gotchas, confirmation) |
|------|------|---------|----------|-------------|----------------------------------------|

## Queue / candidates (not yet applied)
| Role | Company | Route | Blocker |
|------|---------|-------|---------|
