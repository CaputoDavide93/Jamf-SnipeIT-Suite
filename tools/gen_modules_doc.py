#!/usr/bin/env python3
"""Generate the CLI command inventory table in README.md.

Source of truth:
  - src/main.py            -> subparser registrations (command name + help text)
                              and the run-group handler map (modules reachable
                              only via `run-group`, e.g. monthly-digest)
  - src/docker_scheduler.py -> default cron per scheduled job

The table is written between <!-- AUTOGEN:modules --> markers in README.md.

Usage:
  python tools/gen_modules_doc.py           # regenerate README table
  python tools/gen_modules_doc.py --check   # exit 1 if the table is stale
"""

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "src" / "main.py"
SCHEDULER = ROOT / "src" / "docker_scheduler.py"
README = ROOT / "README.md"

START = "<!-- AUTOGEN:modules -->"
END = "<!-- /AUTOGEN:modules -->"


def parse_subparsers(tree: ast.AST) -> dict:
    """Return {command: help_text} from subparsers.add_parser(...) calls."""
    commands = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            continue
        name = node.args[0].value
        help_text = ""
        for kw in node.keywords:
            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                help_text = " ".join(str(kw.value.value).split())
        commands[name] = help_text
    return commands


def parse_run_group_map(tree: ast.AST) -> list:
    """Return module names registered in cmd_run_group's name_to_handler map."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_run_group":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    return [k.value for k in inner.keys
                            if isinstance(k, ast.Constant)]
    return []


def parse_scheduler_crons(source: str) -> dict:
    """Return {job_key: default_cron} from jobs_config[...].get('cron', ...)."""
    pattern = r"jobs_config\['(\w+)'\]\.get\('cron',\s*'([^']+)'\)"
    return dict(re.findall(pattern, source))


def build_table() -> str:
    tree = ast.parse(MAIN.read_text())
    commands = parse_subparsers(tree)
    group_only = [m for m in parse_run_group_map(tree) if m not in commands]
    crons = parse_scheduler_crons(SCHEDULER.read_text())

    lines = [
        "| Command | What it does | Scheduler default (cron) |",
        "|---------|--------------|---------------------------|",
    ]
    for name, help_text in commands.items():
        cron = crons.get(name.replace("-", "_"), "—")
        lines.append(f"| `{name}` | {help_text} | `{cron}` |"
                     if cron != "—" else f"| `{name}` | {help_text} | — |")
    for name in group_only:
        cron = crons.get(name.replace("-", "_"), "—")
        cron_cell = f"`{cron}`" if cron != "—" else "—"
        lines.append(f"| `{name}` | Reachable via `run-group` only | {cron_cell} |")

    count = len(commands) + len(group_only)
    header = (f"*{count} CLI commands, generated from `src/main.py` by "
              f"`tools/gen_modules_doc.py` — do not edit by hand.*\n")
    return header + "\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Fail (exit 1) if README table is out of date")
    args = parser.parse_args()

    readme = README.read_text()
    if START not in readme or END not in readme:
        print(f"ERROR: {START} markers not found in README.md", file=sys.stderr)
        return 2

    table = build_table()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    new_readme = pattern.sub(f"{START}\n{table}\n{END}", readme)

    if args.check:
        if new_readme != readme:
            print("STALE: README module table does not match src/main.py — "
                  "run `python tools/gen_modules_doc.py`", file=sys.stderr)
            return 1
        print("OK: README module table is up to date")
        return 0

    README.write_text(new_readme)
    print("README.md module table regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
