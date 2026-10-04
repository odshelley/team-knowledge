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


def _local_host(args: argparse.Namespace):
    from .githost.local import LocalGitHost
    from .repo import KnowledgeRepo

    repo = KnowledgeRepo(args.repo)
    return LocalGitHost(Path(repo.remote_url()), author=os.environ.get("TK_AUTHOR", "tk"))


def cmd_review_list(args: argparse.Namespace) -> int:
    for mr in _local_host(args).list_all():
        print(f"{mr.iid}\t{mr.state}\t{mr.author}\t{mr.branch}\t{mr.url}")
    return 0


def cmd_review_approve(args: argparse.Namespace) -> int:
    from .githost.base import GitHostError

    try:
        mr = _local_host(args).approve(args.branch, args.approver)
    except GitHostError as exc:
        print(f"error: {exc}")
        return 1
    print(f"{mr.branch} merged into main as {mr.merge_commit} (approved by {args.approver})")
    return 0


def _host_from_env(repo):
    """Build the git host named by TK_GITHOST."""
    from .settings import Settings, make_host

    return make_host(Settings.from_env(), repo)


def cmd_push(args: argparse.Namespace) -> int:
    from .propose import Proposer
    from .repo import KnowledgeRepo
    from .validate import Validator

    repo = KnowledgeRepo(args.repo)
    proposer = Proposer(repo, Validator(repo.schema_dir), _host_from_env(repo), author=os.environ.get("TK_AUTHOR", "tk"))
    result = proposer.push_branch(args.branch)
    if result.error:
        print(f"error: {result.error}")
        return 1
    print(f"merge request: {result.merge_request_url}")
    return 0


def cmd_graph_init(args: argparse.Namespace) -> int:
    from .graph.schema import init_schema
    from .settings import Settings, make_embedder_from, make_graph_client

    s = Settings.from_env()
    embedder = make_embedder_from(s)
    dims = embedder.dims if embedder else None
    with make_graph_client(s) as client:
        init_schema(client, dims)
    print(f"graph schema ready; vector index: {dims} dims" if dims else "graph schema ready; no vector index (TK_EMBEDDER=none)")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .graph.sync import SyncError, Syncer
    from .repo import KnowledgeRepo
    from .settings import Settings, make_embedder_from, make_graph_client

    s = Settings.from_env()
    repo = KnowledgeRepo(args.repo)
    host = None
    try:
        host = _host_from_env(repo)
    except SystemExit as exc:
        print(f"warning: no git host for review metadata ({exc})")
    with make_graph_client(s) as client:
        syncer = Syncer(repo, client, embedder=make_embedder_from(s), host=host)
        try:
            if args.embed_missing:
                print(f"embedded {syncer.embed_missing()} findings")
                return 0
            report = syncer.sync(full=args.full)
        except SyncError as exc:
            print(f"error: {exc}")
            return 1
    if report.unchanged:
        print(f"unchanged: graph already at {report.head}")
        return 0
    print(f"synced to {report.head}")
    print(f"findings upserted: {report.findings_upserted}, entities upserted: {report.entities_upserted}, "
          f"findings deleted: {report.findings_deleted}, entities deleted: {report.entities_deleted}")
    print(f"embedded: {report.embedded}, embedding failures: {report.embedding_failures}, review metadata set: {report.review_metadata_set}")
    for w in report.warnings:
        print(f"warning: {w}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from .render.build import render_site
    from .repo import KnowledgeRepo

    written = render_site(KnowledgeRepo(args.repo), args.out)
    print(f"rendered {len(written)} pages to {args.out}")
    return 0


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as serve

    serve()
    return 0


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    import tempfile

    from .eval_retrieval import run_eval
    from .graph.client import GraphClient
    from .settings import Settings, make_embedder_from

    try:
        from testcontainers.community.neo4j import Neo4jContainer
    except ImportError:
        print("error: install the eval extra (uv sync --extra eval) and start Docker")
        return 1
    embedder = make_embedder_from(Settings.from_env())
    if embedder is None or embedder.model == "fake":
        print("note: vector and fused arms are meaningless without a real embedder (set TK_EMBEDDER=openai or bedrock)")
    with Neo4jContainer("neo4j:5.26-community") as container, tempfile.TemporaryDirectory() as tmp:
        client = GraphClient(container.get_connection_url(), "neo4j", container.password)
        result = run_eval(client, embedder, Path(tmp))
        client.close()
    print(f"embedder: {result['embedder']}; {result['findings']} findings, {result['queries']} queries")
    print(f"recall@3  full-text only: {result['fulltext']:.3f}")
    print(f"recall@3  vector only:    {result['vector']:.3f}")
    print(f"recall@3  fused:          {result['fused']:.3f}")
    return 0


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

    review = sub.add_parser("review", help="local adapter review commands").add_subparsers(dest="review_command", required=True)
    p = review.add_parser("list")
    _add_repo_arg(p)
    p.set_defaults(func=cmd_review_list)
    p = review.add_parser("approve")
    p.add_argument("branch")
    p.add_argument("--as", dest="approver", required=True)
    _add_repo_arg(p)
    p.set_defaults(func=cmd_review_approve)

    p = sub.add_parser("push", help="retry push and merge request creation for a committed branch")
    p.add_argument("branch")
    _add_repo_arg(p)
    p.set_defaults(func=cmd_push)

    graph = sub.add_parser("graph", help="graph administration").add_subparsers(dest="graph_command", required=True)
    p = graph.add_parser("init", help="create constraints and indexes")
    p.set_defaults(func=cmd_graph_init)

    p = sub.add_parser("sync", help="upsert the repo into Neo4j")
    _add_repo_arg(p)
    p.add_argument("--full", action="store_true", help="wipe and rebuild")
    p.add_argument("--embed-missing", action="store_true", help="only fill null embeddings")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("render", help="build the static wiki")
    _add_repo_arg(p)
    p.add_argument("--out", type=Path, default=Path("public"))
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("serve-mcp", help="run the MCP server over stdio")
    p.set_defaults(func=cmd_serve_mcp)

    ev = sub.add_parser("eval", help="evaluations").add_subparsers(dest="eval_command", required=True)
    p = ev.add_parser("retrieval", help="recall@3 for full-text, vector, and fused search on the fixture corpus")
    p.set_defaults(func=cmd_eval_retrieval)

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
