"""`comfybulk recipes` subcommands: list, show, run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..recipes import list_recipes, load_recipe, run_recipe


def cmd_recipes(a) -> int:
    action = a.action
    if action == "list":
        return _cmd_list()
    if action == "show":
        return _cmd_show(a.name)
    if action == "run":
        return _cmd_run(a.name, Path(a.input), Path(a.output))
    print(f"unknown action: {action}", file=sys.stderr)
    return 2


def _cmd_list() -> int:
    rows = list_recipes()
    for r in rows:
        thumb = "thumb" if r.thumb_path.exists() else "no-thumb"
        desc = r.body.get("description", "")
        print(f"{r.name}\t{thumb}\t{desc}")
    return 0


def _cmd_show(name: str) -> int:
    r = load_recipe(name)
    print(json.dumps(r.body, indent=2))
    return 0


def _cmd_run(name: str, input_dir: Path, output_dir: Path) -> int:
    if not input_dir.is_dir():
        print(f"input is not a directory: {input_dir}", file=sys.stderr)
        return 1
    outs = run_recipe(name, input_dir, output_dir)
    print(f"recipe={name} processed={len(outs)} output={output_dir}")
    for o in outs:
        print(f"  {o}")
    return 0


def add_subparser(sub) -> None:
    sp = sub.add_parser("recipes", help="Named recipe templates (etsy-product, realestate-listing, social-square)")
    rsub = sp.add_subparsers(dest="action", required=True)

    rsub.add_parser("list", help="List available recipes")

    sp_show = rsub.add_parser("show", help="Print the parsed JSON for a recipe")
    sp_show.add_argument("name")

    sp_run = rsub.add_parser("run", help="Apply a recipe to a folder of inputs")
    sp_run.add_argument("name")
    sp_run.add_argument("--input", required=True)
    sp_run.add_argument("--output", required=True)

    sp.set_defaults(func=_dispatch)


def _dispatch(a):
    rc = cmd_recipes(a)
    if rc:
        sys.exit(rc)
