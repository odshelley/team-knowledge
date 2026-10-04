# team-knowledge

A shared, reviewed body of team findings that Claude Code can write to and read from.

Someone learns that approach X does not work on system Y. They say "record that" in Claude Code,
confirm a one-paragraph preview, and a merge request opens. A colleague approves it. From then on,
anyone on the team who asks Claude Code "what do we know about Y" gets it, and it appears on a
rendered wiki. Findings are structured files, not prose: a kind, a falsifiable claim, the systems and
projects it applies to, evidence, and a confidence.

The design is in `docs/superpowers/specs/2026-10-04-team-knowledge-core-design.md`.

## How it works

```
 Claude Code  --record-->  knowledge repo (git)  --merge request-->  reviewer approves
                                   |
                                   v  tk sync (CI on merge; by hand at home)
 Claude Code  <--ask-----  Neo4j graph (derived, rebuildable)      tk render --> static wiki
```

| Piece | What it holds | Who writes it |
|---|---|---|
| Knowledge repo | one markdown file per finding and per entity; the source of truth | the MCP server, as branches and merge requests |
| Git host | merge requests and approvals | reviewers (GitLab at work; a local bare repo at home) |
| Neo4j | a derived graph: findings, entities, sources, people, edges, embeddings, approver and MR link | only `tk sync` |
| Static wiki | rendered pages for browsing and audit | `tk render`, from the files |
| MCP server | eight tools Claude Code calls; runs on your machine | you, via `.mcp.json` |

Findings have five kinds: `dead-end`, `caveat`, `how-to`, `decision`, `fact`. They are scoped by
entities of five types: `project`, `system`, `environment`, `desk`, `tool`. Entities are the only
vocabulary; a finding cannot be scoped to something that has no entity page.

## Who runs what, and why Neo4j

Neo4j is the **shared team database**: one instance, read by everyone's MCP server. That is how a
colleague's finding reaches you. They record it, a reviewer approves the merge request, CI runs
`tk sync` on merge, and the finding is in the graph the next time you ask. Nobody syncs by hand at
work, and the MCP server cannot write to Neo4j at all, so the graph has exactly one writer and review
cannot be bypassed.

The repo is the source of truth and the graph is a derived copy. Sync is idempotent and incremental,
and `tk sync --full` rebuilds the graph from scratch, so losing Neo4j loses nothing.

At home there is no CI and one user, so you run the reviewer's approval and the sync yourself. If you
would rather not leave Claude Code for it, ask the agent to run the sync command in its shell; it is
an ordinary CLI command, deliberately not an MCP tool.

## Quickstart at home

Everything below runs from this directory with `uv run --env-file .env tk ...`, so the variables in
`.env` are picked up. Docker is needed for the scratch database.

**1. Install and start a scratch Neo4j**

```bash
uv sync --all-extras
docker run -d --name tk-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password neo4j:5.26-community
```

**2. Create a knowledge repo with a local stand-in for GitLab**

```bash
uv run tk init ~/tk-data --local-remote ~/tk-data.git --author <your-username>
```

This scaffolds the repo from the packaged template (schema, CI file, one example entity and
finding) and pushes it to a bare repo at `~/tk-data.git` that plays the part of GitLab.

**3. Write `.env`** in this directory:

```bash
TK_REPO=/Users/<you>/tk-data
TK_GITHOST=local
TK_AUTHOR=<your-username>
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
TK_EMBEDDER=openai          # or bedrock | fake | none
OPENAI_API_KEY=...
```

**4. Create the graph schema and load the repo**

```bash
uv run --env-file .env tk graph init                 # constraints, indexes, vector index
uv run --env-file .env tk validate --repo ~/tk-data  # 1 findings, 1 entities, 0 errors
uv run --env-file .env tk sync --repo ~/tk-data      # writes the graph
```

**5. Seed your vocabulary.** Add entity files under `~/tk-data/entities/<type>/<slug>.md`, copying
`entities/system/example-system.md`. Give each an `owner` (the GitLab username who should review
findings about it) and a few `aliases`. Commit and push to `main` in `~/tk-data`, then run
`tk validate` and `tk sync` again. This step decides how precise retrieval is later, so use the names
your team actually says. Set `review.default_reviewers` in `~/tk-data/config.yaml` for entities
with no owner.

**6. Wire Claude Code.** In the directory you work from, add `.mcp.json`:

```json
{"mcpServers": {"team-knowledge": {"command": "uv",
  "args": ["run", "--env-file", "/Users/<you>/Projects/team-knowledge/.env",
           "--project", "/Users/<you>/Projects/team-knowledge", "tk", "serve-mcp"]}}}
```

and install the skill: `cp -R skills/team-knowledge ~/.claude/skills/`. Optionally add a
`.team-knowledge.yaml` to that directory with a `scope:` list of entity refs, so "what do we know"
defaults to that repo's systems.

## Day-to-day use

**Record.** In Claude Code: `record that <what you learned>`, or `/team-knowledge record`. The agent
drafts the finding in the schema, shows a preview, asks once, and opens the merge request. It reports
the URL and the reviewers.

**Review.** At work: approve the merge request in GitLab. At home, you are the reviewer:

```bash
uv run --env-file .env tk review list --repo ~/tk-data
uv run --env-file .env tk review approve finding/<id> --as <reviewer> --repo ~/tk-data
```

The approver must not be the author.

**Sync.** At work, CI runs this on every merge to `main`. At home, pull the merge into your clone
first, then sync:

```bash
git -C ~/tk-data pull --ff-only
uv run --env-file .env tk sync --repo ~/tk-data
```

**Ask.** In Claude Code: `what do we know about <system>?`, `anything about <topic>?`, or
`/team-knowledge ask <question>`. Scoped questions use the graph lookup; open questions use hybrid
full-text and semantic search.

**Browse.**

```bash
uv run --env-file .env tk render --repo ~/tk-data --out ~/tk-site && open ~/tk-site/index.html
```

Neo4j Browser is at `http://localhost:7474` for the scratch container.

**Correct or withdraw.** Say `supersede <id> with ...`, `amend <id> ...`, or `retract <id> because ...`
in Claude Code. Each opens a merge request like a new finding.

## Commands

| Command | Does |
|---|---|
| `tk init <dir> [--local-remote PATH] [--author NAME]` | scaffold a knowledge repo from the template |
| `tk validate [--repo PATH] [--base REF]` | validate the repo; `--base` checks status transitions (CI uses `CI_MERGE_REQUEST_DIFF_BASE_SHA` automatically) |
| `tk graph init` | create constraints and indexes for the configured embedder |
| `tk sync [--repo PATH] [--full] [--embed-missing]` | upsert the repo into Neo4j; `--full` wipes and rebuilds; `--embed-missing` fills null embeddings |
| `tk render [--repo PATH] --out DIR` | build the static wiki |
| `tk review list` / `tk review approve BRANCH --as USER` | local git host only |
| `tk push BRANCH` | retry a failed push and merge request |
| `tk eval retrieval` | recall@3 for full-text, vector, and fused search on a fixture corpus (starts its own container) |
| `tk serve-mcp` | run the MCP server over stdio |

## Environment variables

| Variable | Meaning |
|---|---|
| `TK_REPO` | path to your clone of the knowledge repo |
| `TK_GITHOST` | `local` or `gitlab` |
| `TK_AUTHOR` | your username; written into `author` and used to exclude you from your own reviewers |
| `TK_GITLAB_MODE`, `GITLAB_URL`, `GITLAB_TOKEN` | GitLab only: `api` (needs a token with `api` scope) or `push-options` |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | the graph; database defaults to `neo4j` |
| `TK_EMBEDDER` | `openai` (`text-embedding-3-small`, 1536 dims), `bedrock` (Titan v2, 1024 dims), `fake` (tests), `none` (full-text only) |
| `OPENAI_API_KEY`, `AWS_REGION` | credentials for the chosen embedder; Bedrock uses the standard AWS chain |

## Troubleshooting

- **"behind main; rebase it first" on approve.** `main` moved after the branch was opened. In your clone:
  `git fetch && git checkout <branch> && git rebase origin/main && git push --force-with-lease origin <branch>`,
  then approve again. GitLab handles this itself.
- **`tk sync` says `unchanged` right after an approve.** Your clone has not pulled the merge. `git -C ~/tk-data pull --ff-only`, then sync.
- **No reviewers resolved.** The scoped entities have no `owner` and `config.yaml` has no `review.default_reviewers`.
- **"knowledge graph unreachable" from the agent.** Neo4j is down or the `.env` does not reach the server. Recording still works; the wiki URL in the message is the fallback.
- **"embedding model changed; run tk sync --full".** You switched `TK_EMBEDDER`. A full rebuild recreates the vector index at the new dimension. Never run `--full` against a database that holds anything else.
- **Driver notices about relationship types that "do not exist".** Harmless; they appear until the graph has its first supersede or contradict edge.

## At work

Use `TK_GITHOST=gitlab`, set `gitlab.project` in `config.yaml`, and let the template's `.gitlab-ci.yml`
validate merge requests and run `tk sync` and `tk render` on merge. Set `TK_EMBEDDER=bedrock`. Neo4j
must be Community 5.13 or later. Spec section 19 is the rebuild checklist and section 22 lists the
assumptions to verify.

## Develop

```bash
uv run pytest                  # Neo4j tests need Docker; they skip without it
uv run pytest -m "not neo4j"   # fast subset
uv run ruff check src tests
```
