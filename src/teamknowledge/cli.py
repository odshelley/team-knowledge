"""The tk command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__


def _add_repo_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", type=Path, default=Path.cwd(), help="path to the knowledge repo clone (default: cwd)")


def cmd_init(args: argparse.Namespace) -> int:
    from .repo import init_knowledge_repo

    repo = init_knowledge_repo(args.dir, local_remote=args.local_remote, author=args.author)
    print(f"initialised knowledge repo at {repo.root}")
    if args.local_remote:
        print(f"local remote at {Path(args.local_remote).resolve()}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .repo import KnowledgeRepo
    from .validate import Validator

    repo = KnowledgeRepo(args.repo)
    validator = Validator(repo.schema_dir)
    findings, entities, errors = repo.load_with_errors()
    previous = None
    base = args.base or os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA")
    if base:
        prev_findings, _ = repo.load_at(base)
        previous = {f.id: f for f in prev_findings}
    errors = errors + validator.validate_all(findings, entities, previous)
    if args.files:
        wanted = {str(Path(f)) for f in args.files}
        errors = [e for e in errors if e.path in wanted]
    for e in errors:
        line = f"{e.path or '-'}: {e.field}: {e.message}"
        if e.suggestion:
            line += f" ({e.suggestion})"
        print(line)
    print(f"{len(findings)} findings, {len(entities)} entities, {len(errors)} errors")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tk", description="Team Knowledge")
    parser.add_argument("--version", action="version", version=f"tk {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="scaffold a knowledge repo from the template")
    p.add_argument("dir", type=Path)
    p.add_argument("--local-remote", type=Path, default=None, help="create a bare repo here and push main to it")
    p.add_argument("--author", default=os.environ.get("TK_AUTHOR", "tk"))
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate", help="validate findings and entities")
    _add_repo_arg(p)
    p.add_argument("--all", action="store_true", help="validate everything (the default; accepted for CI readability)")
    p.add_argument("--base", default=None, help="git ref to check status transitions against")
    p.add_argument("files", nargs="*", help="restrict reported errors to these paths")
    p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
