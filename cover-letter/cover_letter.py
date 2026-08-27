#!/usr/bin/env python3
"""
cover_letter.py — generate a per-job, HONEST, tailored cover letter for the candidate.
==============================================================================

Recruiters increasingly ATS-filter on keywords and 73% of them are fine with AI-assisted
applications *as long as the content is accurate* - so tailoring each letter to the job
(grounded strictly in her real CV) is a real lever on landing interviews, without lying.

This feeds ONLY `candidate-facts.md` (her true facts) + the job description to a headless
`claude` call, with hard no-fabrication / no-degree-classification rules baked in, and
writes the tailored letter to `cover-letter/generated/`.

    python3 cover_letter.py --role "Sustainability Analyst" --company "dentsu" --jd-file jd.txt
    python3 cover_letter.py --role "..." --company "..." --url https://uk.linkedin.com/jobs/view/...-1234567890
    cat jd.txt | python3 cover_letter.py --role "..." --company "..."     # JD on stdin

Notes:
- Needs a headless `claude` on PATH + CLAUDE_CODE_OAUTH_TOKEN (sourced from the telegram-bot .env,
  same as the apply/outreach crons). Model defaults to sonnet.
- The output is the letter BODY (paragraphs). Drop it into the LaTeX template
  (the candidateCoverLetter.tex: set the Role/Company macros and replace the body) if a PDF is needed, or
  paste as plain text where a cover-letter box is required.
- INTERACTIVE alternative: inside a Claude session you don't need this script - draft the letter
  directly from candidate-facts.md + the JD, obeying the same rules. This script is for the automated
  apply-cron / batch use.
"""
import argparse, os, re, subprocess, sys, urllib.request, html

HERE = os.path.dirname(os.path.abspath(__file__))
FACTS = os.path.join(HERE, "candidate-facts.md")
OUTDIR = os.path.join(HERE, "generated")
BOT_ENV = os.path.expanduser("~/.config/job-hunt/.env")
UA = {"User-Agent": "Mozilla/5.0"}


def load_token():
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    try:
        for line in open(BOT_ENV):
            m = re.match(r'\s*CLAUDE_CODE_OAUTH_TOKEN\s*=\s*"?([^"\n]+)"?', line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""


def jd_from_url(url):
    m = re.search(r"/jobs/view/[^/]*?-(\d{6,})", url) or re.search(r"(\d{8,})", url)
    if m and "linkedin" in url:
        u = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
        page = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read().decode("utf-8", "ignore")
        mk = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
        return html.unescape(re.sub(r"<[^>]+>", " ", mk.group(1))) if mk else ""
    page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    return html.unescape(re.sub(r"<[^>]+>", " ", page))[:6000]


PROMPT = """You are writing a job application cover letter FOR the candidate. Write in the FIRST PERSON as the candidate.

Use ONLY the facts in the FACT SHEET below. This is a hard rule: do not invent any experience, skill,
employer, project, number, qualification or personal anecdote that is not on the sheet. Never state or
imply a degree classification or grade. Be honest that the candidate is a recent graduate / early-career; never
imply years of professional experience. Where the job asks for something she has not done, lean on her
genuine transferable skills rather than claiming it.

Style: British English by default (change if the fact sheet says otherwise). NO em-dashes or en-dashes
anywhere (use commas or restructure). Warm, specific, professional, and concise: roughly 250-320 words,
3-4 short paragraphs. Open by naming the role and why this specific company/role appeals (grounded in the
job description). Connect the candidate's REAL experience from the fact sheet to the role's actual stated
requirements. Close with the candidate's availability (as stated on the fact sheet) and a warm sign-off
using the candidate's name.

Output ONLY the letter text (starting "Dear {company} team," or "Dear Hiring Team,"). No preamble,
no notes, no markdown headers.

=== ROLE ===
{role} at {company}

=== JOB DESCRIPTION ===
{jd}

=== FACT SHEET (the ONLY facts you may use) ===
{facts}
"""


def generate(role, company, jd, model="sonnet"):
    facts = open(FACTS, encoding="utf-8").read()
    prompt = PROMPT.format(role=role, company=company, jd=jd[:6000].strip() or "(not provided)", facts=facts)
    env = dict(os.environ)
    tok = load_token()
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    try:
        out = subprocess.run(["claude", "-p", "--model", model, prompt],
                             capture_output=True, text=True, timeout=180, env=env)
    except FileNotFoundError:
        sys.exit("ERROR: `claude` CLI not found on PATH.")
    if out.returncode != 0:
        sys.exit(f"ERROR: claude failed: {out.stderr[:400]}")
    return out.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--jd-file")
    ap.add_argument("--url")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.jd_file:
        jd = open(a.jd_file, encoding="utf-8", errors="ignore").read()
    elif a.url:
        jd = jd_from_url(a.url)
    elif not sys.stdin.isatty():
        jd = sys.stdin.read()
    else:
        jd = ""

    letter = generate(a.role, a.company, jd, a.model)
    print(letter)

    os.makedirs(OUTDIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (a.company + "-" + a.role).lower()).strip("-")[:60]
    path = a.out or os.path.join(OUTDIR, slug + ".txt")
    open(path, "w", encoding="utf-8").write(letter + "\n")
    sys.stderr.write(f"\n[saved -> {path}]\n")


if __name__ == "__main__":
    main()
