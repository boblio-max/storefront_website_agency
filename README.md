# Storefront Web — automated website agency

Finds local businesses with missing or bad websites, builds each one a
modern site, deploys it, and runs personalized email outreach — including
handling replies (revision requests rebuild + redeploy automatically).

## How it works

```
maps_scraper ──► website_classifier ──► website_qualifier ──► lead_prioritizer
  (Bot 1)          (Bot 2)               (Bot 3)                (Bot 4)
  Google Maps      with/without site     keeps BAD sites        target_leads.json
  businesses.json
                                                          │
                        per lead:                         ▼
              website_generator ──► qa_bot ──► deployment_manager
                (Bot 5)              (Bot 6)     (Bot 7)
                OpenCode or          score ≥80   GitHub repo + Vercel
                built-in template    or retry    production URL
                                                          │
                                                          ▼
              current_lead_generator ──► email_generator ◄──► response_feedback_manager
                (Bot 8)                    (Bot 9)              (Bot 10)
                outreach-ready lead        SMTP outreach        classifies replies;
                                           (draft by default)   revisions loop back
                                                              to Bot 5
```

Customer-facing names are business slugs (`bothell-way-garage`), never
`lead_00001` (kept as the internal key). Sites are built with OpenCode
when available, otherwise a built-in category-aware template.

## Setup

Requires Python 3.11+, `pip install requests beautifulsoup4 playwright`
(plus `playwright install chromium`), the `gh`, `vercel`, and `opencode`
CLIs.

| Purpose | What to do |
|---|---|
| GitHub | `gh auth login` as the agency account; `gh auth status` to verify |
| Vercel | `vercel login` as the agency account; `vercel whoami` to verify |
| Sending mail | `AGENCY_SMTP_PASS` (Gmail app password for `storefront.webs@gmail.com`; host/user/sender prefilled) |
| Reading replies | `AGENCY_IMAP_PASS` (same app password; host/user prefilled) |
| Sender override | `AGENCY_FROM` (default `storefront.webs@gmail.com`) |
| Commit authorship | `AGENCY_GIT_NAME`, `AGENCY_GIT_EMAIL` (repo-local only) |
| Deploy guard | `AGENCY_GH_USER` — deploys abort if another account is active |
| Branding | `AGENCY_NAME` (default `Storefront Web`) |

If you work in other local folders, they belong to `boblio-max` — see
`../AGENTS.md`. Never mix the two accounts.

## Usage (safe → live)

```bash
py orchestrator.py --help                              # safe: shows options
py orchestrator.py --reuse --limit 1                   # 1 site, draft-only email
py orchestrator.py --reuse --limit 1 --send-emails     # actually sends (needs SMTP env)
py orchestrator.py --watch --reuse --send-emails       # continuous: new leads + inbox replies
py orchestrator.py --reply "..." --reply-lead <id>     # manual reply handling
```

- `--reuse` reuses `businesses.json` instead of re-scraping Google Maps.
- `--limit 0` processes all leads; default `1` is a safe first run.
- `--send-emails` is the ONLY flag that sends real mail. Without it,
  Bot 9 prints drafts and records nothing.
- `--watch` loops forever (Ctrl-C stops): skips deployed/emailed leads,
  polls the inbox, routes revisions back through generate → QA → deploy.

Single steps:

```bash
py website_generator.py --lead <id> --force            # build (omit --force to reuse cache)
py qa_bot.py generated_sites/<slug>                    # QA gate (≥80 passes)
py deployment_manager.py generated_sites/<slug>        # GitHub + Vercel deploy
py response_feedback_manager.py --poll                 # list unseen inbox replies
```

## Rules the pipeline enforces

- **Emails are never fabricated.** No verified address → lead is skipped
  for outreach (phone-only leads are reported, not emailed).
- **Opt-outs suppress forever** (exit 3); resends are guarded per
  lead + purpose.
- **QA failing stops the lead** — fix the site, don't force the deploy.
- **No `lead_00001` in public**: folders, repos, and Vercel projects use
  business slugs; colliding names get a short stable suffix.
- **Previews are locked demos.** Every outreach build ships with a
  visible preview banner, `noindex`, and demo-only forms — gorgeous but
  unusable until paid. `--final` rebuilds the unlocked production site
  after payment (banner gone, indexable, live forms).
- Generated sites, `deployments/`, and outreach history are pipeline
  output — lead data (`businesses.json`, `target_leads.json`) is input.
  Don't delete one thinking it's the other.
