# website_agency — Storefront Web (agency account)

## GitHub account
All pipeline GitHub operations (repo create/push) MUST run as the
**Storefront Web agency account** — never `boblio-max`.
- Verify: `gh auth status` → active account must be the agency one.
- Switch: `gh auth switch --user <agency-login>`.
- `deployment_manager` enforces this via `AGENCY_GH_USER`: if the active
  account mismatches, deploys fail loudly instead of publishing wrong.
- Deploy commit authorship is set per-repo from
  `AGENCY_GIT_NAME` / `AGENCY_GIT_EMAIL` (repo-local config only).

## Vercel account
Deploys go to whichever account `vercel` is logged into.
- Verify: `vercel whoami` → must be the Storefront Web account.
- The CLI holds ONE login: `vercel logout` + `vercel login` to change it.
- Old lead projects were deleted; each site links its own fresh project.

## Pipeline (safe → live)
```
py orchestrator.py --help                                  # always safe
py orchestrator.py --reuse --limit 1                       # 1 site, draft-only email
py orchestrator.py --reuse --limit 1 --send-emails         # actually sends (needs SMTP env)
py orchestrator.py --watch --reuse --send-emails           # continuous: new leads + inbox replies
py orchestrator.py --reply "..." --reply-lead <id>         # manual reply handling
```

## Conventions
- Customer-facing names are business slugs (`bothell-way-garage`), never
  `lead_00001`. `lead_id` stays the internal key across JSONs.
- Outreach only to verified emails (never fabricated); opt-outs suppress
  forever; resends are guarded per lead+purpose.
- Env: `AGENCY_SMTP_*`, `AGENCY_IMAP_*`, `AGENCY_FROM`,
  `AGENCY_GIT_NAME/EMAIL`, `AGENCY_GH_USER`, `AGENCY_NAME`.
