# Storefront — automated website agency

so instead of building websites one at a time like a normal freelancer, I built a robot agency: ten bots that find local businesses with missing or terrible websites, generate modern replacements, deploy them to Vercel, and run personalized email outreach — including handling replies, where a revision request automatically rebuilds and redeploys the site. I'm basically the manager; the pipeline does the work.

## how it actually works

`orchestrator.py` (536 lines) chains each bot's `main()` in order:
1. **maps_scraper** — finds local businesses off Maps
2. **classifier** — flags the ones with missing/bad sites
3. **qualifier** — scores leads, **prioritizer** ranks them
4. **website_generator** — builds each site (OpenCode agent or template path)
5. **qa_bot** — quality gate, must score ≥80 or it's back for regen
6. **deployment_manager** — GitHub repo + Vercel deploy per site (business-slug names like `bothell-way-garage`, never `lead_00001`)
7. **email_generator** + outreach — personalized emails via SMTP, reply/revision loop watches the inbox and triggers rebuilds

`generated_sites/`, `deployments/`, and the `agency_site/` demo hold the outputs.

```bash
py orchestrator.py --help                                  # always safe, start here
py orchestrator.py --reuse --limit 1                       # 1 site, draft-only email
py orchestrator.py --reuse --limit 1 --send-emails         # actually sends (needs SMTP env)
py orchestrator.py --watch --reuse --send-emails           # continuous: new leads + inbox replies
```

## rules that keep it safe

outreach builds are locked previews (banner + noindex + demo forms) until `--final` after payment. outreach only goes to verified emails (never fabricated), opt-outs suppress forever, resends are guarded per lead+purpose. secrets stay in env (`AGENCY_SMTP_PASS`, `AGENCY_IMAP_PASS`, Stripe keys) — `.env*` is gitignored, never commit them.

## accounts (important)

this is the ONLY repo on the agency GitHub/Vercel account — everything else here runs as `boblio-max`. verify with `gh auth status` / `vercel whoami` before touching deploys; `AGENCY_GH_USER` enforcement fails loudly on mismatch instead of publishing to the wrong account.

## stack

Python 3.11 (`requests`, `bs4`, `playwright`), `gh` / `vercel` / `opencode` CLIs. safe → live pipeline: preview first, send later, watch mode for the full loop.
