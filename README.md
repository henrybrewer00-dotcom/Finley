# Finley

**Open-source lead finder, lead scorer and cold email campaign manager. Bring your own Claude key; pay cents, not hundreds a month.**

Finley finds leads for you from a plain-English description of your ideal customer (or takes your own list), researches each one, scores it against your ideal customer, and writes a cold email in one of ten proven formats using Claude Opus 5.5. Every email has to pass automatic quality checks before you can send it from your own inbox. Every step logs what it cost and what happened, so you can see cost per meeting, which formats win, and whether high-scoring leads really reply more.

> Status: MVP. Lead finding, CSV import, research, scoring, writing, review, sending and metrics work today. Multi-step sequences and automatic reply detection are next.

## How it works

1. **Find leads** by describing who you sell to (see below), or **import** them from any CSV (Apollo, HubSpot, Google Sheets). Finley maps the columns, drops bad and duplicate emails, and skips anyone on your do-not-contact list.
2. **Research.** Finley reads each company's homepage and About page, and Claude Haiku pulls out up to five verifiable facts, each with its source.
3. **Score.** Free hard filters (role addresses, excluded domains) run first. Then Haiku rates fit, need, timing and reachability from 1 to 5, with a reason for each, and combines them into a 0-100 score.
4. **Write.** For leads above your threshold, Finley picks the best format for what the research found, then Claude Opus 5.5 writes the email in your voice.
5. **Gate.** Each draft must pass checks for length, links, spam phrases, placeholders, reading level, subject line, and **every claim tracing back to a research fact**. A Haiku judge then scores relevance, specificity, clarity and tone. Failures go back to the writer with the reasons, up to two rewrites. Anything still failing is flagged for you instead of sent.
6. **Review and send** from the dashboard: edit, approve one by one or all that passed, then send from your own Gmail/Outlook/SMTP inbox with a daily cap and a one-click unsubscribe header.
7. **Measure.** Log replies and meetings with one click. The dashboard shows reply rate by email format and by score band, plus AI cost per lead and per meeting.

## Finding leads

Describe your ideal customer in plain English ("owner-run plumbing companies around Austin with 5 to 50 staff") and Finley builds the list:

1. Haiku turns the description into 3 to 6 web searches aimed at company websites, plus the job titles to contact.
1. If your customers are local businesses, Finley first pulls them from OpenStreetMap for free. Web search only fills the gap, so local lists often cost almost nothing.
2. Haiku runs the searches with Anthropic web search (directories, social sites and job boards are blocked) and lists the companies it found. A company is kept only if its domain actually appeared in the search results, so nothing is invented.
3. Finley reads each company's homepage and its contact, about and team pages (free) and collects the email addresses published there. In the same call, Haiku checks the site against your description, dropping franchises, vendors and other mismatches. It then picks the best email for the target titles and names the person if the page does. Parked and for-sale domains are skipped, and a site that redirects is saved under the domain it lands on.
4. If a site lists no email and you set `HUNTER_API_KEY` (or `TREG_TOKEN`), Finley asks Hunter. Otherwise the company is skipped. Emails are never guessed.
5. A free DNS check drops domains that can't receive mail. Duplicates, excluded domains and your do-not-contact list are skipped.

Found leads land in the campaign as `new`, with where each email came from, ready for step 2. Cost is about 1 cent per web search plus Haiku tokens: roughly 5 to 10 cents for 25 leads.

```bash
finley find 1 "Owner-run plumbing companies around Austin, TX" --limit 25
```

Or over HTTP: `POST /api/campaigns/{id}/find-leads` with JSON `{"description": "...", "limit": 25, "exclude_domains": "", "api_key": "", "dry_run": false}` returns the counts, the search plan, the cost and the new leads. A plain form post to `/campaigns/{id}/find-leads` with the same fields works too.

## The format library

Every email follows one named format from [`finley/formats.py`](finley/formats.py): Quick question, Trigger, Problem-Agitate-Solve, Before-After-Bridge, Relevance-Reward-Request, Peer proof, Give first, Owner-direct (local), Right person?, and Breakup. A format only becomes available when the research supports it. For example, Trigger needs a real trigger event, and Before-After-Bridge needs a case study you supplied. Add your own with `formats.load_extra("my_formats.json")`.

## Quick start

```bash
git clone <this repo> && cd finley
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # add your ANTHROPIC_API_KEY and, to send, your SMTP details
finley serve                # http://127.0.0.1:8000
```

Or from the command line:

```bash
finley new "Austin plumbers" --offer "We answer after-hours calls..." --icp "Owner-operated plumbers in Texas"
finley find 1 --limit 25                        # find leads from the ICP (add --dry-run to try it free)
finley import 1 examples/leads.csv              # or bring your own list
finley run 1 --limit 25 --min-score 50          # add --dry-run to try it with fake AI and no cost
finley metrics 1
```

## Deploy to Vercel (free)

The repo includes `main.py` and `requirements.txt`, so importing it into Vercel works with no settings. On Vercel, each person pastes their own Anthropic key on the Run form; it's used for that run only and never stored. Note that serverless storage is temporary: the SQLite file lives in `/tmp` and resets when the instance restarts, so treat a Vercel deploy as a demo until a hosted database is added.

## Cost

Finley calls Claude with **your** API key, so you pay Anthropic's list prices and nothing else. If you're on a Claude Max or Team plan, its monthly API credits cover this. Rough AI cost per lead fully worked (research, score, write, judge): about 1 to 2 cents, mostly the Opus email. Leads that fail the free filters or score below your threshold never reach Opus. Every call's real cost is in the event log; see `finley metrics`.

To trade quality for cost, set `FINLEY_WRITER_MODEL=claude-sonnet-5-5` or lower `FINLEY_WRITER_EFFORT`.

## Email Bench

```bash
finley bench               # runs the writer on 20 fixed lead profiles; writes bench/results/<time>.md
finley bench --dry-run     # same, with fake AI (checks the plumbing, costs nothing)
```

Bench reports the pass rate, the **unbacked-claim rate** (target 0), the average judge score, words, rewrite attempts, cost and which formats were used. Run it before and after any prompt, model or format change.

## Configuration

See [`.env.example`](.env.example). Data lives in one SQLite file (`finley.db`). The dashboard binds to `127.0.0.1`. Set `FINLEY_PASSWORD` before exposing it anywhere.

## Sending responsibly

Finley is for relevant, one-to-one B2B outreach. Plain text, no tracking pixels, an unsubscribe line and a `List-Unsubscribe` header on every email, a per-inbox daily cap (default 30), and automatic suppression of bounces and unsubscribes. You're responsible for following CAN-SPAM, GDPR/PECR, CASL and Anthropic's [Usage Policy](https://www.anthropic.com/legal/aup). Set `PHYSICAL_ADDRESS` to include your postal address, as CAN-SPAM requires.

## Data sources

- Web lead finding (built in, uses your Anthropic key).
- CSV import (any provider).
- [Hunter](https://hunter.io) (optional, `HUNTER_API_KEY`): finds emails for companies whose site lists none.
- [treg](https://github.com/superdesigndev/treg) (optional, `TREG_TOKEN`): `finley/sources/treg.py` can find missing work emails through Hunter via treg's proxy, for both CSV rows and found companies. Finley calls treg; it does not bundle or re-host it.

## License

AGPL-3.0. You can self-host and modify Finley freely; if you run a modified version as a service for others, you must publish your changes.
