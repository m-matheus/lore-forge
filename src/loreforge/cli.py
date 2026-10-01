"""Command line entry point.

    loreforge new "Bloodborne" --format history --minutes 130 [--ref URL ...] [--footage URL ...]
    loreforge status <run>
    loreforge step <run> <step> [--force] [--section s05] [--set key=value ...]
    loreforge channel prepare [--channel midnight-realm] [--force]
    loreforge channel brand [--force]        # avatar, banner, watermark, band
    loreforge look preview <video> [--start 30] [--seconds 10]
    loreforge ui [--port 8770] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(msg: str) -> None:
    print(msg, flush=True)


def cmd_new(args) -> None:
    from loreforge.models.project import VideoProject

    slug = args.slug or f"{args.game}-{args.format}"
    p = VideoProject.create(slug, game=args.game, format=args.format,
                            target_minutes=args.minutes, scope=args.scope,
                            publisher=args.publisher or "",
                            reference_urls=args.ref or [], footage_urls=args.footage or [],
                            **({"channel_slug": args.channel} if args.channel else {}))
    _print(f"Created run {p.run_id} -> {p.run_dir}")
    _print(f"Drop wiki/PDF/notes into {p.sources_dir} and own captures into {p.footage_raw_dir}")


def cmd_status(args) -> None:
    from loreforge.models.project import VideoProject
    from loreforge.pipeline.steps import wizard_state

    p = VideoProject.load(args.run)
    _print(f"{p.run_id}: {p.game} [{p.format}] target {p.target_minutes} min "
           f"(~{p.target_words} words), channel {p.channel_slug}")
    for s in wizard_state(p):
        hint = f"  ({s['requires_hint']})" if s["status"] == "locked" else ""
        _print(f"  {s['status']:<8} {s['id']:<10} {s['label']}{hint}")
    _print(f"  costs: {p.costs.model_dump()}")


def cmd_step(args) -> None:
    from loreforge.pipeline.runner import run_step

    params: dict = {}
    for kv in args.set or []:
        key, _, value = kv.partition("=")
        params[key] = value
    if args.section:
        params["section"] = args.section
    res = run_step(args.run, args.step, params, force=args.force, log=_print)
    _print(json.dumps(res, indent=2, default=str))
    if res.get("status") == "error":
        sys.exit(1)


def cmd_channel(args) -> None:
    from loreforge.config.channels import load_channel
    from loreforge.services import brand_service, look_service

    ch = load_channel(args.channel)
    if args.action == "brand":
        made = brand_service.run(ch, force=args.force, on_log=_print)
    else:
        made = look_service.prepare_channel(ch, force=args.force, on_log=_print)
    _print(json.dumps(made, indent=2) if made else "Channel assets already prepared.")


def cmd_look(args) -> None:
    from loreforge.config.channels import load_channel
    from loreforge.services import look_service

    ch = load_channel(args.channel)
    src = Path(args.video)
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_look.mp4")
    mp4, png = look_service.render_preview(ch, src, out, start=args.start,
                                           seconds=args.seconds, on_log=_print)
    _print(f"{mp4}\n{png}")


def cmd_ui(args) -> None:
    import threading
    import webbrowser

    import uvicorn

    from loreforge.api.app import create_app

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    _print(f"LoreForge UI at {url}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    from loreforge.config.settings import get_settings

    default_channel = get_settings().default_channel
    ap = argparse.ArgumentParser(prog="loreforge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="create a run from a brief")
    p.add_argument("game")
    p.add_argument("--format", choices=["history", "facts", "catalog"], default="history")
    p.add_argument("--minutes", type=int, default=get_settings().default_target_minutes)
    p.add_argument("--scope", default="the entire game")
    p.add_argument("--publisher")
    p.add_argument("--ref", action="append", help="reference video URL (repeatable)")
    p.add_argument("--footage", action="append", help="footage video URL (repeatable)")
    p.add_argument("--slug")
    p.add_argument("--channel")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("status")
    p.add_argument("run")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("step")
    p.add_argument("run")
    p.add_argument("step")
    p.add_argument("--force", action="store_true")
    p.add_argument("--section")
    p.add_argument("--set", action="append", help="extra step param key=value")
    p.set_defaults(fn=cmd_step)

    p = sub.add_parser("channel")
    p.add_argument("action", choices=["prepare", "brand"])
    p.add_argument("--channel", default=default_channel)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_channel)

    p = sub.add_parser("look")
    p.add_argument("action", choices=["preview"])
    p.add_argument("video")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--out")
    p.add_argument("--channel", default=default_channel)
    p.set_defaults(fn=cmd_look)

    p = sub.add_parser("ui")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_ui)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
