# Team Knowledge: core loop design

| | |
|---|---|
| Date | 2026-10-04 |
| Status | Draft for review |
| Scope | First of four specs. This one covers the core loop: schema, capture, review, sync, retrieval, wiki. |
| Later specs | LLM maintenance; connectors; multi-team. See section 20. |

## 1. Problem and goal

Teams learn things that never get written down well. Someone finds that approach X does not work for project gamma. That is useful to everyone else on the team, but it rarely reaches the wiki or Confluence, and when it does, it is prose of uneven quality that nobody can find later.

Team Knowledge is a shared, structured, reviewed body of team findings that Claude Code can write to and read from. GitLab holds the source of truth and provides review. Neo4j holds a derived graph for retrieval. A rendered static wiki provides browsing and audit.

**Success criterion for the first three months.** A teammate avoids a dead end because the agent surfaced another teammate's finding that they did not know existed. Secondary: findings get recorded regularly because capture is one confirmation away.

## 2. Context and constraints

- **First users** are the author's own team at a large British investment bank. Other teams at the bank come later.
- **Work environment.** On-prem only, no code egress. Neo4j Community self-hosted on AWS: a single database, no role-based access control, offline backups. GitLab for source control. AWS Bedrock with Claude models. Claude Code is in use by the team.
- **Home build is a prototype.** It validates the design. This document is the deliverable. The system is rebuilt at work from this document using Python, Neo4j Community, GitLab, and Bedrock. Every dependency and every database feature named here must be available in that environment.
- **No dependency** on alethograph, knowledge-retrieval, graphify, or kgc. This project stands alone.
- **Capture and primary consumption happen inside Claude Code**, invoked deliberately by the user. Nothing is injected into a session automatically.
- **Light review.** One approver other than the author, before a finding becomes visible to others.
- **Findings are structured**, not arbitrary text. Structure is enforced at the gate by a validator, not hoped for.

## 3. Non-goals for this spec

- LLM-written synthesis prose on entity pages, contradiction detection, staleness sweeps. These are the maintenance spec.
- Crawling or indexing Confluence, links into code or a code graph. These are the connectors spec. This spec stores Confluence and GitLab URLs as evidence references only.
- Multi-team namespaces, per-team reviewers, cross-team visibility rules. This spec serves one team with one knowledge repo.
- Hooks that surface findings automatically on session start or per prompt.
- A review user interface. Review is a GitLab merge request.
- Free-form tags. The entity vocabulary is the only taxonomy.
- Writing to Neo4j from anywhere other than CI sync.

## 4. Architecture

```
  Person's machine                          GitLab                           AWS
 +-------------------------------+     +------------------------+     +------------------+
 | Claude Code                   |     | knowledge repo         |     | Neo4j Community  |
 |  +-- skill: team-knowledge    |     |  findings/  entities/  |     |  derived graph   |
 |  |     record / ask           |     |  schema/   config.yaml |     |  full-text index |
 |  +-- MCP server (stdio)       |     |                        |     |  vector index    |
 |       write tools --git push--+---->| branch finding/<id>    |     +--------^---------+
 |                    --REST-----+---->| merge request          |              |
 |       read tools  --bolt------+---------------------------------------------+
 |                               |     |   CI on MR: validate   |              |
 | local clone of knowledge repo |     |   CI on main: sync ----+--------------+
 +-------------------------------+     |                render -+---> GitLab Pages (wiki)
                                       +------------------------+
```

Components:

| Component | Role | Writes to |
|---|---|---|
| Knowledge repo | Source of truth: findings, entities, schema, config | nothing |
| MCP server | Runs locally per person. Validates and proposes findings as merge requests. Reads the graph. | knowledge repo branches, merge requests |
| CI validate | Reruns the validator on every merge request | nothing |
| CI sync | On merge to main, upserts changed files into Neo4j and computes embeddings | Neo4j |
| CI render | On merge to main, builds the static wiki from the files | GitLab Pages |
| Claude Code skill | Tells the agent how to record a finding and how to look findings up | nothing directly |

Data flow for a finding's life:

1. A person tells Claude Code to record something. The skill drafts a finding in the schema, the person confirms once, and the MCP tool validates it, commits it on a branch, pushes, and opens a merge request.
2. CI validates the merge request. A reviewer who is not the author approves. The merge request merges to main.
3. CI sync upserts the finding into Neo4j with its edges and embedding. CI render republishes the wiki.
4. Later, a teammate asks Claude Code what is known about a system. The agent calls the read tools, which query Neo4j, and presents the findings with their wiki links.

## 5. Data model

### 5.1 The finding file

One finding is one markdown file at `findings/<ulid>-<slug>.md`. The file has a YAML header and a short body made of kind-specific sections.

```markdown
---
id: 01J9XK3M8Q7ZV2W1F4N6B5HT9D
kind: dead-end
title: Batch kdb+ queries over 10k symbols time out against the UAT gateway
claim: >
  Sending a single select over more than ~10k syms to the UAT kdb+ gateway
  hits the 30s gateway timeout; the gateway does not stream partial results.
scope:
  - system/kdb-gateway
  - environment/uat
  - project/gamma
applies_when: gateway version 4.x; prod has a 120s timeout so the limit differs
evidence:
  - type: observation
    note: Reproduced 2026-10-04 with a 12k-symbol select, error 'timeout'.
  - type: confluence
    ref: https://confluence.bank/x/KDB-GW-LIMITS
  - type: gitlab
    ref: https://gitlab.bank/pricing/gamma/-/merge_requests/412
confidence: observed
status: active
supersedes: []
contradicts: []
author: osian.shelley
created: 2026-10-04
review_after: 2027-04-01
---

## Approach

Issue one `select` over the full symbol list in a single gateway call.

## Why it fails

The UAT gateway enforces a 30 second timeout per call and returns nothing on
timeout. A 12k-symbol select takes roughly 45 seconds.

## Instead

Chunk the symbol list at 5k and issue the selects concurrently; see the
how-to on batching gateway calls.
```

Header fields:

| Field | Required | Rules |
|---|---|---|
| `id` | yes | ULID, 26 characters, assigned by the tool, never edited |
| `kind` | yes | one of `dead-end`, `caveat`, `how-to`, `decision`, `fact` |
| `title` | yes | 10 to 120 characters, a specific declarative claim |
| `claim` | yes | 20 to 600 characters, the finding itself in one to three sentences |
| `scope` | yes | 1 to 8 entity refs; every ref must resolve to an entity page |
| `applies_when` | no | up to 300 characters of conditions or version constraints |
| `evidence` | yes | 1 to 10 items; see evidence types below |
| `confidence` | yes | `observed` (author saw it), `inferred` (author deduced it), `reported` (someone told the author) |
| `status` | yes | `active`, `superseded`, `retracted` |
| `supersedes` | no | finding ids this one replaces |
| `superseded_by` | when status is superseded | the replacing finding id |
| `contradicts` | no | finding ids this one disagrees with, set by humans in this spec |
| `retracted_reason` | when status is retracted | 10 to 300 characters |
| `author` | yes | GitLab username, set by the tool from configuration |
| `created` | yes | ISO date, set by the tool |
| `review_after` | no | ISO date, a staleness hint, mainly for facts |

Evidence items are one of:

- `observation` with a required `note` of 10 to 400 characters and no `ref`.
- `confluence`, `gitlab`, `runbook`, or `url` with a required `ref` and an optional `note`.

Review metadata, meaning the merge request, approver, and merge time, is not stored in the file. It is derived from GitLab at sync time. The file stays the author's statement.

### 5.2 Kinds and body sections

The body may contain only level-two headings from the kind's section list, in the listed order, each non-empty if required, with no other headings and no content before the first heading. The whole body is capped at 400 words. These rules live in `schema/kinds.yaml`:

```yaml
schema_version: 1
max_body_words: 400
kinds:
  dead-end:
    description: An approach that does not work in the given scope.
    sections:
      - { heading: Approach,      required: true }
      - { heading: Why it fails,  required: true }
      - { heading: Instead,       required: false }
  caveat:
    description: Non-obvious behaviour that causes mistakes.
    sections:
      - { heading: Symptom,       required: true }
      - { heading: Cause,         required: true }
      - { heading: Workaround,    required: true }
  how-to:
    description: A recipe that is known to work in the given scope.
    sections:
      - { heading: Steps,         required: true }
      - { heading: Verify,        required: true }
  decision:
    description: A choice the team made, and why.
    sections:
      - { heading: Options considered, required: true }
      - { heading: Rationale,          required: true }
      - { heading: Consequences,       required: true }
  fact:
    description: A stable property of a system or environment.
    sections:
      - { heading: Detail,        required: true }
      - { heading: How to check,  required: true }
```

### 5.3 The entity file

Entities are the controlled vocabulary findings are scoped by. Five types: `project`, `system`, `environment`, `desk`, `tool`. One entity is one file at `entities/<type>/<slug>.md`. The ref is `<type>/<slug>`. The slug comes from the filename and the type from the directory; the header repeats the type and the validator checks they agree.

```markdown
---
type: system
name: kdb+ gateway
aliases: [kdb gateway, gw, pricing-gateway]
description: >
  The q process that fronts the tick databases for the pricing desk. All
  application reads go through it; it enforces per-call timeouts.
owner: [jane.doe]
related:
  - project/gamma
  - tool/kdb
links:
  - type: confluence
    ref: https://confluence.bank/x/KDB-GW
  - type: gitlab
    ref: https://gitlab.bank/infra/kdb-gateway
---

Optional free markdown, at most 300 words, for context that does not fit the
description.
```

| Field | Required | Rules |
|---|---|---|
| `type` | yes | one of the five types, must match the directory |
| `name` | yes | 2 to 80 characters |
| `aliases` | no | unique strings, used by fuzzy lookup |
| `description` | yes | 20 to 600 characters |
| `owner` | no | GitLab usernames; owners are the default reviewers for findings scoped here |
| `related` | no | up to 20 entity refs; retrieval expands one hop over these |
| `links` | no | same shape as reference evidence |

A scope ref must resolve to an existing entity, or the merge request that introduces the finding must also add the entity page. That is how the vocabulary grows under review. Adding an entity type is a schema change and goes through its own merge request.

### 5.4 Identifiers

- Finding id: a ULID. The filename is `<ulid>-<slug>.md` where the slug is derived from the title, lower-case, hyphenated, at most 60 characters. The slug may be changed by an amendment; the id may not.
- Entity ref: `<type>/<slug>`, slug matching `[a-z0-9][a-z0-9-]{0,62}`.
- Usernames: GitLab usernames, matching `[A-Za-z0-9._@-]+`.

### 5.5 Status transitions

| From | To | How | Validator checks |
|---|---|---|---|
| active | superseded | `propose_finding` with `supersedes: [old]` edits the old file in the same merge request, setting `status: superseded` and `superseded_by: new` | for every id in A's `supersedes`, that finding has `status: superseded` and `superseded_by: A`, and A is active |
| active | retracted | `retract_finding` sets `status: retracted` and `retracted_reason` | reason present |
| superseded, retracted | anything | not allowed | status of an existing finding may only move forward |

Superseded and retracted are terminal. To bring a retracted claim back, write a new finding.

### 5.6 Schema files

`schema/finding.schema.json` validates the header. `schema/kinds.yaml` validates the body. Cross-file checks, meaning scope resolution, id uniqueness, supersede consistency, and legal transitions, are in the validator code. All three run in the MCP tool before any git operation and again in CI on every merge request.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "team-knowledge/finding/v1",
  "title": "Finding header",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "kind", "title", "claim", "scope", "evidence",
               "confidence", "status", "author", "created"],
  "properties": {
    "id":            { "$ref": "#/$defs/findingId" },
    "kind":          { "enum": ["dead-end", "caveat", "how-to", "decision", "fact"] },
    "title":         { "type": "string", "minLength": 10, "maxLength": 120 },
    "claim":         { "type": "string", "minLength": 20, "maxLength": 600 },
    "scope":         { "type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": true,
                       "items": { "$ref": "#/$defs/entityRef" } },
    "applies_when":  { "type": "string", "maxLength": 300 },
    "evidence":      { "type": "array", "minItems": 1, "maxItems": 10,
                       "items": { "$ref": "#/$defs/evidence" } },
    "confidence":    { "enum": ["observed", "inferred", "reported"] },
    "status":        { "enum": ["active", "superseded", "retracted"] },
    "supersedes":    { "type": "array", "uniqueItems": true,
                       "items": { "$ref": "#/$defs/findingId" } },
    "superseded_by": { "$ref": "#/$defs/findingId" },
    "contradicts":   { "type": "array", "uniqueItems": true,
                       "items": { "$ref": "#/$defs/findingId" } },
    "retracted_reason": { "type": "string", "minLength": 10, "maxLength": 300 },
    "author":        { "$ref": "#/$defs/username" },
    "created":       { "type": "string", "format": "date" },
    "review_after":  { "type": "string", "format": "date" }
  },
  "allOf": [
    { "if":   { "properties": { "status": { "const": "superseded" } } },
      "then": { "required": ["superseded_by"] } },
    { "if":   { "properties": { "status": { "const": "retracted" } } },
      "then": { "required": ["retracted_reason"] } }
  ],
  "$defs": {
    "findingId": { "type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$" },
    "entityRef": { "type": "string",
                   "pattern": "^(project|system|environment|desk|tool)/[a-z0-9][a-z0-9-]{0,62}$" },
    "username":  { "type": "string", "pattern": "^[A-Za-z0-9._@-]+$" },
    "evidence": {
      "oneOf": [
        { "type": "object", "additionalProperties": false, "required": ["type", "note"],
          "properties": {
            "type": { "const": "observation" },
            "note": { "type": "string", "minLength": 10, "maxLength": 400 } } },
        { "type": "object", "additionalProperties": false, "required": ["type", "ref"],
          "properties": {
            "type": { "enum": ["confluence", "gitlab", "runbook", "url"] },
            "ref":  { "type": "string", "minLength": 1, "maxLength": 500 },
            "note": { "type": "string", "maxLength": 400 } } }
      ]
    }
  }
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "team-knowledge/entity/v1",
  "title": "Entity header",
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "name", "description"],
  "properties": {
    "type":        { "enum": ["project", "system", "environment", "desk", "tool"] },
    "name":        { "type": "string", "minLength": 2, "maxLength": 80 },
    "aliases":     { "type": "array", "uniqueItems": true,
                     "items": { "type": "string", "minLength": 1, "maxLength": 80 } },
    "description": { "type": "string", "minLength": 20, "maxLength": 600 },
    "owner":       { "type": "array", "uniqueItems": true,
                     "items": { "type": "string", "pattern": "^[A-Za-z0-9._@-]+$" } },
    "related":     { "type": "array", "uniqueItems": true, "maxItems": 20,
                     "items": { "type": "string",
                       "pattern": "^(project|system|environment|desk|tool)/[a-z0-9][a-z0-9-]{0,62}$" } },
    "links":       { "type": "array",
                     "items": { "type": "object", "additionalProperties": false,
                       "required": ["type", "ref"],
                       "properties": {
                         "type": { "enum": ["confluence", "gitlab", "runbook", "url"] },
                         "ref":  { "type": "string", "minLength": 1, "maxLength": 500 },
                         "note": { "type": "string", "maxLength": 400 } } } }
  }
}
```

The `date` format requires the validator to run with format checking enabled. Entity bodies are optional and capped at 300 words.

## 6. Knowledge repo layout and configuration

```
findings/<ulid>-<slug>.md         flat; kind lives in the header
entities/project/<slug>.md
entities/system/<slug>.md
entities/environment/<slug>.md
entities/desk/<slug>.md
entities/tool/<slug>.md
schema/finding.schema.json
schema/entity.schema.json
schema/kinds.yaml
config.yaml                        team-level settings, no secrets
.gitlab-ci.yml                     validate on MR; sync and render on main
README.md                          how to install the MCP server and the skill
```

`config.yaml`:

```yaml
schema_version: 1
gitlab:
  project: pricing/team-knowledge      # path with namespace
  target_branch: main
review:
  default_reviewers: [pricing-quants]  # GitLab usernames or group handles, used when no scoped entity has an owner
wiki:
  base_url: https://pricing.pages.gitlab.bank/team-knowledge
```

Personal credentials are never in the repo. They come from environment variables on each person's machine and from CI variables in GitLab.

## 7. Review flow

The review flow is a merge request from end to end.

1. The MCP tool validates the proposal with the same validator CI uses. It then fetches, creates a branch `finding/<ulid>` from `origin/main`, commits the finding file and any new entity pages, pushes, and opens a merge request. The description is templated from the header: title, claim, scope, evidence, confidence, and the kind-specific sections.
2. Reviewers are the union of `owner` lists on the scoped entities. If that union is empty, reviewers are `review.default_reviewers` from `config.yaml`.
3. CI on the merge request runs `tk validate --all` and fails the pipeline on any violation.
4. GitLab's approval rule requires one approval and prevents authors approving their own merge request. Main is a protected branch. Both are repository settings, not code.
5. On merge, CI on main runs `tk sync` then `tk render`.
6. Amendments, supersessions, and retractions are merge requests produced by the same tooling, on branches named `amend/<ulid>-<short random>` and `retract/<ulid>`. Supersession uses the new finding's `finding/<ulid>` branch and edits the old file there.

Merge request creation has two paths. The primary path is the GitLab REST API with the person's personal access token with the `api` scope. The fallback, for environments where personal tokens are restricted, is GitLab push options, which need only `git push`:

```
git push -o merge_request.create -o merge_request.target=main \
         -o merge_request.title="..." -o merge_request.description="..." \
         -o merge_request.assign="jane.doe"
```

The GitLab adapter supports both and `config.yaml` does not need to know which one is used; it is a per-person environment setting.

## 8. Git host adapter

Local git operations, meaning fetch, branch, commit, and push, are done with the `git` executable through `subprocess` in `repo.py`. The adapter covers only what the hosting service adds: merge requests and their review state.

```python
@dataclass(frozen=True)
class MergeRequest:
    iid: int | str
    url: str
    branch: str
    state: Literal["open", "merged", "closed"]
    author: str
    approvers: list[str]
    merged_at: datetime | None
    merge_commit: str | None

class GitHost(Protocol):
    def open_merge_request(self, branch: str, title: str, description: str,
                           reviewers: list[str]) -> MergeRequest: ...
    def merge_request_for_branch(self, branch: str) -> MergeRequest | None: ...
    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]: ...
    def list_open_by(self, username: str) -> list[MergeRequest]: ...
```

Two implementations.

- **GitLab.** REST calls through `requests`. Endpoints used: create merge request, get merge request by source branch, list merge requests by merge commit, list approvals. Authentication by `PRIVATE-TOKEN` header. When configured for push options, `open_merge_request` pushes with the options and then polls `merge_request_for_branch` once to return the record.
- **Local.** Used by the home prototype and by tests. The remote is a bare repo on disk. A merge request is a JSON file in a sidecar directory next to the bare repo, `<remote>.review/<branch>.json`. `tk review approve <branch> --as <username>` records the approver, refuses if the approver is the author, fast-forward merges the branch into main in the bare repo, and records the merge commit. `tk review list` prints open records.

## 9. MCP server

The server runs locally over stdio inside each person's Claude Code. It is configured by environment variables:

| Variable | Meaning |
|---|---|
| `TK_REPO` | path to the person's clone of the knowledge repo; the server manages this clone and keeps it on `main` between operations |
| `TK_GITHOST` | `gitlab` or `local` |
| `TK_GITLAB_MODE` | `api` or `push-options`, GitLab only |
| `GITLAB_URL`, `GITLAB_TOKEN` | GitLab only |
| `TK_AUTHOR` | GitLab username written into `author` |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | read access to the graph; `NEO4J_DATABASE` defaults to `neo4j` |
| `TK_EMBEDDER` | `openai`, `bedrock`, or `none`; needed at query time for semantic search |
| provider credentials | `OPENAI_API_KEY`, or the standard AWS credential chain for Bedrock |

The `.mcp.json` snippet each person adds to Claude Code:

```json
{
  "mcpServers": {
    "team-knowledge": {
      "command": "tk",
      "args": ["serve-mcp"],
      "env": { "TK_REPO": "/home/me/src/team-knowledge-data" }
    }
  }
}
```

The server never writes to Neo4j.

### 9.1 Write tools

**`propose_finding`**

Input:

```json
{
  "kind": "dead-end",
  "title": "...",
  "claim": "...",
  "scope": ["system/kdb-gateway", "environment/uat"],
  "applies_when": "...",
  "evidence": [{"type": "observation", "note": "..."}],
  "confidence": "observed",
  "sections": {"Approach": "...", "Why it fails": "...", "Instead": "..."},
  "supersedes": [],
  "review_after": "2027-04-01",
  "new_entities": [
    {"type": "system", "slug": "kdb-gateway", "name": "...", "description": "...",
     "aliases": [], "owner": [], "related": [], "links": []}
  ]
}
```

Behaviour: assign a ULID and `created`, set `author` from `TK_AUTHOR`, serialise, validate against the current `main` plus `new_entities`. If invalid, return the error list and stop. If valid, branch, commit, push, open the merge request.

Output on success:

```json
{"finding_id": "01J9...", "branch": "finding/01J9...", "merge_request_url": "https://...",
 "reviewers": ["jane.doe"], "warnings": []}
```

Output on validation failure:

```json
{"errors": [
  {"field": "scope[1]", "message": "entity 'system/kdb-gw' does not exist",
   "suggestion": "closest existing: system/kdb-gateway (alias 'gw'); or add it in new_entities"}
]}
```

Output when git or the host fails after the commit:

```json
{"finding_id": "01J9...", "branch": "finding/01J9...", "merge_request_url": null,
 "error": "push failed: ...", "retry": "tk push finding/01J9..."}
```

**`amend_finding`** takes `finding_id`, optional header changes limited to `title`, `claim`, `scope`, `applies_when`, `evidence`, `confidence`, `contradicts`, `review_after`, optional replacement `sections`, and a `reason` that becomes the merge request description. Same validation and output shapes. The id, kind, author, and created date cannot be changed; changing the kind means a new finding.

**`retract_finding`** takes `finding_id` and `reason`. Sets `status: retracted` and `retracted_reason`, opens the merge request.

### 9.2 Read tools

All read tools query Neo4j, which contains accepted findings only. Each returns finding summaries in one shape:

```json
{"id": "01J9...", "kind": "dead-end", "title": "...", "claim": "...",
 "scope": ["system/kdb-gateway", "environment/uat"], "confidence": "observed",
 "status": "active", "author": "osian.shelley", "created": "2026-10-04",
 "wiki_url": "https://.../findings/01J9....html", "score": 0.83,
 "matched_scope": ["system/kdb-gateway"]}
```

- **`findings_for_scope(scope: list[str], kinds: list[str] = [], include_related: bool = true, limit: int = 50)`.** Deterministic. Returns active findings scoped to any of the given entities, expanded one hop over `RELATED_TO` when `include_related` is true. Direct matches rank above related-only matches; within a tier, more matched entities first, then newest first.
- **`search_findings(query: str, scope: list[str] = [], kinds: list[str] = [], limit: int = 10)`.** Hybrid. Runs full-text and vector search, fuses them, filters to active, boosts scope matches. See section 11.3. If the embedder is `none` or unavailable, runs full-text only and sets a warning in the response.
- **`get_finding(finding_id)`.** The full finding including body sections, evidence, review metadata, and links to superseding or contradicting findings.
- **`list_entities(type: str | None = None, query: str | None = None, limit: int = 20)`.** Resolves vocabulary. With a query, full-text over name, aliases, and description. Returns ref, type, name, aliases, description, owner, and the count of active findings.
- **`my_proposals()`.** The caller's open merge requests from the git host, with branch, title, URL, and state. This is the only tool that shows unaccepted work, and only to its author.

## 10. The Claude Code skill

`skills/team-knowledge/SKILL.md` is a user-invoked skill. Nothing in it runs unless the user asks. It has two modes.

**Record.** Triggered by `/team-knowledge record` or a plain request like "record that". The skill gathers what was learned from the session, chooses the kind, resolves scope with `list_entities` and asks the user only when a ref is genuinely ambiguous, drafts the header and sections, shows a compact preview of title, kind, scope, and claim, asks for one confirmation, and calls `propose_finding`. It reports the merge request URL and the reviewers. If validation fails, it fixes what it can from the suggestions and asks the user only for what it cannot.

**Ask.** Triggered by `/team-knowledge ask <question or scope>` or a plain request like "what do we know about the UAT gateway". The skill resolves scope refs from the words used, and from an optional `.team-knowledge.yaml` in the current code repo that lists that repo's entity refs. It calls `findings_for_scope` for scoped questions and `search_findings` for open questions, and presents results as a short list with kind, title, claim, author, date, and wiki link. It never invents a finding and says plainly when nothing was found.

The skill's description encourages invoking Ask at the start of work on a scoped system. It does not force it. The skill also tells the agent that the write tools open merge requests that colleagues will review, so the draft should read as a colleague would want to read it.

## 11. Neo4j graph

### 11.1 Nodes and edges

| Label | Key | Properties |
|---|---|---|
| `Finding` | `id` | kind, title, claim, body, sections (JSON string), applies_when, confidence, status, retracted_reason, author, created, review_after, path, commit, text_hash, embedding, embedding_model, mr_url, approved_by (list), merged_at, observations (list of notes) |
| `Entity` | `ref` | type, slug, name, aliases (list), aliases_text (joined string for full-text), description, body, path |
| `Source` | `ref` | type |
| `Person` | `username` | |
| `Meta` | `key` = `"meta"` | schema_version, last_sync_commit, embedding_model, embedding_dims, synced_at |

| Edge | From | To | Notes |
|---|---|---|---|
| `SCOPED_TO` | Finding | Entity | |
| `CITES` | Finding | Source | property `note` |
| `SUPERSEDES` | Finding | Finding | |
| `CONTRADICTS` | Finding | Finding | |
| `AUTHORED_BY` | Finding | Person | |
| `RELATED_TO` | Entity | Entity | from the entity's `related` list |
| `OWNED_BY` | Entity | Person | |
| `LINKS_TO` | Entity | Source | property `note` |

### 11.2 Schema statements

Run by `tk graph init`. Every statement here is supported by Neo4j Community 5.13 or later. Existence and property type constraints are Enterprise-only and are deliberately absent; the validator enforces those rules before data reaches the database.

```cypher
CREATE CONSTRAINT finding_id     IF NOT EXISTS FOR (f:Finding) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT entity_ref     IF NOT EXISTS FOR (e:Entity)  REQUIRE e.ref IS UNIQUE;
CREATE CONSTRAINT source_ref     IF NOT EXISTS FOR (s:Source)  REQUIRE s.ref IS UNIQUE;
CREATE CONSTRAINT person_name    IF NOT EXISTS FOR (p:Person)  REQUIRE p.username IS UNIQUE;
CREATE CONSTRAINT meta_key       IF NOT EXISTS FOR (m:Meta)    REQUIRE m.key IS UNIQUE;

CREATE INDEX finding_status IF NOT EXISTS FOR (f:Finding) ON (f.status);
CREATE INDEX finding_kind   IF NOT EXISTS FOR (f:Finding) ON (f.kind);
CREATE INDEX entity_type    IF NOT EXISTS FOR (e:Entity)  ON (e.type);

CREATE FULLTEXT INDEX finding_text IF NOT EXISTS
  FOR (f:Finding) ON EACH [f.title, f.claim, f.body];
CREATE FULLTEXT INDEX entity_text IF NOT EXISTS
  FOR (e:Entity) ON EACH [e.name, e.aliases_text, e.description];

// dimensions are interpolated by tk graph init from the configured embedder;
// vector index DDL does not accept parameters
CREATE VECTOR INDEX finding_embedding IF NOT EXISTS
  FOR (f:Finding) ON (f.embedding)
  OPTIONS { indexConfig: { `vector.dimensions`: 1024, `vector.similarity_function`: 'cosine' } };
```

When `TK_EMBEDDER` is `none`, the vector index is not created and search runs full-text only.

### 11.3 Read queries

`findings_for_scope`:

```cypher
UNWIND $refs AS ref
MATCH (e:Entity {ref: ref})
OPTIONAL MATCH (e)-[:RELATED_TO]-(r:Entity)
WITH collect(DISTINCT e) AS direct,
     CASE WHEN $include_related THEN collect(DISTINCT r) ELSE [] END AS related
UNWIND direct + related AS ent
MATCH (f:Finding {status: 'active'})-[:SCOPED_TO]->(ent)
WHERE size($kinds) = 0 OR f.kind IN $kinds
WITH f,
     sum(CASE WHEN ent IN direct THEN 1 ELSE 0 END) AS direct_hits,
     count(DISTINCT ent) AS hits,
     collect(DISTINCT ent.ref) AS matched
ORDER BY direct_hits DESC, hits DESC, f.created DESC
LIMIT $limit
MATCH (f)-[:SCOPED_TO]->(s:Entity)
RETURN f, matched, collect(s.ref) AS scope
```

`search_findings`, two queries run in parallel, each returning up to `3 * limit` candidates:

```cypher
CALL db.index.fulltext.queryNodes('finding_text', $lucene_query) YIELD node, score
WHERE node.status = 'active' AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score ORDER BY score DESC LIMIT $k
```

```cypher
CALL db.index.vector.queryNodes('finding_embedding', $k, $query_vector) YIELD node, score
WHERE node.status = 'active' AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score
```

The Lucene query is built from the user's text by escaping Lucene special characters and joining terms with `OR`, so a natural-language question never produces a syntax error. The two ranked lists are fused in Python with reciprocal rank fusion, `score = sum(1 / (60 + rank))` over the lists an id appears in. If `scope` was given, the fused score of any finding scoped to one of those refs is multiplied by 1.5. The top `limit` ids are then hydrated with one query that returns the finding and its scope refs.

## 12. Sync

`tk sync` runs in CI on main, serialised by a GitLab resource group so two merges never sync concurrently. It is incremental by default and idempotent.

1. Read `Meta.last_sync_commit`. If absent, behave as `--full`.
2. `git diff --name-status <last>..HEAD -- findings/ entities/` to get added, modified, deleted, and renamed files.
3. Parse and validate every file at `HEAD`, because cross-file checks need the full set. Any validation failure aborts the sync before writing.
4. Entities first. For each added or modified entity: `MERGE (e:Entity {ref})`, set properties, delete its outgoing `RELATED_TO`, `OWNED_BY`, `LINKS_TO`, and recreate them, merging `Entity`, `Person`, and `Source` targets by key. For deleted entities: `DETACH DELETE`.
5. Findings next. For each added or modified finding: compute `text_hash` over title, claim, and body. If the stored hash and embedding model match, keep the stored embedding; otherwise call the embedder. `MERGE (f:Finding {id})`, set properties, delete its outgoing `SCOPED_TO`, `CITES`, `SUPERSEDES`, `CONTRADICTS`, `AUTHORED_BY`, and recreate them. For deleted findings, which should not happen because retraction is the supported path, `DETACH DELETE` anyway.
6. Review metadata. For each changed finding, find the merge commit that introduced the change with `git log --merges -1 --format=%H -- <path>`, ask the adapter for the merge requests on that commit, and set `mr_url`, `approved_by`, and `merged_at`. If the adapter call fails, log it, leave those properties as they were, and continue. Sync is never blocked on the GitLab API.
7. Write `Meta` with `last_sync_commit = HEAD` and the embedder's model and dimensions, only if every step succeeded. If any file failed, the job exits non-zero and `last_sync_commit` is not advanced, so the next run retries the same range.

Writes go through `UNWIND $rows` in batches of 100.

`tk sync --full` deletes every node in batches using `CALL { ... } IN TRANSACTIONS OF 10000 ROWS`, reruns `tk graph init`, and replays every file. Use it for recovery, schema changes, and embedding model changes. `tk sync --embed-missing` finds findings whose `embedding` is null or whose `embedding_model` differs from the configured one and fills them in without touching anything else.

## 13. Render

`tk render --out public` reads the repo files, not Neo4j, so the wiki builds even when the database is down. Markdown bodies are converted with the `markdown` package. Templates are Jinja2 with a single inline stylesheet, no JavaScript, no external assets, so the site works behind the bank's proxy.

| Page | Path | Content |
|---|---|---|
| Index | `index.html` | counts by kind and status, the twenty newest findings, the entity list by type |
| Finding | `findings/<ulid>.html` | header as a definition list, sections, evidence with links, scope links, supersedes and superseded-by and contradicts links, author and date |
| Entity | `entities/<type>/<slug>.html` | description, owners, links, related entities, active findings grouped by kind, then superseded and retracted findings collapsed under a heading |
| Kind | `kinds/<kind>.html` | all active findings of that kind, newest first |
| Sources | `sources.html` | every cited reference grouped by type, with the findings and entities that cite it |

Finding and entity pages are what the read tools return as `wiki_url`. The base URL comes from `config.yaml`.

## 14. CI

`.gitlab-ci.yml` in the knowledge repo:

```yaml
stages: [validate, publish]

image: python:3.12-slim

variables:
  GIT_DEPTH: 0                      # sync diffs from the last synced commit; shallow clones break that
  PIP_INDEX_URL: $INTERNAL_PYPI_URL # at work; unset at home

before_script:
  - pip install --quiet "teamknowledge==0.1.*"

validate:
  stage: validate
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - tk validate --all

pages:                              # the job must be named "pages" for GitLab Pages
  stage: publish
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  resource_group: sync              # never two syncs at once
  script:
    - tk sync
    - tk render --out public
  artifacts:
    paths: [public]
```

CI variables set in the GitLab project, all masked: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `TK_EMBEDDER`, the embedder's credentials, `GITLAB_URL`, and a `GITLAB_TOKEN` with `read_api` scope so sync can read merge request approvals. `TK_GITHOST` is `gitlab` and `TK_AUTHOR` is unused in CI.

## 15. Embeddings

```python
class Embedder(Protocol):
    model: str
    dims: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

| Implementation | Model | Dims | Where |
|---|---|---|---|
| `bedrock` | Amazon Titan Text Embeddings V2 | 1024 | work; credentials from the standard AWS chain, region from `AWS_REGION` |
| `openai` | `text-embedding-3-small` | 1536 | home prototype |
| `fake` | hash-based deterministic vectors | 64 | tests |
| `none` | no embeddings; vector index not created; search is full-text only | | either |

The embedded text is `title + "\n" + claim + "\n" + body`. Embeddings are computed in CI during sync and stored on the finding node with the model name. `search_findings` embeds the query at call time on the person's machine, which is why the MCP server has embedder configuration too. The model and dimensions are recorded on `Meta`; `tk sync` refuses to run if the configured embedder differs from `Meta` and tells the operator to run `tk sync --full`.

A note on the two environments: Aura at home and self-hosted Community at work both support the vector index used here. The integration test suite runs against a Community container so that nothing Aura-specific creeps in.

## 16. Failure handling

| Situation | Behaviour |
|---|---|
| Proposal fails validation | Return the structured error list. Nothing is written, no git operation runs. |
| Push or merge request creation fails | The finding is already committed on a local branch. Return the branch name and `tk push <branch>`. The clone is returned to `main`. A drafted finding is never lost. |
| Two people propose at once | Separate branches, ULIDs cannot collide, no conflict. |
| Two findings are merged close together | CI jobs on main are serialised by the resource group. The second sync sees both commits in its diff range. |
| A file fails validation during sync | Sync aborts before writing, the job fails, `last_sync_commit` does not advance. Because CI validated the merge request, this means someone bypassed review or the schema changed; the job output says which file. |
| The embedder is down | Retry three times with backoff. Then store the finding with `embedding` null and log it. The job still succeeds. `tk sync --embed-missing` fills the gap later. |
| The GitLab API is down during sync | Review metadata stays as it was. Logged. The job still succeeds. |
| Neo4j is unreachable from a read tool | Return an error that says the knowledge graph is unreachable and gives the wiki base URL. |
| Neo4j is unreachable from sync | The job fails and is retried by GitLab's retry policy. The next merge also resyncs the missed range because `last_sync_commit` did not advance. |
| Neo4j is lost or corrupted | `tk sync --full` rebuilds it from the repo. Nothing is lost because the repo is the source of truth. |
| The embedding model changes | `tk sync` refuses and asks for `tk sync --full`. |
| Someone edits Neo4j by hand | Community edition cannot prevent it. The next full sync overwrites it. This is accepted because the graph is derived. |

## 17. Testing

- **Unit.** Parse and serialise round trips for findings and entities. Every validator rule against a fixture corpus with one valid example per kind and one invalid example per rule: missing section, extra heading, unresolved scope ref, duplicate id, empty evidence, oversize body, illegal status transition, inconsistent supersede pair, type and directory mismatch. ULID generation and slug derivation. Lucene query escaping. Reciprocal rank fusion and scope boost.
- **Integration.** One end-to-end test with the local git host adapter and a Neo4j Community 5.x container at the version used at work: `tk init`, propose, `tk review approve`, `tk sync`, every read tool returns the finding, `tk render` produces the expected pages. A second run of `tk sync` on the same commit changes nothing. A supersession and a retraction flow through the same path.
- **Embedders.** All tests use `fake`. One opt-in smoke test per real provider, skipped unless the provider's credentials are set.
- **MCP.** Tools exercised through an in-process MCP client over stdio, including each error shape from section 9.1.
- **GitLab adapter.** Tested against recorded REST fixtures for each endpoint. One opt-in live smoke test against a free gitlab.com project, skipped unless `GITLAB_TOKEN` is set.
- **Retrieval evaluation.** A fixture set of thirty findings and twenty queries with expected hits, reporting recall at three for full-text only, vector only, and fused. This is run by `tk eval retrieval` and its output is the evidence for whether semantic search earns its keep.

## 18. Tool repo

`~/Projects/team-knowledge`, Python package `teamknowledge`, CLI `tk`, managed with `uv`.

```
pyproject.toml
src/teamknowledge/
  model.py          Finding and Entity types; markdown + YAML header parse and serialise
  validate.py       JSON Schema, kinds rules, cross-file checks
  repo.py           locate the clone, read all files, diff since a commit, git operations
  githost/
    base.py         GitHost protocol and MergeRequest
    local.py        bare repo plus JSON sidecar
    gitlab.py       REST and push-option modes
  graph/
    schema.py       constraints and indexes
    sync.py         incremental and full sync
    queries.py      read queries and fusion
  embed/
    base.py  openai.py  bedrock.py  fake.py
  render/
    build.py  templates/
  mcp_server.py     the tools in section 9
  cli.py            tk init | validate | graph init | sync | render | review | push | eval | serve-mcp
skills/team-knowledge/SKILL.md
templates/knowledge-repo/
  schema/finding.schema.json  schema/entity.schema.json  schema/kinds.yaml
  config.yaml  .gitlab-ci.yml  README.md
  entities/system/example.md  findings/<example>.md
tests/
docs/superpowers/specs/
```

Dependencies: `pyyaml`, `jsonschema`, `neo4j`, `mcp`, `jinja2`, `markdown`, `requests`, `python-ulid`. Optional extras: `openai`, `bedrock` (boto3). Development: `pytest`, `testcontainers`, `ruff`. Frontmatter parsing is hand-rolled rather than another dependency. Nothing else.

`tk` commands:

| Command | Does |
|---|---|
| `tk init <dir>` | scaffold a knowledge repo from the template |
| `tk validate [--all \| <files>]` | run the validator; exit non-zero on any violation |
| `tk graph init` | create constraints and indexes for the configured embedder |
| `tk sync [--full] [--embed-missing]` | section 12 |
| `tk render --out <dir>` | section 13 |
| `tk review list`, `tk review approve <branch> --as <user>` | local adapter only |
| `tk push <branch>` | retry push and merge request creation for a committed branch |
| `tk eval retrieval` | section 17 |
| `tk serve-mcp` | run the MCP server over stdio |

## 19. Rebuilding at work

This is the order of operations for standing the system up inside the bank from this document.

1. Build the package from sections 5 through 18 and publish it to the internal package index.
2. Create the knowledge repo in GitLab with `tk init`. Protect `main`. Set the approval rule: one approval, authors cannot approve their own merge requests. Enable Pages. Set the CI variables from section 14.
3. Provision Neo4j Community 5.13 or later. Create a database user. Run `tk graph init` with `TK_EMBEDDER=bedrock`.
4. Seed entities: the team's projects, systems, environments, desks, and tools, with owners and aliases. This is the single most important step for retrieval precision, so do it as a team, in one sitting, as one merge request.
5. Each person installs the package, sets the environment variables from section 9, adds the MCP server to Claude Code, and installs the skill.
6. Record one finding through the skill and watch it travel: merge request, approval, sync, graph, wiki.

## 20. Roadmap: the later specs

1. **LLM maintenance.** A scheduled pass that reads the graph and proposes merge requests: overview prose on entity pages, candidate `contradicts` edges, findings past `review_after`, entity alias and duplicate suggestions, link suggestions between findings. Also the place to decide, from `tk eval retrieval` data, whether semantic search stays.
2. **Connectors.** Index the Confluence pages and GitLab artefacts that findings cite so the agent can read them in context; links into code and repositories.
3. **Multi-team.** Namespaces, per-team reviewers, cross-team visibility, and whether that is one knowledge repo with directories or several repos feeding one graph.

## 21. Decisions made during design

| Decision | Reason |
|---|---|
| GitLab repo is the source of truth; Neo4j is derived | Review is a merge request with an audit trail the bank already trusts; Neo4j Community's lack of access control stops mattering because the graph is rebuildable |
| Findings are schema-validated files, not prose | The user's requirement; structure is the difference between this and Confluence |
| Five kinds; `gotcha` renamed to `caveat` | Professional vocabulary |
| No free-form tags | Entities are the only taxonomy so scope stays controlled |
| Review metadata derived at sync, not in the file | The file is the author's statement |
| `contradicts` is a header field now | Humans can set it before automation exists |
| Embeddings kept, hybrid search with fusion | Questioned and confirmed; full-text alone would miss synonym matches |
| No SessionStart or UserPromptSubmit hooks | The user wants the skill invoked on purpose; interjection would be annoying |
| MCP server never writes to Neo4j | Keeps the single-writer property; only CI writes |
| MCP server runs per person with their own credentials | Authorship and permissions without building auth |
| Render reads files, not Neo4j | The wiki must build when the database is down |
| New repo, not knowledge-retrieval | This project is independent of alethograph |
| No dependency on graphify or kgc | The user intends to remove graphify once this exists |

## 22. Assumptions to verify at work

- The self-hosted Neo4j Community is version 5.13 or later, so vector indexes are available. If not, run with `TK_EMBEDDER=none` until it is upgraded.
- Personal access tokens with `api` scope are permitted. If not, use push-option mode.
- Amazon Titan Text Embeddings V2 is enabled in the bank's Bedrock account. If a different embedding model is mandated, only `embed/bedrock.py` and the dimension change.
- An internal package index exists for CI to install from. If not, vendor the wheel into the knowledge repo.
- GitLab Pages is enabled on the instance. If not, `tk render` output can be served from any static host.
