# team-knowledge

Structured, reviewed team findings for Claude Code, GitLab, and Neo4j. The design is in
`docs/superpowers/specs/2026-10-04-team-knowledge-core-design.md`.

A finding is one validated markdown file in a knowledge repo. It arrives as a merge request from
Claude Code, a colleague approves it, CI syncs it into Neo4j and republishes a static wiki, and
teammates query it from Claude Code.

## Install

    uv sync --all-extras          # development
    pip install teamknowledge     # at work, from the internal index

## Set up a knowledge repo

    tk init ~/src/team-knowledge-data --local-remote ~/src/team-knowledge-data.git   # home prototype
    tk init ~/src/team-knowledge-data                                                  # then push to GitLab

Edit `config.yaml` for the GitLab project path, default reviewers, and wiki base URL. Seed `entities/`
with the team's projects, systems, environments, desks, and tools before recording findings.

## Configure

Environment variables, kept in your shell profile:

    TK_REPO=~/src/team-knowledge-data      # your clone
    TK_GITHOST=gitlab                      # or local
    TK_GITLAB_MODE=api                     # or push-options
    GITLAB_URL=https://gitlab.bank  GITLAB_TOKEN=...   TK_AUTHOR=your.username
    NEO4J_URI=bolt://...  NEO4J_USERNAME=neo4j  NEO4J_PASSWORD=...  NEO4J_DATABASE=neo4j
    TK_EMBEDDER=bedrock                    # or openai, fake, none

Claude Code `.mcp.json`:

    {"mcpServers": {"team-knowledge": {"command": "tk", "args": ["serve-mcp"], "env": {"TK_REPO": "/home/me/src/team-knowledge-data"}}}}

Install the skill by copying `skills/team-knowledge/` into your Claude Code skills directory.

## Commands

| Command | Does |
|---|---|
| `tk validate [--base REF]` | validate the knowledge repo |
| `tk graph init` | create constraints and indexes |
| `tk sync [--full] [--embed-missing]` | upsert the repo into Neo4j (CI runs this on main) |
| `tk render --out public` | build the static wiki (CI publishes it to Pages) |
| `tk review list` / `tk review approve BRANCH --as USER` | local adapter review |
| `tk push BRANCH` | retry a failed push and merge request |
| `tk eval retrieval` | recall@3 for full-text, vector, fused |
| `tk serve-mcp` | run the MCP server over stdio |

`tk validate` reads `CI_MERGE_REQUEST_DIFF_BASE_SHA` from the environment in GitLab merge-request pipelines to
check status transitions against the target branch; `--base REF` does the same thing locally.

## Develop

    uv run pytest                  # Neo4j tests need Docker; they skip without it
    uv run pytest -m "not neo4j"   # fast subset
    uv run ruff check src tests
