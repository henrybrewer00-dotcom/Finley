# Finley

**Open-source lead finder, lead scorer and cold email campaign manager. Bring your own Claude key; pay cents, not hundreds a month.**

Finley finds leads for you from a plain-English description of your ideal customer (or takes your own list), researches each one, scores it against your ideal customer, and writes a cold email in one of 26 researched formats using Claude Opus 5.5. Every email has to pass automatic quality checks before you can send it from your own inbox. Every step logs what it cost and what happened, so you can see cost per meeting, which formats win, and whether high-scoring leads really reply more.

> Status: MVP. Lead finding, CSV import, research, scoring, writing, review, sending and metrics work today. Multi-step sequences and automatic reply detection are next.

## How it works

1. **Find leads** by describing who you sell to (see below), or **import** them from any CSV (Apollo, HubSpot, Google Sheets). Finley maps the columns, drops bad and duplicate emails, and skips anyone on your do-not-contact list.
2. **Research.** Finley reads each company's homepage and About page, and Claude Haiku pulls out up to five verifiable facts, each with its source.
3. **Score.** Free hard filters (role addresses, excluded domains) run first. Then Haiku rates fit, need, timing and reachability from 1 to 5, with a reason for each, and combines them into a 0-100 score.
4. **Write.** For leads above your threshold, Finley picks the best format for what the research found, then Claude Opus 5.5 writes the email in your voice.
5. **Gate.** Each draft must pass checks for length, links, spam phrases, AI-sounding phrases, an opener about the reader rather than the sender, paragraph length, number of questions, placeholders, reading level, subject line, and **every claim tracing back to a research fact**. A Haiku judge then scores relevance, specificity, clarity and tone. Failures go back to the writer with the reasons, up to two rewrites. Anything still failing is flagged for you instead of sent.
6. **Review and send** from the dashboard: edit, approve one by one or all that passed, then send from your own Gmail/Outlook/SMTP inbox with a daily cap and a one-click unsubscribe header.
7. **Measure.** Log replies and meetings with one click. The dashboard shows reply rate by email format and by score band, plus AI cost per lead and per meeting.

## Finding leads

Describe your ideal customer in plain English ("owner-run plumbing companies around Austin with 5 to 50 staff") and Finley builds the list:

1. Haiku turns the description into 3 to 6 web searches aimed at company websites, plus the job titles to contact.
2. Haiku runs the searches with Anthropic web search (directories, social sites and job boards are blocked) and lists the companies it found. A company is kept only if its domain actually appeared in the search results, so nothing is invented.
3. Finley reads each company's homepage and its contact, about and team pages (free) and collects the email addresses published there. Haiku picks the best one for the target titles and names the person if the page does.
4. If a site lists no email and you set `HUNTER_API_KEY` (or `TREG_TOKEN`), Finley asks Hunter. Otherwise the company is skipped. Emails are never guessed.
5. A free DNS check drops domains that can't receive mail. Duplicates, excluded domains and your do-not-contact list are skipped.

Found leads land in the campaign as `new`, with where each email came from, ready for step 2. Cost is about 1 cent per web search plus Haiku tokens: roughly 5 to 10 cents for 25 leads.

```bash
finley find 1 "Owner-run plumbing companies around Austin, TX" --limit 25
```

Or over HTTP: `POST /api/campaigns/{id}/find-leads` with JSON `{"description": "...", "limit": 25, "exclude_domains": "", "api_key": "", "dry_run": false}` returns the counts, the search plan, the cost and the new leads. A plain form post to `/campaigns/{id}/find-leads` with the same fields works too.

## The format library

Every email follows one named format from [`finley/formats.py`](finley/formats.py). There are 26, covering first emails, follow-ups and the final email, and each one records the principle it is built on. [docs/cold-email-playbook.md](docs/cold-email-playbook.md) has the research behind them and the rules the writer follows. Run `finley formats list` to see them all.

A format only becomes available when the research supports it. For example, Trigger needs a real trigger event, and Before-After-Bridge needs a case study you supplied. Haiku ranks the formats that fit each lead; once you have reply data, Finley favors the formats that actually get positive replies (Thompson sampling), while still trying the others.

To add your own, put emails that got replies in a text file separated by `---` lines and run `finley formats mine winners.txt`. Opus extracts their shape into `my_formats.json`; load it with `FINLEY_FORMATS_FILE=my_formats.json`.

## Quick start

```bash
git clone <this repo> && cd finley
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # optional: everything can also be set per account in Settings
finley serve                # http://127.0.0.1:8000, then create your account
```

Each person signs up with their own account. Their Anthropic key, sending inbox, sender profile and
do-not-contact domains live under Settings; keys and SMTP passwords are stored encrypted. The first
account also takes over any campaigns made earlier with the CLI. Set `FINLEY_SIGNUPS=closed` to stop
anyone else signing up.

Or from the command line:

```bash
finley new "Austin plumbers" --offer "We answer after-hours calls..." --icp "Owner-operated plumbers in Texas"
finley find 1 --limit 25                        # find leads from the ICP (add --dry-run to try it free)
finley import 1 examples/leads.csv              # or bring your own list
finley run 1 --limit 25 --min-score 50          # add --dry-run to try it with fake AI and no cost
finley metrics 1
```

## Deploy to Vercel (free)

The repo includes `main.py` and `requirements.txt`, so importing it into Vercel works with no settings. To keep accounts and data between restarts, add two things in the Vercel project:

1. **A database.** Under Storage, add Neon Postgres (the free tier is plenty). It sets `DATABASE_URL`, and Finley creates its tables on first start. Any Postgres URL works, including Supabase.
2. **`FINLEY_SECRET_KEY`** under Environment Variables, so users can save their Anthropic key and SMTP password (encrypted with this key). Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and never change it.

Without a database, Vercel's filesystem is temporary: SQLite lives in `/tmp` and resets when the instance restarts, and the site shows a banner saying so. Without the secret key, people paste their Anthropic key on each run instead. Never put your own Anthropic key in the environment of a public deploy.

## Cost

Finley calls Claude with **your** API key, so you pay Anthropic's list prices and nothing else. If you're on a Claude Max or Team plan, its monthly API credits cover this. Rough AI cost per lead fully worked (research, score, write, judge): about 1 to 2 cents, mostly the Opus email. Leads that fail the free filters or score below your threshold never reach Opus. Every call's real cost is in the event log; see `finley metrics`.

To trade quality for cost, set `FINLEY_WRITER_MODEL=claude-sonnet-5-5` or lower `FINLEY_WRITER_EFFORT`.

## Email Bench

```bash
finley bench               # runs the writer on 30 fixed lead profiles, follow-ups included; writes bench/results/<time>.md
finley bench --dry-run     # same, with fake AI (checks the plumbing, costs nothing)
```

Bench reports the pass rate, the **unbacked-claim rate** (target 0), the average judge score, words, rewrite attempts, first-try pass rate, cost, results per format, and which checks fail most often. Run it before and after any prompt, model or format change.

## Configuration

See [`.env.example`](.env.example). Locally, data lives in one SQLite file (`finley.db`); set `DATABASE_URL` to use Postgres instead. The dashboard binds to `127.0.0.1` and requires an account login.

## Sending responsibly

Finley is for relevant, one-to-one B2B outreach. Plain text, no tracking pixels, an unsubscribe line and a `List-Unsubscribe` header on every email, a per-inbox daily cap (default 30), and automatic suppression of bounces and unsubscribes. You're responsible for following CAN-SPAM, GDPR/PECR, CASL and Anthropic's [Usage Policy](https://www.anthropic.com/legal/aup). Set `PHYSICAL_ADDRESS` to include your postal address, as CAN-SPAM requires.

## Data sources

- Web lead finding (built in, uses your Anthropic key).
- CSV import (any provider).
- [Hunter](https://hunter.io) (optional, `HUNTER_API_KEY`): finds emails for companies whose site lists none.
- [treg](https://github.com/superdesigndev/treg) (optional, `TREG_TOKEN`): `finley/sources/treg.py` can find missing work emails through Hunter via treg's proxy, for both CSV rows and found companies. Finley calls treg; it does not bundle or re-host it.

## License

AGPL-3.0. You can self-host and modify Finley freely; if you run a modified version as a service for others, you must publish your changes.
