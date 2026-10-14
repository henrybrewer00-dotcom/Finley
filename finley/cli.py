"""Command line: `finley serve | new | find | import | run | metrics | bench | formats`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .db import DB, now
from .llm import make_llm


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="finley", description="Open-source lead finder, scorer and campaign manager.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    n = sub.add_parser("new", help="create a campaign")
    n.add_argument("name")
    n.add_argument("--offer", required=True)
    n.add_argument("--icp", required=True)
    n.add_argument("--proof", default="")
    n.add_argument("--voice-file", help="text file of emails you wrote before")

    f = sub.add_parser("find", help="find new leads on the web from a description of your ideal customer")
    f.add_argument("campaign_id", type=int)
    f.add_argument("description", nargs="?", default="", help="defaults to the campaign's ICP")
    f.add_argument("--limit", type=int, default=25)
    f.add_argument("--exclude-domains", default="")
    f.add_argument("--dry-run", action="store_true", help="fake AI and fake websites, no cost")

    i = sub.add_parser("import", help="import leads from a CSV")
    i.add_argument("campaign_id", type=int)
    i.add_argument("csv_path")

    r = sub.add_parser("run", help="research, score and write emails for new leads")
    r.add_argument("campaign_id", type=int)
    r.add_argument("--limit", type=int, default=25)
    r.add_argument("--min-score", type=float, default=50)
    r.add_argument("--exclude-domains", default="")
    r.add_argument("--dry-run", action="store_true", help="fake AI responses, no cost")

    m = sub.add_parser("metrics", help="print campaign metrics as JSON")
    m.add_argument("campaign_id", type=int)

    b = sub.add_parser("bench", help="run Email Bench")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--out", default="bench/results")

    f = sub.add_parser("formats", help="list formats, or mine new ones from emails that got replies")
    fsub = f.add_subparsers(dest="formats_cmd", required=True)
    fsub.add_parser("list", help="print the format library")
    fm = fsub.add_parser("mine", help="extract formats from a text file of winning emails (split by ---)")
    fm.add_argument("emails_path")
    fm.add_argument("--out", default="my_formats.json", help="JSON file to write; load it with FINLEY_FORMATS_FILE")
    fm.add_argument("--dry-run", action="store_true")

    args = p.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0
    if args.cmd == "bench":
        from .bench import run_bench
        summary = run_bench(make_llm(args.dry_run), settings, limit=args.limit, out_dir=args.out)
        print(json.dumps(summary, indent=2))
        return 0

    if args.cmd == "formats":
        from .formats import FORMATS
        if args.formats_cmd == "list":
            for fmt in FORMATS.values():
                print(f"{fmt.key:16} {fmt.step:9} {fmt.words[0]}-{fmt.words[1]:<4} {fmt.name}: {fmt.use_when}")
            return 0
        from .mine import mine_formats
        mined = mine_formats(make_llm(args.dry_run), settings, Path(args.emails_path).read_text())
        Path(args.out).write_text(json.dumps(mined, indent=2))
        print(f"Wrote {len(mined)} new formats to {args.out}. Load them with FINLEY_FORMATS_FILE={args.out}")
        return 0

    db = DB(settings.db_path)
    if args.cmd == "new":
        voice = Path(args.voice_file).read_text() if args.voice_file else ""
        cid = db.insert("campaigns", {"name": args.name, "offer": args.offer, "icp": args.icp,
                                      "proof": args.proof, "voice_samples": voice, "created_at": now()})
        print(f"Created campaign {cid}")
    elif args.cmd == "find":
        from .sourcing import fake_fetch, find_leads
        offline = {"fetch": fake_fetch, "mx_check": lambda d: True, "email_finder": lambda d: []} if args.dry_run else {}
        result = find_leads(db, make_llm(args.dry_run), settings, args.campaign_id, description=args.description,
                            limit=args.limit, exclude_domains=args.exclude_domains, **offline)
        print(json.dumps(result, indent=2))
    elif args.cmd == "import":
        from .sources.csv_import import import_csv
        print(import_csv(db, args.campaign_id, Path(args.csv_path).read_text(encoding="utf-8-sig"),
                         source=f"csv:{Path(args.csv_path).name}"))
    elif args.cmd == "run":
        from .pipeline import run_campaign
        print(run_campaign(db, make_llm(args.dry_run), settings, args.campaign_id, limit=args.limit,
                           min_score=args.min_score, exclude_domains=args.exclude_domains))
    elif args.cmd == "metrics":
        from .metrics import campaign_metrics
        print(json.dumps(campaign_metrics(db, args.campaign_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
