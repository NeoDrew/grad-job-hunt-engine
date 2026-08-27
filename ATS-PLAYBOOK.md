# ATS application playbook

Reusable recipes for applying **as the candidate** through each ATS, via a browser-automation
tool (this system used chromeflow; see SETUP.md). The open-source auto-appliers break because
they hard-code one platform's selectors; this is the opposite - the accumulated per-ATS quirks so
a future apply is fast and doesn't re-learn the same traps. Full per-application detail lives in
`data/applications-log.md`; account logins live in `data/credentials.md` (gitignored). **The
candidate's canonical form answers live in `data/application-brief.md`** (name/email/phone/address,
right-to-work, sponsorship, start date, salary range, driving licence).

## Golden rules (every ATS)
1. **NEVER LinkedIn Easy Apply if the browser session is signed in as someone other than the
   candidate.** Easy Apply submits under whoever is logged in. If a role is Easy-Apply-only and the
   session is not the candidate's, leave it for the candidate to do herself/himself.
2. **Custom-element inputs usually ignore synthetic fills.** `fill_input` reports success but the
   framework's model stays empty. Fix: trusted `type_text` (real keystrokes) after focusing, OR
   the native setter + dispatched events:
   `setter=Object.getOwnPropertyDescriptor(proto,'value').set; setter.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true})); ...'change'...`
3. **Submit buttons often don't fire on `click_element`.** Use `click_at_coordinates` (trusted) or a
   full dispatched sequence: `['pointerdown','mousedown','pointerup','mouseup','click']`.
4. **CV upload can hit the wrong file input** (profile photo vs resume). `set_file_input` (CDP mode,
   `file_path`) reaches closed shadow roots; if it targets the photo, temporarily park the image-only
   input: set its `type='text'` so the tool targets the real resume input, then restore it.
5. **reCAPTCHA / anti-bot submit** → trusted `click_at_coordinates` on the checkbox + Submit (isTrusted);
   confirm success via the email/confirmation page, not the button.
6. **Confirm the submission actually landed** - a success page or the acknowledgement email - never
   assume from a click. Log it in `applications-log.md`.

## Per-ATS recipes

### Workday  (ERM, dentsu, MUFG)  — `*.myworkdayjobs.com`
- Create account with email+password (`<candidate-password>`). **Some tenants require email verification, some
  don't** - dentsu didn't, ERM did (click the `/activate` link from her Gmail before sign-in works).
- Flow is 5-7 steps. "Autofill with Resume" mangles the parse (mismatched titles/dates) - verify each
  entry, or use "Apply Manually" + upload CV in the Experience step.
- **`Select One` dropdowns** are buttons opening a `<ul>` listbox: click to open, then click the `<li>`
  via a full pointer-event dispatch (a single click is flaky).
- **Country/phone LOVs**: type to filter, then click the matching option.
- **Malformed CV-parsed education tile** ("Unnamed Major", "Fields to fix: 1") blocks submit - delete it
  (her A-Levels are captured elsewhere). Optional Work/Education can be left "No Response".
- **Submit needs the dispatched pointer sequence**, not a plain click. Honeypot: `input[name=website]` - leave empty.

### Oracle Recruiting Cloud  (WSP, Pure Data Centres)  — `*.oraclecloud.com/hcmUI/CandidateExperience`
- "Use your email" flow: enter email → agree terms → **6-digit PIN emailed to her Gmail** (read it, enter
  the 6 pin-code inputs). CV auto-imports the profile.
- Equality fields are **`cx-select` LOVs**: type to filter, then `click_at_coordinates` the option
  (dispatched clicks are flaky). **Disability is a radio group**, not a dropdown.
- On submit, use **"Go to Next Issue"** to jump to each blocking field. Honeypot `input[name=website]`.

### SmartRecruiters  (Primark)  — `jobs.smartrecruiters.com` / `apply.workable`-style guest
- `spl-input` custom elements: **don't bind synthetic fills** - use trusted `type_text`; the **City field is
  an autocomplete** - type then click the suggestion to commit.
- **Resume**: `set_file_input` keeps hitting the image-only profile-photo input - park it (see rule 4) so it
  targets the real Resume `#file-input`.
- A "Next" click surfaces unbound required fields via validation; re-enter and they stick.

### SAP SuccessFactors  (Burberry, Capgemini)  — `career*.successfactors.eu`
- **Cascading picklists** (Country of Residence, Country/Region Code) are `juic` widgets that won't
  open/commit via clicks. Root-cause via the page's own `juic.Component._registry`: call
  `model.fetchItems({fetchFirstPageItems:true})` with a temporarily-patched `model.dispatch` to capture
  the real option ids (label→value), then `comp.setValue({value,label})`. **The server validates the
  internal numeric picklist id, NOT the ISO code** (guessing "GB" passes client-side but fails on submit
  and wipes the form).
- **This ATS WIPES every `<select>`/combobox on a failed-validation postback** - re-set every select via the
  native setter + dispatch IMMEDIATELY before the final Apply click, not earlier.
- "How did you hear" is a juic autocomplete: `type_text` into it, then dispatch mousedown/mouseup/click on
  the matched `<li role=option>`.

### Salesforce Experience Cloud  (Sonnedix)  — `*.my.site.com`
- Apply usually via **Google SSO** (the candidate's account in the chooser - NOT another person's). The social-login loops
  through account-chooser + consent and may throw **"ERROR_LOGGING_IN / Temporary error"** - just retry
  (re-open `/s/social-login`); the header then shows "Log Out".
- All fields are in **Lightning shadow DOM** - walk shadow roots recursively to map ids; `set_file_input`
  (CDP) reaches them. Phone fields: digits-only. If the landing errors ("Invalid Page"), go straight to
  `/s/apply?vacancyId=<id>`.

### Workable  (AVK-SEG, Radley Yeldar)  — `apply.workable.com/{slug}`
- Clean guest apply; standard `fill_input` works for name/email/phone. **Custom React radios** (names like
  `QA_...`, value true/false) **regenerate their ids on every click's re-render** - re-query by NAME each
  iteration (a stale `getElementById` throws after the first).

### Vincere  (Timely Recruit)  — `*.vincere.io`
- Screening `<select>` dropdowns **don't bind via `fill_input`** (reports success, DOM value stays empty) -
  set via the native `HTMLSelectElement` value setter + dispatch input/change. reCAPTCHA submit via trusted click.

### Gravity Forms (WordPress)  (Oxygen Conservation)
- Standard inputs - `fill_input` by selector works. The **submit button is often theme-hidden (0×0)** - fire
  it directly: `document.getElementById('gform_submit_button_{formid}').click()` (triggers GF validation +
  AJAX). Success shows a `.gform_confirmation_message`.

### networx / current-vacancies  (MTVH, Workspace)  — `*.current-vacancies.com`, `networxrecruitment`
- Account + email verify. After "Submit Application" a **`#JobAlertConfirmation` modal invisibly overlays the
  form and eats clicks** (looks like the radios keep failing validation) - dismiss it ("Notify/Don't Notify")
  to finalise; the submit had already validated.

### Teamtailor  (Deepki, White Stuff, CDP, Revolution Beauty)  — `{slug}.teamtailor.com`
- Guest apply. The **consent checkbox is Stimulus-controlled** and resists synthetic clicks - set `checked`,
  dispatch a bubbling `change`, then `form.requestSubmit()`.

### Phenom  (ICF)
- CV parse auto-fills contact/work/education; **fix the Degree dropdowns the parser leaves blank** (Masters
  for the degree university; "High School" for pre-university - no "A Level" option exists).

### Ashby / Pinpoint / Greenhouse  (discovery + apply)
- These are the clean JSON feeds the sweep polls for discovery (`{slug}.pinpointhq.com/postings.json`,
  `api.ashbyhq.com/posting-api/job-board/{slug}`, `boards-api.greenhouse.io/v1/boards/{slug}/jobs`). Their
  apply forms are standard guest applies.

### Reed  (daily cron)
- Logged in as **the candidate via her Google SSO**. One-click "Easy apply" as the candidate is fine here (it's her account,
  unlike LinkedIn). Verify the card's real location before applying (Reed's "in London" filter leaks nationwide).

## When you hit a NEW ATS
1. Map the form: `execute_script` walking shadow roots for `input/select/textarea/[contenteditable]`.
2. Try `fill_input` first; if the value doesn't stick, fall back to trusted `type_text` or the native setter.
3. For dropdowns: identify plain `<select>` (native setter) vs custom LOV (type-to-filter + click option).
4. Submit via `click_at_coordinates` / dispatched sequence; verify via confirmation page or email.
5. **Add the new ATS + its quirk to this file** so the next apply is faster.
