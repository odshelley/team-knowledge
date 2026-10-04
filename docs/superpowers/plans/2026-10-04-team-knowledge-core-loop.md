# Team Knowledge Core Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `teamknowledge` Python package and `tk` CLI that let a team capture structured findings from Claude Code as GitLab merge requests, sync accepted findings into Neo4j, query them from Claude Code, and browse them as a static wiki.

**Architecture:** A GitLab (or local bare) repo of schema-validated markdown files is the source of truth. A per-person MCP server validates proposals and opens merge requests; CI validates merge requests, and on merge runs `tk sync` (idempotent upsert into Neo4j with embeddings) and `tk render` (static site). Read tools query Neo4j only. The graph is a deterministic function of the files and is rebuildable from scratch.

**Tech Stack:** Python 3.12, `uv`, `pyyaml`, `jsonschema`, `neo4j` driver, `mcp` (FastMCP), `jinja2`, `markdown`, `requests`, `python-ulid`; optional `openai`, `boto3`; dev `pytest`, `pytest-asyncio`, `testcontainers[neo4j]`, `ruff`. Neo4j Community 5.13+ (tests use `neo4j:5.26-community`).

Spec: `docs/superpowers/specs/2026-10-04-team-knowledge-core-design.md`. Section numbers below refer to it.

## Global Constraints

- Python `>=3.12`. Package name `teamknowledge`, CLI entry point `tk`, repo `~/Projects/team-knowledge`, managed with `uv`. Run tests with `uv run pytest`.
- Runtime dependencies are exactly: `pyyaml`, `jsonschema`, `neo4j`, `mcp`, `jinja2`, `markdown`, `requests`, `python-ulid`. Optional extras: `openai` (`openai`), `bedrock` (`boto3`), `eval` (`testcontainers[neo4j]`). Nothing else. Frontmatter parsing is hand-rolled. CLI uses `argparse`.
- Neo4j features used must exist in Community 5.13+: uniqueness constraints, range indexes, full-text indexes, vector indexes. No existence or type constraints. The MCP server never writes to Neo4j; only `tk sync` does.
- Finding kinds: `dead-end`, `caveat`, `how-to`, `decision`, `fact`. Entity types: `project`, `system`, `environment`, `desk`, `tool`. No free-form tags.
- Finding id is a 26-character ULID matching `^[0-9A-HJKMNP-TV-Z]{26}$`. Entity ref is `<type>/<slug>` with slug matching `[a-z0-9][a-z0-9-]{0,62}`. Usernames match `[A-Za-z0-9._@-]+`.
- Body: only `##` headings from `schema/kinds.yaml`, in order, required ones non-empty, nothing before the first heading, at most 400 words. Entity body at most 300 words.
- Statuses `active`, `superseded`, `retracted`; the last two are terminal. `kind`, `author`, `created`, `id` never change.
- Branch names: `finding/<ulid>`, `amend/<ulid>-<6 hex>`, `retract/<ulid>`. Merge request title `[<kind>] <title>`.
- Environment variables (section 9): `TK_REPO`, `TK_GITHOST` (`gitlab`|`local`), `TK_GITLAB_MODE` (`api`|`push-options`), `GITLAB_URL`, `GITLAB_TOKEN`, `TK_AUTHOR`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` (default `neo4j`), `TK_EMBEDDER` (`openai`|`bedrock`|`fake`|`none`), `AWS_REGION`, `OPENAI_API_KEY`.
- Embedders: `bedrock` = `amazon.titan-embed-text-v2:0`, 1024 dims; `openai` = `text-embedding-3-small`, 1536 dims; `fake` = 64 dims deterministic; `none` = no vector index. Embedded text is `title + "\n" + claim + "\n" + body`.
- Hybrid search: full-text and vector lists of `3 * limit` candidates, reciprocal rank fusion with `k = 60`, active only, scope match multiplies fused score by `1.5`.
- Wiki paths: `index.html`, `findings/<ulid>.html`, `entities/<type>/<slug>.html`, `kinds/<kind>.html`, `sources.html`. No JavaScript, no external assets.
- Tests that need Docker are marked `@pytest.mark.neo4j` and skip when Docker is unavailable. Tests that need real credentials are marked `@pytest.mark.live` and skip unless the credential env var is set.
- Every commit message is imperative, prefixed `feat:`, `test:`, `docs:`, or `chore:`.

---

## File structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | package metadata, dependencies, `tk` entry point, pytest and ruff config |
| `src/teamknowledge/__init__.py` | version string |
| `src/teamknowledge/model.py` | `Finding`, `Entity`, `Evidence` dataclasses; frontmatter split; section parsing; parse and serialise; ULID and slug helpers |
| `src/teamknowledge/validate.py` | `Validator` (JSON Schema header checks, kinds body checks, entity checks, cross-file checks), `ValidationError`, `suggest_entity` |
| `src/teamknowledge/repo.py` | `KnowledgeRepo` (read files, git operations, diffs, load at ref), `init_knowledge_repo` scaffold |
| `src/teamknowledge/knowledge_template/` | starter knowledge repo: schema files, `kinds.yaml`, `config.yaml`, `gitlab-ci.yml`, `README.md`, one example entity and finding |
| `src/teamknowledge/githost/base.py` | `MergeRequest`, `GitHost` protocol, `GitHostError` |
| `src/teamknowledge/githost/local.py` | `LocalGitHost`: bare repo plus JSON sidecar, approve creates a real merge commit |
| `src/teamknowledge/githost/gitlab.py` | `GitLabHost`: REST and push-option modes |
| `src/teamknowledge/propose.py` | `Proposer`: propose, amend, retract, push retry; reviewer resolution; merge request text |
| `src/teamknowledge/embed/base.py` | `Embedder` protocol |
| `src/teamknowledge/embed/fake.py` | deterministic hash embedder |
| `src/teamknowledge/embed/openai.py` | OpenAI embedder |
| `src/teamknowledge/embed/bedrock.py` | Bedrock Titan embedder |
| `src/teamknowledge/embed/__init__.py` | `make_embedder(name)` factory |
| `src/teamknowledge/graph/client.py` | `GraphClient` thin wrapper over the neo4j driver |
| `src/teamknowledge/graph/schema.py` | constraint and index statements, `init_schema`, `wipe`, `drop_vector_index` |
| `src/teamknowledge/graph/sync.py` | `Syncer`: incremental and full sync, embed-missing, review metadata |
| `src/teamknowledge/graph/queries.py` | `Reader`: scope lookup, hybrid search, get, list entities; `lucene_escape`, `rrf` |
| `src/teamknowledge/render/build.py` | `render_site` |
| `src/teamknowledge/render/templates/*.html` | Jinja2 templates |
| `src/teamknowledge/settings.py` | `Settings.from_env`, factories for host, graph client, embedder |
| `src/teamknowledge/mcp_server.py` | `build_server(services)`, tools, stdio entry |
| `src/teamknowledge/eval_fixtures.py` | 30 findings and 20 queries for retrieval evaluation |
| `src/teamknowledge/eval_retrieval.py` | `run_eval` |
| `src/teamknowledge/cli.py` | `tk` argparse CLI |
| `skills/team-knowledge/SKILL.md` | Claude Code skill |
| `README.md` | install and use |
| `tests/conftest.py` | fixtures: `knowledge_repo`, `validator`, `neo4j_client`, `graph` |
| `tests/test_*.py` | one test module per source module |

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/teamknowledge/__init__.py`
- Create: `src/teamknowledge/cli.py`
- Create: `tests/test_package.py`
- Create: `tests/conftest.py` (empty for now)

**Interfaces:**
- Produces: `teamknowledge.__version__ == "0.1.0"`; `teamknowledge.cli.main(argv: list[str] | None = None) -> int` returning an exit code; `tk --version` prints `tk 0.1.0`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "teamknowledge"
version = "0.1.0"
description = "Structured, reviewed team findings for Claude Code, GitLab and Neo4j"
requires-python = ">=3.12"
dependencies = [
  "pyyaml>=6.0",
  "jsonschema>=4.23",
  "neo4j>=5.27",
  "mcp>=1.10",
  "jinja2>=3.1",
  "markdown>=3.7",
  "requests>=2.32",
  "python-ulid>=3.0",
]

[project.optional-dependencies]
openai = ["openai>=1.55"]
bedrock = ["boto3>=1.35"]
eval = ["testcontainers[neo4j]>=4.8"]

[project.scripts]
tk = "teamknowledge.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/teamknowledge"]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "testcontainers[neo4j]>=4.8",
  "ruff>=0.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "neo4j: needs a Neo4j container via Docker",
  "live: needs real external credentials",
]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Write the package init and CLI skeleton**

`src/teamknowledge/__init__.py`:

```python
"""Team Knowledge: structured, reviewed team findings."""

__version__ = "0.1.0"
```

`src/teamknowledge/cli.py`:

```python
"""The tk command line interface. Subcommands are added by later tasks."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tk", description="Team Knowledge")
    parser.add_argument("--version", action="version", version=f"tk {__version__}")
    parser.add_subparsers(dest="command")
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
```

- [ ] **Step 3: Write the failing test**

`tests/test_package.py`:

```python
import subprocess
import sys

import teamknowledge


def test_version_constant():
    assert teamknowledge.__version__ == "0.1.0"


def test_tk_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "teamknowledge.cli", "--version"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "tk 0.1.0"
```

- [ ] **Step 4: Install and run**

Run: `cd ~/Projects/team-knowledge && uv sync --all-extras && uv run pytest tests/test_package.py -v`
Expected: 2 passed. If `uv sync` fails on `testcontainers[neo4j]`, remove the extra from `dependency-groups.dev` temporarily, install, and restore it; the Neo4j tests skip without it.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/teamknowledge/__init__.py src/teamknowledge/cli.py tests/test_package.py tests/conftest.py
git commit -m "chore: scaffold teamknowledge package and tk CLI"
```

---

### Task 2: Model: parse and serialise findings and entities

**Files:**
- Create: `src/teamknowledge/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Produces:
  - `KINDS`, `ENTITY_TYPES` tuples; `class ParseError(ValueError)`.
  - `@dataclass Evidence(type: str, ref: str | None = None, note: str | None = None)` with `to_dict()`.
  - `@dataclass Finding(id, kind, title, claim, scope: list[str], evidence: list[Evidence], confidence, status, author, created: date, sections: dict[str, str], preamble: str = "", applies_when=None, supersedes: list[str], superseded_by=None, contradicts: list[str], retracted_reason=None, review_after: date | None = None, path: Path | None = None, raw_header: dict | None = None)` with `header() -> dict` (dates as `date` objects), `body() -> str`, `text_for_embedding() -> str`, `observations() -> list[str]`, `references() -> list[Evidence]`.
  - `@dataclass Entity(type, slug, name, description, aliases, owner, related, links: list[Evidence], body: str = "", path=None, dir_type=None, raw_header=None)` with `ref` property and `header()`.
  - `split_frontmatter(text) -> tuple[dict, str]`, `parse_sections(body) -> tuple[str, dict[str, str]]`, `parse_finding(text, path=None) -> Finding`, `parse_entity(text, path) -> Entity`, `serialize_finding(f) -> str`, `serialize_entity(e) -> str`, `new_ulid() -> str`, `slugify(title, max_len=60) -> str`, `finding_filename(id, title) -> str`, `entity_path(e) -> Path`, `jsonable(obj) -> obj` (dates to ISO strings, recursively).

- [ ] **Step 1: Write the failing tests**

`tests/test_model.py`:

```python
import re
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.model import (
    Evidence, Finding, Entity, ParseError, finding_filename, new_ulid, parse_entity,
    parse_finding, parse_sections, serialize_entity, serialize_finding, slugify,
    split_frontmatter,
)

EXAMPLE = """---
id: 01J9XK3M8Q7ZV2W1F4N6B5HT9D
kind: dead-end
title: Batch kdb+ queries over 10k symbols time out against the UAT gateway
claim: >
  Sending a single select over more than ~10k syms to the UAT kdb+ gateway
  hits the 30s gateway timeout; the gateway does not stream partial results.
scope:
  - system/kdb-gateway
  - environment/uat
applies_when: gateway version 4.x
evidence:
  - type: observation
    note: Reproduced 2026-10-04 with a 12k-symbol select, error 'timeout'.
  - type: confluence
    ref: https://confluence.bank/x/KDB-GW-LIMITS
confidence: observed
status: active
supersedes: []
contradicts: []
author: osian.shelley
created: 2026-10-04
review_after: 2027-04-01
---

## Approach

Issue one select over the full symbol list.

## Why it fails

The gateway enforces a 30 second timeout.

## Instead

Chunk the symbol list at 5k.
"""


def test_parse_finding_fields():
    f = parse_finding(EXAMPLE, Path("findings/01J9XK3M8Q7ZV2W1F4N6B5HT9D-batch.md"))
    assert f.id == "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
    assert f.kind == "dead-end"
    assert f.scope == ["system/kdb-gateway", "environment/uat"]
    assert f.created == date(2026, 10, 4)
    assert f.review_after == date(2027, 4, 1)
    assert f.evidence[0] == Evidence(type="observation", note="Reproduced 2026-10-04 with a 12k-symbol select, error 'timeout'.")
    assert f.evidence[1] == Evidence(type="confluence", ref="https://confluence.bank/x/KDB-GW-LIMITS")
    assert list(f.sections) == ["Approach", "Why it fails", "Instead"]
    assert f.sections["Why it fails"] == "The gateway enforces a 30 second timeout."
    assert f.preamble == ""
    assert f.path == Path("findings/01J9XK3M8Q7ZV2W1F4N6B5HT9D-batch.md")
    assert f.raw_header["created"] == "2026-10-04"


def test_round_trip_is_stable():
    f = parse_finding(EXAMPLE)
    text = serialize_finding(f)
    g = parse_finding(text)
    assert g == Finding(**{**f.__dict__, "raw_header": g.raw_header})
    assert serialize_finding(g) == text


def test_created_accepts_iso_string():
    text = EXAMPLE.replace("created: 2026-10-04", "created: '2026-10-04'")
    assert parse_finding(text).created == date(2026, 10, 4)


def test_missing_frontmatter_raises():
    with pytest.raises(ParseError):
        split_frontmatter("## Approach\n\nno header\n")


def test_unterminated_frontmatter_raises():
    with pytest.raises(ParseError):
        split_frontmatter("---\nid: x\n")


def test_parse_sections_preamble_and_order():
    pre, sections = parse_sections("stray text\n\n## A\n\none\n\n## B\n\ntwo\n")
    assert pre == "stray text"
    assert sections == {"A": "one", "B": "two"}


def test_duplicate_section_raises():
    with pytest.raises(ParseError):
        parse_sections("## A\n\nx\n\n## A\n\ny\n")


def test_text_for_embedding():
    f = parse_finding(EXAMPLE)
    assert f.text_for_embedding().startswith(f.title + "\n" + f.claim + "\n## Approach")


def test_observations_and_references():
    f = parse_finding(EXAMPLE)
    assert f.observations() == ["Reproduced 2026-10-04 with a 12k-symbol select, error 'timeout'."]
    assert [e.type for e in f.references()] == ["confluence"]


ENTITY = """---
type: system
name: kdb+ gateway
aliases: [kdb gateway, gw]
description: The q process that fronts the tick databases for the pricing desk.
owner: [jane.doe]
related:
  - project/gamma
links:
  - type: confluence
    ref: https://confluence.bank/x/KDB-GW
---

Extra context.
"""


def test_parse_entity():
    e = parse_entity(ENTITY, Path("entities/system/kdb-gateway.md"))
    assert e.ref == "system/kdb-gateway"
    assert e.slug == "kdb-gateway"
    assert e.dir_type == "system"
    assert e.aliases == ["kdb gateway", "gw"]
    assert e.owner == ["jane.doe"]
    assert e.related == ["project/gamma"]
    assert e.links == [Evidence(type="confluence", ref="https://confluence.bank/x/KDB-GW")]
    assert e.body == "Extra context."


def test_entity_round_trip():
    e = parse_entity(ENTITY, Path("entities/system/kdb-gateway.md"))
    text = serialize_entity(e)
    g = parse_entity(text, Path("entities/system/kdb-gateway.md"))
    assert g.header() == e.header()
    assert g.body == e.body


def test_new_ulid_matches_pattern():
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", new_ulid())
    assert new_ulid() != new_ulid()


def test_slugify():
    assert slugify("Batch kdb+ queries over 10k symbols!") == "batch-kdb-queries-over-10k-symbols"
    assert len(slugify("x" * 200)) <= 60
    assert slugify("???") == "finding"


def test_finding_filename():
    assert finding_filename("01J9XK3M8Q7ZV2W1F4N6B5HT9D", "A Title") == "01J9XK3M8Q7ZV2W1F4N6B5HT9D-a-title.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.model'`

- [ ] **Step 3: Write model.py**

```python
"""Finding and Entity types; markdown + YAML header parse and serialise."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from ulid import ULID

KINDS = ("dead-end", "caveat", "how-to", "decision", "fact")
ENTITY_TYPES = ("project", "system", "environment", "desk", "tool")


class ParseError(ValueError):
    """A file could not be parsed into a Finding or Entity."""


def jsonable(obj: Any) -> Any:
    """Recursively convert dates to ISO strings so the object matches the JSON Schema."""
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [jsonable(v) for v in obj]
    return obj


@dataclass
class Evidence:
    type: str
    ref: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"type": self.type}
        if self.ref is not None:
            d["ref"] = self.ref
        if self.note is not None:
            d["note"] = self.note
        return d


@dataclass
class Finding:
    id: str
    kind: str
    title: str
    claim: str
    scope: list[str]
    evidence: list[Evidence]
    confidence: str
    status: str
    author: str
    created: date
    sections: dict[str, str] = field(default_factory=dict)
    preamble: str = ""
    applies_when: str | None = None
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    contradicts: list[str] = field(default_factory=list)
    retracted_reason: str | None = None
    review_after: date | None = None
    path: Path | None = None
    raw_header: dict | None = None

    def header(self) -> dict[str, Any]:
        h: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "claim": self.claim,
            "scope": list(self.scope),
        }
        if self.applies_when:
            h["applies_when"] = self.applies_when
        h["evidence"] = [e.to_dict() for e in self.evidence]
        h["confidence"] = self.confidence
        h["status"] = self.status
        h["supersedes"] = list(self.supersedes)
        if self.superseded_by:
            h["superseded_by"] = self.superseded_by
        h["contradicts"] = list(self.contradicts)
        if self.retracted_reason:
            h["retracted_reason"] = self.retracted_reason
        h["author"] = self.author
        h["created"] = self.created
        if self.review_after:
            h["review_after"] = self.review_after
        return h

    def body(self) -> str:
        return "\n".join(f"## {heading}\n\n{text.strip()}\n" for heading, text in self.sections.items())

    def text_for_embedding(self) -> str:
        return f"{self.title}\n{self.claim}\n{self.body()}"

    def observations(self) -> list[str]:
        return [e.note for e in self.evidence if e.type == "observation" and e.note]

    def references(self) -> list[Evidence]:
        return [e for e in self.evidence if e.type != "observation"]


@dataclass
class Entity:
    type: str
    slug: str
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    owner: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    links: list[Evidence] = field(default_factory=list)
    body: str = ""
    path: Path | None = None
    dir_type: str | None = None
    raw_header: dict | None = None

    @property
    def ref(self) -> str:
        return f"{self.type}/{self.slug}"

    def header(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "owner": list(self.owner),
            "related": list(self.related),
            "links": [link.to_dict() for link in self.links],
        }


_H2 = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ParseError("missing YAML front matter (file must start with '---')")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            end = len(text) - 4
            body = ""
        else:
            raise ParseError("unterminated YAML front matter")
    else:
        body = text[end + 5 :]
    try:
        header = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise ParseError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(header, dict):
        raise ParseError("front matter must be a mapping")
    return header, body


def parse_sections(body: str) -> tuple[str, dict[str, str]]:
    matches = list(_H2.finditer(body))
    preamble = (body[: matches[0].start()] if matches else body).strip()
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        heading = m.group(1).strip()
        if heading in sections:
            raise ParseError(f"duplicate section '{heading}'")
        sections[heading] = body[m.end() : end].strip()
    return preamble, sections


def _coerce_date(value: Any, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ParseError(f"{field_name}: not an ISO date: {value!r}") from exc
    raise ParseError(f"{field_name}: expected a date, got {type(value).__name__}")


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _evidence(value: Any) -> Evidence:
    if isinstance(value, dict):
        ref = value.get("ref")
        note = value.get("note")
        return Evidence(
            type=str(value.get("type", "")),
            ref=None if ref is None else str(ref),
            note=None if note is None else str(note),
        )
    return Evidence(type=str(value))


def parse_finding(text: str, path: Path | None = None) -> Finding:
    header, body = split_frontmatter(text)
    preamble, sections = parse_sections(body)
    created = header.get("created")
    review_after = header.get("review_after")
    return Finding(
        id=str(header.get("id", "")),
        kind=str(header.get("kind", "")),
        title=str(header.get("title", "")).strip(),
        claim=str(header.get("claim", "")).strip(),
        scope=_str_list(header.get("scope")),
        evidence=[_evidence(e) for e in _str_list_raw(header.get("evidence"))],
        confidence=str(header.get("confidence", "")),
        status=str(header.get("status", "")),
        author=str(header.get("author", "")),
        created=_coerce_date(created, "created") if created is not None else date.min,
        sections=sections,
        preamble=preamble,
        applies_when=header.get("applies_when"),
        supersedes=_str_list(header.get("supersedes")),
        superseded_by=header.get("superseded_by"),
        contradicts=_str_list(header.get("contradicts")),
        retracted_reason=header.get("retracted_reason"),
        review_after=_coerce_date(review_after, "review_after") if review_after is not None else None,
        path=path,
        raw_header=jsonable(header),
    )


def _str_list_raw(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_entity(text: str, path: Path) -> Entity:
    header, body = split_frontmatter(text)
    path = Path(path)
    return Entity(
        type=str(header.get("type", "")),
        slug=path.stem,
        name=str(header.get("name", "")).strip(),
        description=str(header.get("description", "")).strip(),
        aliases=_str_list(header.get("aliases")),
        owner=_str_list(header.get("owner")),
        related=_str_list(header.get("related")),
        links=[_evidence(link) for link in _str_list_raw(header.get("links"))],
        body=body.strip(),
        path=path,
        dir_type=path.parent.name,
        raw_header=jsonable(header),
    )


def _dump_header(header: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True, width=88) + "---\n"


def serialize_finding(f: Finding) -> str:
    return _dump_header(f.header()) + "\n" + f.body()


def serialize_entity(e: Entity) -> str:
    text = _dump_header(e.header())
    if e.body:
        text += "\n" + e.body.strip() + "\n"
    return text


def new_ulid() -> str:
    return str(ULID())


def slugify(title: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    s = s[:max_len].rstrip("-")
    return s or "finding"


def finding_filename(finding_id: str, title: str) -> str:
    return f"{finding_id}-{slugify(title)}.md"


def entity_path(e: Entity) -> Path:
    return Path("entities") / e.type / f"{e.slug}.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model.py -v`
Expected: all pass. If `test_round_trip_is_stable` fails on the `claim` folding, YAML has re-wrapped the folded scalar; the second serialisation must still equal the first, which is what the assertion checks, so compare the two serialised strings in the failure output and adjust `width` only if they differ.

- [ ] **Step 5: Commit**

```bash
git add src/teamknowledge/model.py tests/test_model.py
git commit -m "feat: finding and entity model with parse and serialise"
```

---

### Task 3: Validator and the schema files

**Files:**
- Create: `src/teamknowledge/knowledge_template/schema/finding.schema.json`
- Create: `src/teamknowledge/knowledge_template/schema/entity.schema.json`
- Create: `src/teamknowledge/knowledge_template/schema/kinds.yaml`
- Create: `src/teamknowledge/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `Finding`, `Entity`, `jsonable` from `teamknowledge.model`.
- Produces:
  - `@dataclass(frozen=True) ValidationError(field: str, message: str, suggestion: str | None = None, path: str | None = None)` with `to_dict()`.
  - `class Validator(schema_dir: Path)` with `validate_finding_header(f) -> list[ValidationError]`, `validate_finding_body(f)`, `validate_entity(e)`, `validate_corpus(findings, entities, previous: dict[str, Finding] | None = None)`, `validate_all(findings, entities, previous=None)`.
  - `suggest_entity(ref: str, entities: list[Entity]) -> str`.
  - `TERMINAL = frozenset({"superseded", "retracted"})`.

- [ ] **Step 1: Write the schema files**

`src/teamknowledge/knowledge_template/schema/finding.schema.json`: copy verbatim from spec section 5.6, first JSON block.

`src/teamknowledge/knowledge_template/schema/entity.schema.json`: copy verbatim from spec section 5.6, second JSON block.

`src/teamknowledge/knowledge_template/schema/kinds.yaml`: copy verbatim from spec section 5.2.

Verify each loads: `uv run python -c "import json,yaml,pathlib as p; d=p.Path('src/teamknowledge/knowledge_template/schema'); json.loads((d/'finding.schema.json').read_text()); json.loads((d/'entity.schema.json').read_text()); print(yaml.safe_load((d/'kinds.yaml').read_text())['max_body_words'])"`
Expected: `400`

- [ ] **Step 2: Write the failing tests**

`tests/test_validate.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.model import Entity, Evidence, Finding
from teamknowledge.validate import Validator, suggest_entity

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "src/teamknowledge/knowledge_template/schema"
FID = "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
FID2 = "01J9XK3M8Q7ZV2W1F4N6B5HT9E"


@pytest.fixture
def v() -> Validator:
    return Validator(SCHEMA_DIR)


def entity(type="system", slug="kdb-gateway", **kw) -> Entity:
    base = dict(type=type, slug=slug, name="kdb+ gateway", description="The q process that fronts the tick databases.",
                path=Path(f"entities/{type}/{slug}.md"), dir_type=type)
    base.update(kw)
    return Entity(**base)


def finding(**kw) -> Finding:
    base = dict(
        id=FID, kind="dead-end", title="Batch queries over 10k symbols time out",
        claim="Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        scope=["system/kdb-gateway"], evidence=[Evidence("observation", note="Reproduced on 2026-10-04 with 12k symbols.")],
        confidence="observed", status="active", author="osian.shelley", created=date(2026, 10, 4),
        sections={"Approach": "One select.", "Why it fails": "Timeout."},
        path=Path(f"findings/{FID}-batch.md"),
    )
    base.update(kw)
    return Finding(**base)


def fields(errors):
    return [e.field for e in errors]


def test_valid_finding_has_no_errors(v):
    assert v.validate_all([finding()], [entity()]) == []


def test_missing_required_header_field(v):
    errs = v.validate_finding_header(finding(claim=""))
    assert any(e.field == "claim" for e in errs)


def test_bad_kind_reported_by_schema(v):
    assert "kind" in fields(v.validate_finding_header(finding(kind="gotcha")))


def test_bad_id_pattern(v):
    assert "id" in fields(v.validate_finding_header(finding(id="not-a-ulid")))


def test_evidence_observation_needs_note(v):
    errs = v.validate_finding_header(finding(evidence=[Evidence("observation")]))
    assert any(e.field.startswith("evidence") for e in errs)


def test_superseded_requires_superseded_by(v):
    assert "superseded_by" in fields(v.validate_finding_header(finding(status="superseded")))


def test_superseded_by_only_when_superseded(v):
    assert "superseded_by" in fields(v.validate_finding_header(finding(superseded_by=FID2)))


def test_retracted_requires_reason(v):
    assert "retracted_reason" in fields(v.validate_finding_header(finding(status="retracted")))


def test_missing_required_section(v):
    errs = v.validate_finding_body(finding(sections={"Approach": "x"}))
    assert "body.Why it fails" in fields(errs)


def test_section_not_allowed_for_kind(v):
    errs = v.validate_finding_body(finding(sections={"Approach": "x", "Why it fails": "y", "Steps": "z"}))
    assert "body.Steps" in fields(errs)


def test_sections_out_of_order(v):
    errs = v.validate_finding_body(finding(sections={"Why it fails": "y", "Approach": "x"}))
    assert any(e.message == "sections out of order" for e in errs)


def test_preamble_rejected(v):
    errs = v.validate_finding_body(finding(preamble="stray"))
    assert any("before the first section" in e.message for e in errs)


def test_other_heading_levels_rejected(v):
    errs = v.validate_finding_body(finding(sections={"Approach": "### sub\nx", "Why it fails": "y"}))
    assert any("level-two" in e.message for e in errs)


def test_body_word_limit(v):
    errs = v.validate_finding_body(finding(sections={"Approach": "word " * 300, "Why it fails": "word " * 200}))
    assert any("limit is 400" in e.message for e in errs)


def test_entity_type_dir_mismatch(v):
    e = entity(dir_type="project")
    assert "type" in fields(v.validate_entity(e))


def test_entity_body_word_limit(v):
    assert "body" in fields(v.validate_entity(entity(body="w " * 301)))


def test_entity_schema_error(v):
    assert "description" in fields(v.validate_entity(entity(description="short")))


def test_unresolved_scope_ref_with_suggestion(v):
    errs = v.validate_corpus([finding(scope=["system/kdb-gw"])], [entity(aliases=["gw"])])
    assert errs[0].field == "scope[0]"
    assert "system/kdb-gateway" in errs[0].suggestion


def test_unresolved_related_ref(v):
    errs = v.validate_corpus([], [entity(related=["project/nope"])])
    assert errs[0].field == "related[0]"


def test_duplicate_id(v):
    errs = v.validate_corpus([finding(), finding(path=Path(f"findings/{FID}-other.md"))], [entity()])
    assert any("duplicate id" in e.message for e in errs)


def test_filename_must_start_with_id(v):
    errs = v.validate_corpus([finding(path=Path("findings/wrong.md"))], [entity()])
    assert "path" in fields(errs)


def test_supersede_pair_consistent(v):
    old = finding(id=FID2, status="superseded", superseded_by=FID, path=Path(f"findings/{FID2}-old.md"))
    new = finding(supersedes=[FID2])
    assert v.validate_corpus([old, new], [entity()]) == []


def test_supersede_target_must_be_marked(v):
    old = finding(id=FID2, path=Path(f"findings/{FID2}-old.md"))
    new = finding(supersedes=[FID2])
    assert "supersedes" in fields(v.validate_corpus([old, new], [entity()]))


def test_superseded_by_must_point_back(v):
    old = finding(id=FID2, status="superseded", superseded_by=FID, path=Path(f"findings/{FID2}-old.md"))
    new = finding()
    assert "superseded_by" in fields(v.validate_corpus([old, new], [entity()]))


def test_supersede_chain_allowed(v):
    a = finding(id="01J9XK3M8Q7ZV2W1F4N6B5HT9A", status="superseded", superseded_by=FID2, path=Path("findings/01J9XK3M8Q7ZV2W1F4N6B5HT9A-a.md"))
    b = finding(id=FID2, status="superseded", superseded_by=FID, supersedes=["01J9XK3M8Q7ZV2W1F4N6B5HT9A"], path=Path(f"findings/{FID2}-b.md"))
    c = finding(supersedes=[FID2])
    assert v.validate_corpus([a, b, c], [entity()]) == []


def test_contradicts_target_must_exist(v):
    assert "contradicts" in fields(v.validate_corpus([finding(contradicts=[FID2])], [entity()]))


def test_terminal_status_cannot_move(v):
    previous = {FID: finding(status="retracted", retracted_reason="was wrong about it")}
    errs = v.validate_corpus([finding()], [entity()], previous)
    assert "status" in fields(errs)


def test_immutable_fields_vs_previous(v):
    previous = {FID: finding(author="someone.else")}
    errs = v.validate_corpus([finding()], [entity()], previous)
    assert "author" in fields(errs)


def test_suggest_entity_falls_back():
    assert "new_entities" in suggest_entity("system/zzz", [])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.validate'`

- [ ] **Step 4: Write validate.py**

```python
"""JSON Schema header checks, kinds body checks, entity checks, and cross-file checks."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .model import Entity, Finding, jsonable

TERMINAL = frozenset({"superseded", "retracted"})
IMMUTABLE = ("kind", "author", "created")
_OTHER_HEADING = re.compile(r"^(#|#{3,6}) ", re.MULTILINE)
MAX_ENTITY_BODY_WORDS = 300


@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str
    suggestion: str | None = None
    path: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _p(obj: Finding | Entity) -> str | None:
    return str(obj.path) if obj.path is not None else None


def _schema_field(error) -> str:
    parts = [str(p) for p in error.absolute_path]
    if not parts:
        return "header"
    out = parts[0]
    for part in parts[1:]:
        out += f"[{part}]" if part.isdigit() else f".{part}"
    return out


class Validator:
    def __init__(self, schema_dir: Path):
        schema_dir = Path(schema_dir)
        checker = Draft202012Validator.FORMAT_CHECKER
        self.finding_schema = Draft202012Validator(
            json.loads((schema_dir / "finding.schema.json").read_text()), format_checker=checker)
        self.entity_schema = Draft202012Validator(
            json.loads((schema_dir / "entity.schema.json").read_text()), format_checker=checker)
        kinds = yaml.safe_load((schema_dir / "kinds.yaml").read_text())
        self.kinds: dict = kinds["kinds"]
        self.max_body_words: int = int(kinds["max_body_words"])

    # --- single file -------------------------------------------------------

    def validate_finding_header(self, f: Finding) -> list[ValidationError]:
        header = f.raw_header if f.raw_header is not None else jsonable(f.header())
        errors = sorted(self.finding_schema.iter_errors(header), key=lambda e: list(e.absolute_path))
        out = [ValidationError(_schema_field(e), e.message, path=_p(f)) for e in errors]
        if f.superseded_by and f.status != "superseded":
            out.append(ValidationError("superseded_by", "only allowed when status is superseded", path=_p(f)))
        return out

    def validate_finding_body(self, f: Finding) -> list[ValidationError]:
        spec = self.kinds.get(f.kind)
        if spec is None:
            return []  # the schema already reported the bad kind
        out: list[ValidationError] = []
        path = _p(f)
        allowed = [s["heading"] for s in spec["sections"]]
        if f.preamble:
            out.append(ValidationError("body", "content before the first section heading is not allowed", path=path))
        if _OTHER_HEADING.search(f.body()):
            out.append(ValidationError("body", "only level-two headings from the kind's section list are allowed", path=path))
        for heading in f.sections:
            if heading not in allowed:
                out.append(ValidationError(f"body.{heading}", f"section not allowed for kind '{f.kind}'",
                                           suggestion=f"allowed: {', '.join(allowed)}", path=path))
        for s in spec["sections"]:
            if s["required"] and not f.sections.get(s["heading"], "").strip():
                out.append(ValidationError(f"body.{s['heading']}", "required section missing or empty", path=path))
        present_in_order = [h for h in allowed if h in f.sections]
        present_as_written = [h for h in f.sections if h in allowed]
        if present_in_order != present_as_written:
            out.append(ValidationError("body", "sections out of order", suggestion=f"order: {', '.join(allowed)}", path=path))
        words = sum(len(t.split()) for t in f.sections.values())
        if words > self.max_body_words:
            out.append(ValidationError("body", f"body has {words} words; limit is {self.max_body_words}", path=path))
        return out

    def validate_entity(self, e: Entity) -> list[ValidationError]:
        header = e.raw_header if e.raw_header is not None else jsonable(e.header())
        errors = sorted(self.entity_schema.iter_errors(header), key=lambda x: list(x.absolute_path))
        out = [ValidationError(_schema_field(x), x.message, path=_p(e)) for x in errors]
        if e.dir_type is not None and e.dir_type != e.type:
            out.append(ValidationError("type", f"header type '{e.type}' does not match directory '{e.dir_type}'", path=_p(e)))
        if len(e.body.split()) > MAX_ENTITY_BODY_WORDS:
            out.append(ValidationError("body", f"entity body exceeds {MAX_ENTITY_BODY_WORDS} words", path=_p(e)))
        return out

    # --- whole corpus ------------------------------------------------------

    def validate_corpus(self, findings: list[Finding], entities: list[Entity],
                        previous: dict[str, Finding] | None = None) -> list[ValidationError]:
        out: list[ValidationError] = []
        refs = {e.ref for e in entities}
        by_id: dict[str, Finding] = {}
        for f in findings:
            if f.id in by_id:
                out.append(ValidationError("id", f"duplicate id {f.id}", path=_p(f)))
            else:
                by_id[f.id] = f
            if f.path is not None and not f.path.stem.startswith(f.id):
                out.append(ValidationError("path", "filename must start with the finding id", path=_p(f)))
            for i, ref in enumerate(f.scope):
                if ref not in refs:
                    out.append(ValidationError(f"scope[{i}]", f"entity '{ref}' does not exist",
                                               suggestion=suggest_entity(ref, entities), path=_p(f)))
        for e in entities:
            for i, ref in enumerate(e.related):
                if ref not in refs:
                    out.append(ValidationError(f"related[{i}]", f"entity '{ref}' does not exist",
                                               suggestion=suggest_entity(ref, entities), path=_p(e)))
        for f in findings:
            for sid in f.supersedes:
                target = by_id.get(sid)
                if target is None:
                    out.append(ValidationError("supersedes", f"finding {sid} does not exist", path=_p(f)))
                elif target.status != "superseded" or target.superseded_by != f.id:
                    out.append(ValidationError("supersedes",
                        f"finding {sid} must have status superseded and superseded_by {f.id}", path=_p(f)))
            if f.superseded_by:
                target = by_id.get(f.superseded_by)
                if target is None or f.id not in target.supersedes:
                    out.append(ValidationError("superseded_by",
                        f"finding {f.superseded_by} must list {f.id} in supersedes", path=_p(f)))
            for cid in f.contradicts:
                if cid not in by_id:
                    out.append(ValidationError("contradicts", f"finding {cid} does not exist", path=_p(f)))
        if previous:
            for f in findings:
                prev = previous.get(f.id)
                if prev is None:
                    continue
                if prev.status in TERMINAL and f.status != prev.status:
                    out.append(ValidationError("status", f"{prev.status} is terminal; write a new finding instead", path=_p(f)))
                for attr in IMMUTABLE:
                    if getattr(prev, attr) != getattr(f, attr):
                        out.append(ValidationError(attr, f"{attr} cannot change; it was {getattr(prev, attr)}", path=_p(f)))
        return out

    def validate_all(self, findings: list[Finding], entities: list[Entity],
                     previous: dict[str, Finding] | None = None) -> list[ValidationError]:
        out: list[ValidationError] = []
        for f in findings:
            out += self.validate_finding_header(f)
            out += self.validate_finding_body(f)
        for e in entities:
            out += self.validate_entity(e)
        out += self.validate_corpus(findings, entities, previous)
        return out


def suggest_entity(ref: str, entities: list[Entity]) -> str:
    want = ref.split("/", 1)[-1].lower()
    best: tuple[Entity, str] | None = None
    best_score = 0.0
    for e in entities:
        for cand in [e.slug, e.name, *e.aliases]:
            score = difflib.SequenceMatcher(None, want, cand.lower().replace(" ", "-")).ratio()
            if score > best_score:
                best_score, best = score, (e, cand)
    if best is None or best_score < 0.6:
        return "add it in new_entities if it is genuinely new"
    e, cand = best
    via = "" if cand == e.slug else f" (matched '{cand}')"
    return f"closest existing: {e.ref}{via}; or add it in new_entities"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate.py tests/test_model.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/knowledge_template/schema src/teamknowledge/validate.py tests/test_validate.py
git commit -m "feat: validator with schema, kinds, and cross-file checks"
```

---

### Task 4: Knowledge repo, template, `tk init`, `tk validate`

**Files:**
- Create: `src/teamknowledge/knowledge_template/config.yaml`
- Create: `src/teamknowledge/knowledge_template/gitlab-ci.yml`
- Create: `src/teamknowledge/knowledge_template/README.md`
- Create: `src/teamknowledge/knowledge_template/entities/system/example-system.md`
- Create: `src/teamknowledge/knowledge_template/findings/01J9XK3M8Q7ZV2W1F4N6B5HT9D-example-dead-end.md`
- Create: `src/teamknowledge/repo.py`
- Modify: `src/teamknowledge/cli.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_repo.py`

**Interfaces:**
- Consumes: `parse_finding`, `parse_entity`, `serialize_finding`, `serialize_entity`, `finding_filename`, `entity_path` from `model`; `Validator`, `ValidationError` from `validate`.
- Produces:
  - `class GitError(RuntimeError)`.
  - `@dataclass Change(status: str, path: Path, old_path: Path | None = None)` where status is one of `A M D R`.
  - `class KnowledgeRepo(root: Path)` with attributes `root`, `findings_dir`, `entities_dir`, `schema_dir`; property `config -> dict`; methods `git(*args, check=True, env=None) -> str`, `load_findings() -> list[Finding]`, `load_entities() -> list[Entity]`, `load_with_errors() -> tuple[list[Finding], list[Entity], list[ValidationError]]`, `load_at(ref) -> tuple[list[Finding], list[Entity]]`, `changed_files(base, head="HEAD") -> list[Change]`, `head_sha() -> str`, `remote_url() -> str`, `sync_main(branch="main")`, `create_branch(name, start="origin/main")`, `write_finding(f) -> Path`, `write_entity(e) -> Path`, `commit_files(paths, message) -> str`, `push(branch, push_options=())`, `last_commit_for(path) -> str | None`, `show(ref, path) -> str`, `ls_tree(ref, prefix) -> list[str]`.
  - `init_knowledge_repo(dest: Path, local_remote: Path | None = None, author: str = "tk") -> KnowledgeRepo`.
  - `template_dir() -> Path`.
  - CLI: `tk init <dir> [--local-remote PATH]`, `tk validate [--all] [--repo PATH] [--base REF] [files...]` (`--all` is the default and is accepted so CI reads clearly). Every later subcommand takes `--repo PATH` defaulting to the current directory.
- `tests/conftest.py` produces fixture `knowledge_repo` (a clone with a local bare remote, author `alice`) and `validator`.

- [ ] **Step 1: Write the template files**

`src/teamknowledge/knowledge_template/config.yaml`:

```yaml
schema_version: 1
gitlab:
  project: group/team-knowledge      # path with namespace; edit this
  target_branch: main
review:
  default_reviewers: []              # GitLab usernames, used when no scoped entity has an owner
wiki:
  base_url: https://example.invalid/team-knowledge
```

`src/teamknowledge/knowledge_template/gitlab-ci.yml`: copy verbatim from spec section 14.

`src/teamknowledge/knowledge_template/README.md`:

```markdown
# Team knowledge

Structured findings for this team. Each finding is one file in `findings/`, scoped by
entities in `entities/`. Changes arrive as merge requests, one approval from someone
other than the author, then CI syncs the graph and publishes the wiki.

- Record or look up findings from Claude Code with the `team-knowledge` skill.
- Validate locally: `tk validate`.
- Edit `config.yaml` for the GitLab project path, default reviewers, and wiki URL.
- Delete the example entity and finding once you have real ones.
```

`src/teamknowledge/knowledge_template/entities/system/example-system.md`:

```markdown
---
type: system
name: Example system
aliases: [example]
description: >
  A placeholder entity so the template validates. Replace it with your team's
  real projects, systems, environments, desks, and tools.
owner: []
related: []
links: []
---
```

`src/teamknowledge/knowledge_template/findings/01J9XK3M8Q7ZV2W1F4N6B5HT9D-example-dead-end.md`:

```markdown
---
id: 01J9XK3M8Q7ZV2W1F4N6B5HT9D
kind: dead-end
title: Example finding showing the dead-end shape
claim: >
  This placeholder exists so the template validates and renders; delete it
  once the team has recorded its first real finding.
scope:
  - system/example-system
evidence:
  - type: observation
    note: Written by hand on 2026-10-04 as part of the template.
confidence: observed
status: active
supersedes: []
contradicts: []
author: example.author
created: 2026-10-04
---

## Approach

Keep an empty knowledge repo and hope people fill it in.

## Why it fails

Nobody records the first finding without an example of what one looks like.

## Instead

Record something you learned this week, then delete this file.
```

- [ ] **Step 2: Write the failing tests**

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

from teamknowledge.repo import KnowledgeRepo, init_knowledge_repo
from teamknowledge.validate import Validator


@pytest.fixture
def knowledge_repo(tmp_path: Path) -> KnowledgeRepo:
    return init_knowledge_repo(tmp_path / "clone", local_remote=tmp_path / "remote.git", author="alice")


@pytest.fixture
def validator(knowledge_repo: KnowledgeRepo) -> Validator:
    return Validator(knowledge_repo.schema_dir)
```

`tests/test_repo.py`:

```python
from datetime import date
from pathlib import Path

from teamknowledge.cli import main
from teamknowledge.model import Entity, Evidence, Finding
from teamknowledge.repo import KnowledgeRepo, init_knowledge_repo, template_dir


def test_template_dir_has_schema():
    assert (template_dir() / "schema" / "kinds.yaml").exists()


def test_init_scaffolds_and_pushes(tmp_path):
    repo = init_knowledge_repo(tmp_path / "c", local_remote=tmp_path / "r.git", author="alice")
    assert (repo.root / ".gitlab-ci.yml").exists()
    assert not (repo.root / "gitlab-ci.yml").exists()
    assert repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert repo.git("rev-parse", "origin/main").strip() == repo.head_sha()
    assert repo.remote_url() == str((tmp_path / "r.git").resolve())


def test_template_validates(knowledge_repo, validator):
    findings, entities, parse_errors = knowledge_repo.load_with_errors()
    assert parse_errors == []
    assert validator.validate_all(findings, entities) == []
    assert [e.ref for e in entities] == ["system/example-system"]
    assert len(findings) == 1


def test_config_loaded(knowledge_repo):
    assert knowledge_repo.config["review"]["default_reviewers"] == []


def test_write_commit_diff_and_load_at(knowledge_repo):
    e = Entity(type="project", slug="gamma", name="Gamma", description="The gamma pricing project, a placeholder.")
    path = knowledge_repo.write_entity(e)
    assert path == Path("entities/project/gamma.md")
    base = knowledge_repo.head_sha()
    knowledge_repo.commit_files([path], "add gamma")
    changes = knowledge_repo.changed_files(base)
    assert [(c.status, c.path) for c in changes] == [("A", Path("entities/project/gamma.md"))]
    old_f, old_e = knowledge_repo.load_at(base)
    assert [x.ref for x in old_e] == ["system/example-system"]
    assert len(old_f) == 1


def test_write_finding_sets_path(knowledge_repo):
    f = Finding(id="01J9XK3M8Q7ZV2W1F4N6B5HT9F", kind="fact", title="Prod gateway timeout is 120s",
                claim="The production gateway timeout is one hundred and twenty seconds, not thirty.",
                scope=["system/example-system"], evidence=[Evidence("observation", note="Read from the gateway config on 2026-10-04.")],
                confidence="observed", status="active", author="alice", created=date(2026, 10, 4),
                sections={"Detail": "120s.", "How to check": "Read the config."})
    path = knowledge_repo.write_finding(f)
    assert path == Path("findings/01J9XK3M8Q7ZV2W1F4N6B5HT9F-prod-gateway-timeout-is-120s.md")
    assert f.path == path


def test_branch_push_and_last_commit(knowledge_repo):
    knowledge_repo.create_branch("finding/x")
    p = knowledge_repo.root / "findings" / "note.txt"
    p.write_text("x")
    knowledge_repo.commit_files([Path("findings/note.txt")], "note")
    knowledge_repo.push("finding/x")
    assert knowledge_repo.git("rev-parse", "origin/finding/x").strip() == knowledge_repo.head_sha()
    assert knowledge_repo.last_commit_for(Path("findings/note.txt")) == knowledge_repo.head_sha()
    knowledge_repo.sync_main()
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_load_with_errors_reports_bad_file(knowledge_repo):
    (knowledge_repo.root / "findings" / "01J9XK3M8Q7ZV2W1F4N6B5HT9G-bad.md").write_text("no front matter\n")
    _, _, errors = knowledge_repo.load_with_errors()
    assert errors[0].field == "file"
    assert errors[0].path.endswith("bad.md")


def test_cli_init_and_validate(tmp_path, capsys):
    assert main(["init", str(tmp_path / "kr"), "--local-remote", str(tmp_path / "kr.git")]) == 0
    assert main(["validate", "--repo", str(tmp_path / "kr")]) == 0
    out = capsys.readouterr().out
    assert "1 findings, 1 entities, 0 errors" in out


def test_cli_validate_fails_on_error(tmp_path, capsys):
    main(["init", str(tmp_path / "kr"), "--local-remote", str(tmp_path / "kr.git")])
    bad = tmp_path / "kr" / "findings" / "01J9XK3M8Q7ZV2W1F4N6B5HT9H-bad.md"
    bad.write_text("---\nid: 01J9XK3M8Q7ZV2W1F4N6B5HT9H\nkind: fact\n---\n")
    assert main(["validate", "--repo", str(tmp_path / "kr")]) == 1
    assert "errors" in capsys.readouterr().out


def test_cli_validate_with_base_detects_terminal_transition(knowledge_repo, capsys):
    f = knowledge_repo.load_findings()[0]
    base = knowledge_repo.head_sha()
    f.status = "retracted"
    f.retracted_reason = "the example was never true"
    f.raw_header = None
    knowledge_repo.write_finding(f)
    knowledge_repo.commit_files([f.path], "retract")
    f.status = "active"
    f.retracted_reason = None
    knowledge_repo.write_finding(f)
    knowledge_repo.commit_files([f.path], "reactivate")
    assert main(["validate", "--repo", str(knowledge_repo.root), "--base", base]) == 0
    assert main(["validate", "--repo", str(knowledge_repo.root), "--base", "HEAD~1"]) == 1
    assert "terminal" in capsys.readouterr().out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.repo'`

- [ ] **Step 4: Write repo.py**

```python
"""The knowledge repo on disk: file access, git operations, diffs, scaffolding."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .model import (Entity, Finding, ParseError, entity_path, finding_filename, parse_entity,
                    parse_finding, serialize_entity, serialize_finding)
from .validate import ValidationError


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str  # A M D R
    path: Path
    old_path: Path | None = None


def template_dir() -> Path:
    return Path(str(resources.files("teamknowledge") / "knowledge_template"))


class KnowledgeRepo:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.findings_dir = self.root / "findings"
        self.entities_dir = self.root / "entities"
        self.schema_dir = self.root / "schema"

    # --- git ---------------------------------------------------------------

    def git(self, *args: str, check: bool = True, env: dict | None = None) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=env)
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def remote_url(self) -> str:
        return self.git("remote", "get-url", "origin").strip()

    def sync_main(self, branch: str = "main") -> None:
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-B", branch, f"origin/{branch}")

    def create_branch(self, name: str, start: str = "origin/main") -> None:
        self.git("checkout", "-q", "-B", name, start)

    def commit_files(self, paths: list[Path], message: str) -> str:
        self.git("add", "--", *[str(p) for p in paths])
        self.git("commit", "-q", "-m", message)
        return self.head_sha()

    def push(self, branch: str, push_options: tuple[str, ...] | list[str] = ()) -> None:
        args = ["push", "-q", "-u", "origin", branch]
        for option in push_options:
            args += ["-o", option]
        self.git(*args)

    def last_commit_for(self, path: Path) -> str | None:
        sha = self.git("log", "--first-parent", "-1", "--format=%H", "--", str(path)).strip()
        return sha or None

    def show(self, ref: str, path: str) -> str:
        return self.git("show", f"{ref}:{path}")

    def ls_tree(self, ref: str, prefix: str) -> list[str]:
        return [n for n in self.git("ls-tree", "-r", "--name-only", ref, "--", prefix).split("\n") if n]

    def changed_files(self, base: str, head: str = "HEAD") -> list[Change]:
        out = self.git("diff", "--name-status", "-M", f"{base}..{head}", "--", "findings", "entities")
        changes: list[Change] = []
        for line in out.splitlines():
            parts = line.split("\t")
            status = parts[0][0]
            if status == "R":
                changes.append(Change("R", Path(parts[2]), Path(parts[1])))
            else:
                changes.append(Change(status, Path(parts[1])))
        return changes

    # --- files -------------------------------------------------------------

    @property
    def config(self) -> dict:
        return yaml.safe_load((self.root / "config.yaml").read_text()) or {}

    def load_findings(self) -> list[Finding]:
        return [parse_finding(p.read_text(), p.relative_to(self.root)) for p in sorted(self.findings_dir.glob("*.md"))]

    def load_entities(self) -> list[Entity]:
        return [parse_entity(p.read_text(), p.relative_to(self.root)) for p in sorted(self.entities_dir.glob("*/*.md"))]

    def load_with_errors(self) -> tuple[list[Finding], list[Entity], list[ValidationError]]:
        findings: list[Finding] = []
        entities: list[Entity] = []
        errors: list[ValidationError] = []
        for p in sorted(self.findings_dir.glob("*.md")):
            try:
                findings.append(parse_finding(p.read_text(), p.relative_to(self.root)))
            except ParseError as exc:
                errors.append(ValidationError("file", str(exc), path=str(p.relative_to(self.root))))
        for p in sorted(self.entities_dir.glob("*/*.md")):
            try:
                entities.append(parse_entity(p.read_text(), p.relative_to(self.root)))
            except ParseError as exc:
                errors.append(ValidationError("file", str(exc), path=str(p.relative_to(self.root))))
        return findings, entities, errors

    def load_at(self, ref: str) -> tuple[list[Finding], list[Entity]]:
        findings = [parse_finding(self.show(ref, n), Path(n)) for n in self.ls_tree(ref, "findings") if n.endswith(".md")]
        entities = [parse_entity(self.show(ref, n), Path(n)) for n in self.ls_tree(ref, "entities") if n.endswith(".md")]
        return findings, entities

    def write_finding(self, f: Finding) -> Path:
        rel = f.path or Path("findings") / finding_filename(f.id, f.title)
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(serialize_finding(f))
        f.path = rel
        return rel

    def write_entity(self, e: Entity) -> Path:
        rel = e.path or entity_path(e)
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(serialize_entity(e))
        e.path = rel
        e.dir_type = e.type
        return rel


def init_knowledge_repo(dest: Path, local_remote: Path | None = None, author: str = "tk") -> KnowledgeRepo:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} is not empty")
    shutil.copytree(template_dir(), dest, dirs_exist_ok=True)
    (dest / "gitlab-ci.yml").rename(dest / ".gitlab-ci.yml")
    repo = KnowledgeRepo(dest)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", author)
    repo.git("config", "user.email", f"{author}@users.noreply.local")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "chore: initialise team knowledge repo")
    if local_remote is not None:
        local_remote = Path(local_remote).resolve()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(local_remote)], check=True)
        repo.git("remote", "add", "origin", str(local_remote))
        repo.git("push", "-q", "-u", "origin", "main")
    return repo
```

- [ ] **Step 5: Add `init` and `validate` to cli.py**

Replace `cli.py` with:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_repo.py tests/test_package.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/teamknowledge/knowledge_template src/teamknowledge/repo.py src/teamknowledge/cli.py tests/conftest.py tests/test_repo.py
git commit -m "feat: knowledge repo layer, starter template, tk init and tk validate"
```

---

### Task 5: Git host interface and the local adapter, `tk review`

**Files:**
- Create: `src/teamknowledge/githost/__init__.py`
- Create: `src/teamknowledge/githost/base.py`
- Create: `src/teamknowledge/githost/local.py`
- Modify: `src/teamknowledge/cli.py`
- Test: `tests/test_githost_local.py`

**Interfaces:**
- Consumes: `KnowledgeRepo` from `repo` (tests only).
- Produces:
  - `class GitHostError(RuntimeError)`.
  - `@dataclass(frozen=True) MergeRequest(iid: int | str, url: str, branch: str, state: str, author: str, approvers: list[str], merged_at: datetime | None, merge_commit: str | None)`.
  - `class GitHost(Protocol)` with `push_options(title, description, reviewers) -> list[str]`, `open_merge_request(branch, title, description, reviewers) -> MergeRequest`, `merge_request_for_branch(branch) -> MergeRequest | None`, `merge_requests_for_commit(sha) -> list[MergeRequest]`, `list_open_by(username) -> list[MergeRequest]`.
  - `class LocalGitHost(remote: Path, author: str)` implementing `GitHost` plus `approve(branch, username) -> MergeRequest` and `list_all() -> list[MergeRequest]`. Sidecar directory is `<remote>.review/` beside the bare repo.
  - CLI: `tk review list`, `tk review approve <branch> --as USER`.

- [ ] **Step 1: Write the failing tests**

`tests/test_githost_local.py`:

```python
from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.githost.base import GitHostError
from teamknowledge.githost.local import LocalGitHost


def push_branch(repo, name: str, content: str) -> str:
    repo.create_branch(name)
    p = repo.root / "findings" / f"{name.split('/')[-1]}.txt"
    p.write_text(content)
    sha = repo.commit_files([p.relative_to(repo.root)], f"add {name}")
    repo.push(name)
    repo.sync_main()
    return sha


def test_open_and_lookup(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    mr = host.open_merge_request("finding/one", "[fact] One", "desc", ["bob"])
    assert mr.state == "open" and mr.author == "alice" and mr.iid == 1
    assert host.merge_request_for_branch("finding/one") == mr
    assert host.merge_request_for_branch("finding/none") is None
    assert host.list_open_by("alice") == [mr]
    assert host.list_open_by("bob") == []
    assert host.push_options("t", "d", ["bob"]) == []


def test_author_cannot_approve(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    with pytest.raises(GitHostError, match="own merge request"):
        host.approve("finding/one", "alice")


def test_approve_creates_merge_commit_on_main(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    head = push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    mr = host.approve("finding/one", "bob")
    assert mr.state == "merged" and mr.approvers == ["bob"] and mr.merged_at is not None
    knowledge_repo.sync_main()
    assert knowledge_repo.head_sha() == mr.merge_commit
    parents = knowledge_repo.git("log", "-1", "--format=%P").split()
    assert head in parents and len(parents) == 2
    assert (knowledge_repo.root / "findings" / "one.txt").read_text() == "x"
    assert host.merge_requests_for_commit(mr.merge_commit) == [mr]
    assert knowledge_repo.last_commit_for(Path("findings/one.txt")) == mr.merge_commit


def test_approve_twice_refused(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    host.approve("finding/one", "bob")
    with pytest.raises(GitHostError, match="already merged"):
        host.approve("finding/one", "bob")


def test_approve_refuses_stale_branch(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    push_branch(knowledge_repo, "finding/two", "y")
    host.open_merge_request("finding/one", "t", "d", [])
    host.open_merge_request("finding/two", "t", "d", [])
    host.approve("finding/one", "bob")
    with pytest.raises(GitHostError, match="behind main"):
        host.approve("finding/two", "bob")


def test_cli_review(knowledge_repo, capsys):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "[fact] One", "d", ["bob"])
    assert main(["review", "list", "--repo", str(knowledge_repo.root)]) == 0
    assert "finding/one" in capsys.readouterr().out
    assert main(["review", "approve", "finding/one", "--as", "bob", "--repo", str(knowledge_repo.root)]) == 0
    assert "merged" in capsys.readouterr().out
    assert main(["review", "approve", "finding/one", "--as", "bob", "--repo", str(knowledge_repo.root)]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_githost_local.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.githost'`

- [ ] **Step 3: Write base.py and local.py**

`src/teamknowledge/githost/__init__.py`:

```python
from .base import GitHost, GitHostError, MergeRequest

__all__ = ["GitHost", "GitHostError", "MergeRequest"]
```

`src/teamknowledge/githost/base.py`:

```python
"""What a git hosting service adds on top of git: merge requests and their review state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class GitHostError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeRequest:
    iid: int | str
    url: str
    branch: str
    state: str  # open | merged | closed
    author: str
    approvers: list[str]
    merged_at: datetime | None
    merge_commit: str | None


class GitHost(Protocol):
    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        """Extra `git push -o` options; empty unless the host creates merge requests from pushes."""

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest: ...

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None: ...

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]: ...

    def list_open_by(self, username: str) -> list[MergeRequest]: ...
```

`src/teamknowledge/githost/local.py`:

```python
"""A git host for the home prototype and tests: a bare repo plus a JSON sidecar per merge request."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .base import GitHostError, MergeRequest


class LocalGitHost:
    def __init__(self, remote: Path, author: str):
        self.remote = Path(remote).resolve()
        self.author = author
        self.review_dir = self.remote.parent / (self.remote.name + ".review")
        self.review_dir.mkdir(parents=True, exist_ok=True)

    # --- git helpers -------------------------------------------------------

    def _run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.remote), *args], capture_output=True, text=True, env=env)

    def _git(self, *args: str, env: dict | None = None) -> str:
        result = self._run(*args, env=env)
        if result.returncode != 0:
            raise GitHostError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    # --- records -----------------------------------------------------------

    def _record_path(self, branch: str) -> Path:
        return self.review_dir / (branch.replace("/", "__") + ".json")

    def _load(self, branch: str) -> dict | None:
        p = self._record_path(branch)
        return json.loads(p.read_text()) if p.exists() else None

    def _save(self, rec: dict) -> None:
        self._record_path(rec["branch"]).write_text(json.dumps(rec, indent=2, sort_keys=True))

    def _records(self) -> list[dict]:
        return sorted((json.loads(p.read_text()) for p in self.review_dir.glob("*.json")), key=lambda r: r["iid"])

    @staticmethod
    def _to_mr(rec: dict) -> MergeRequest:
        merged_at = datetime.fromisoformat(rec["merged_at"]) if rec.get("merged_at") else None
        return MergeRequest(iid=rec["iid"], url=rec["url"], branch=rec["branch"], state=rec["state"],
                            author=rec["author"], approvers=list(rec["approvers"]), merged_at=merged_at,
                            merge_commit=rec.get("merge_commit"))

    # --- GitHost -----------------------------------------------------------

    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        return []

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest:
        if self._load(branch) is not None:
            raise GitHostError(f"a merge request for {branch} already exists")
        iid = len(self._records()) + 1
        rec = {
            "iid": iid, "url": f"local://merge-requests/{iid}", "branch": branch, "title": title,
            "description": description, "reviewers": list(reviewers), "author": self.author, "state": "open",
            "approvers": [], "merged_at": None, "merge_commit": None, "head_sha": self._git("rev-parse", branch),
        }
        self._save(rec)
        return self._to_mr(rec)

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None:
        rec = self._load(branch)
        return self._to_mr(rec) if rec else None

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records() if sha in (r.get("merge_commit"), r.get("head_sha"))]

    def list_open_by(self, username: str) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records() if r["author"] == username and r["state"] == "open"]

    def list_all(self) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records()]

    # --- review ------------------------------------------------------------

    def approve(self, branch: str, username: str) -> MergeRequest:
        rec = self._load(branch)
        if rec is None:
            raise GitHostError(f"no merge request for branch {branch}")
        if rec["state"] != "open":
            raise GitHostError(f"merge request for {branch} is already {rec['state']}")
        if username == rec["author"]:
            raise GitHostError("authors cannot approve their own merge request")
        if self._run("merge-base", "--is-ancestor", "main", branch).returncode != 0:
            raise GitHostError(f"{branch} is behind main; rebase it first")
        main = self._git("rev-parse", "main")
        head = self._git("rev-parse", branch)
        tree = self._git("rev-parse", f"{branch}^{{tree}}")
        env = {**os.environ, "GIT_AUTHOR_NAME": username, "GIT_AUTHOR_EMAIL": f"{username}@users.noreply.local",
               "GIT_COMMITTER_NAME": username, "GIT_COMMITTER_EMAIL": f"{username}@users.noreply.local"}
        merge_sha = self._git("commit-tree", tree, "-p", main, "-p", head, "-m",
                              f"Merge {branch}\n\nApproved-by: {username}", env=env)
        self._git("update-ref", "refs/heads/main", merge_sha, main)
        rec.update(state="merged", approvers=[username], merge_commit=merge_sha, head_sha=head,
                   merged_at=datetime.now(timezone.utc).isoformat())
        self._save(rec)
        return self._to_mr(rec)
```

- [ ] **Step 4: Add `tk review` to cli.py**

Add these functions above `build_parser` in `cli.py`:

```python
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
```

And inside `build_parser`, before `return parser`:

```python
    review = sub.add_parser("review", help="local adapter review commands").add_subparsers(dest="review_command", required=True)
    p = review.add_parser("list")
    _add_repo_arg(p)
    p.set_defaults(func=cmd_review_list)
    p = review.add_parser("approve")
    p.add_argument("branch")
    p.add_argument("--as", dest="approver", required=True)
    _add_repo_arg(p)
    p.set_defaults(func=cmd_review_approve)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_githost_local.py tests/test_repo.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/githost src/teamknowledge/cli.py tests/test_githost_local.py
git commit -m "feat: git host interface, local adapter with real merge commits, tk review"
```

---

### Task 6: Proposer: propose, amend, retract, push retry

**Files:**
- Create: `src/teamknowledge/propose.py`
- Modify: `src/teamknowledge/cli.py`
- Test: `tests/test_propose.py`

**Interfaces:**
- Consumes: `KnowledgeRepo`, `GitError`; `Validator`, `ValidationError`; `GitHost`, `MergeRequest`, `GitHostError`; `Finding`, `Entity`, `Evidence`, `new_ulid`.
- Produces:
  - `@dataclass ProposalInput(kind, title, claim, scope: list[str], evidence: list[dict], confidence, sections: dict[str, str], applies_when: str | None = None, supersedes: list[str] = [], review_after: str | None = None, new_entities: list[dict] = [])`.
  - `@dataclass ProposalResult(finding_id: str | None = None, branch: str | None = None, merge_request_url: str | None = None, reviewers: list[str] = [], warnings: list[str] = [], errors: list[dict] = [], error: str | None = None, retry: str | None = None)` with `to_dict()` that drops empty fields but always keeps `finding_id`, `branch`, `merge_request_url` when a branch was created, and `errors` when validation failed.
  - `class Proposer(repo, validator, host, author, today: Callable[[], date] = date.today)` with `propose(inp) -> ProposalResult`, `amend(finding_id, reason, header_changes: dict | None = None, sections: dict | None = None) -> ProposalResult`, `retract(finding_id, reason) -> ProposalResult`, `push_branch(branch) -> ProposalResult`.
  - `resolve_reviewers(scope, entities, config, author) -> list[str]`; `merge_request_text(f, reason=None) -> tuple[str, str]`; `finding_id_from_branch(branch) -> str`.
  - CLI: `tk push <branch>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_propose.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.model import Entity
from teamknowledge.propose import ProposalInput, Proposer, finding_id_from_branch, merge_request_text, resolve_reviewers
from teamknowledge.validate import Validator


@pytest.fixture
def proposer(knowledge_repo):
    # add a scoped entity with an owner, committed to main
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"],
               description="The q process that fronts the tick databases for the pricing desk.", owner=["bob"])
    p = knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([p], "add gateway")
    knowledge_repo.push("main")
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    return Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))


def good_input(**kw) -> ProposalInput:
    base = dict(kind="dead-end", title="Batch queries over 10k symbols time out",
                claim="Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
                scope=["system/kdb-gateway"], evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
                confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."})
    base.update(kw)
    return ProposalInput(**base)


def test_propose_opens_merge_request(proposer, knowledge_repo):
    r = proposer.propose(good_input())
    assert r.errors == [] and r.error is None
    assert r.branch == f"finding/{r.finding_id}"
    assert r.merge_request_url == "local://merge-requests/1"
    assert r.reviewers == ["bob"]
    mr = proposer.host.merge_request_for_branch(r.branch)
    assert mr.state == "open"
    # the clone is back on main and main is untouched
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert not any(p.name.startswith(r.finding_id) for p in knowledge_repo.findings_dir.iterdir())
    # the branch has the file with the right header
    names = knowledge_repo.ls_tree(f"origin/{r.branch}", "findings")
    path = next(n for n in names if r.finding_id in n)
    text = knowledge_repo.show(f"origin/{r.branch}", path)
    assert "author: alice" in text and "created: 2026-10-04" in text and "status: active" in text


def test_propose_invalid_returns_errors_and_writes_nothing(proposer, knowledge_repo):
    r = proposer.propose(good_input(scope=["system/kdb-gw"]))
    assert r.branch is None and r.merge_request_url is None
    assert r.errors[0]["field"] == "scope[0]"
    assert "system/kdb-gateway" in r.errors[0]["suggestion"]
    assert knowledge_repo.git("branch", "--list", "finding/*").strip() == ""


def test_propose_with_new_entity_in_same_mr(proposer, knowledge_repo):
    r = proposer.propose(good_input(scope=["system/kdb-gateway", "environment/uat"], new_entities=[
        {"type": "environment", "slug": "uat", "name": "UAT", "description": "User acceptance testing environment for the desk."}]))
    assert r.errors == []
    assert "entities/environment/uat.md" in knowledge_repo.ls_tree(f"origin/{r.branch}", "entities")


def test_propose_supersedes_edits_old_file(proposer, knowledge_repo):
    first = proposer.propose(good_input())
    proposer.host.approve(first.branch, "bob")
    second = proposer.propose(good_input(title="Batch queries over 5k symbols time out", supersedes=[first.finding_id]))
    assert second.errors == []
    old_path = next(n for n in knowledge_repo.ls_tree(f"origin/{second.branch}", "findings") if first.finding_id in n)
    old_text = knowledge_repo.show(f"origin/{second.branch}", old_path)
    assert "status: superseded" in old_text and f"superseded_by: {second.finding_id}" in old_text


def test_propose_supersedes_missing_target(proposer):
    r = proposer.propose(good_input(supersedes=["01J9XK3M8Q7ZV2W1F4N6B5HT9Z"]))
    assert r.errors[0]["field"] == "supersedes"


def test_amend_and_retract(proposer, knowledge_repo):
    first = proposer.propose(good_input())
    proposer.host.approve(first.branch, "bob")
    a = proposer.amend(first.finding_id, "tighten the claim", header_changes={"confidence": "inferred"})
    assert a.errors == [] and a.branch.startswith(f"amend/{first.finding_id}-")
    text = knowledge_repo.show(f"origin/{a.branch}", next(n for n in knowledge_repo.ls_tree(f"origin/{a.branch}", "findings") if first.finding_id in n))
    assert "confidence: inferred" in text
    bad = proposer.amend(first.finding_id, "nope", header_changes={"kind": "fact"})
    assert bad.errors[0]["field"] == "kind"
    r = proposer.retract(first.finding_id, "the gateway was upgraded and the limit is gone")
    assert r.errors == [] and r.branch == f"retract/{first.finding_id}"
    text = knowledge_repo.show(f"origin/{r.branch}", next(n for n in knowledge_repo.ls_tree(f"origin/{r.branch}", "findings") if first.finding_id in n))
    assert "status: retracted" in text


def test_amend_unknown_id(proposer):
    assert proposer.amend("01J9XK3M8Q7ZV2W1F4N6B5HT9Z", "x").errors[0]["field"] == "finding_id"


def test_push_failure_keeps_branch_and_offers_retry(proposer, knowledge_repo, monkeypatch):
    def boom(branch, push_options=()):
        raise RuntimeError("remote unreachable")
    monkeypatch.setattr(knowledge_repo, "push", boom)
    r = proposer.propose(good_input())
    assert r.merge_request_url is None and "remote unreachable" in r.error
    assert r.retry == f"tk push {r.branch}"
    assert knowledge_repo.git("rev-parse", "--verify", r.branch).strip()
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    monkeypatch.undo()
    r2 = proposer.push_branch(r.branch)
    assert r2.merge_request_url == "local://merge-requests/1" and r2.finding_id == r.finding_id


def test_cli_push(proposer, knowledge_repo, monkeypatch, capsys):
    monkeypatch.setattr(knowledge_repo, "push", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = proposer.propose(good_input())
    monkeypatch.undo()
    monkeypatch.setenv("TK_GITHOST", "local")
    monkeypatch.setenv("TK_AUTHOR", "alice")
    assert main(["push", r.branch, "--repo", str(knowledge_repo.root)]) == 0
    assert "local://merge-requests/1" in capsys.readouterr().out


def test_resolve_reviewers():
    e1 = Entity(type="system", slug="a", name="A", description="d" * 20, owner=["bob", "alice"])
    e2 = Entity(type="system", slug="b", name="B", description="d" * 20, owner=["carol"])
    cfg = {"review": {"default_reviewers": ["dave"]}}
    assert resolve_reviewers(["system/a", "system/b"], [e1, e2], cfg, author="alice") == ["bob", "carol"]
    assert resolve_reviewers(["system/zzz"], [e1, e2], cfg, author="alice") == ["dave"]
    assert resolve_reviewers(["system/a"], [Entity(type="system", slug="a", name="A", description="d" * 20)], {}, author="x") == []


def test_merge_request_text_and_branch_id():
    from teamknowledge.model import Evidence, Finding
    f = Finding(id="01J9XK3M8Q7ZV2W1F4N6B5HT9D", kind="fact", title="T", claim="C", scope=["system/a"],
                evidence=[Evidence("confluence", ref="https://c/x")], confidence="observed", status="active",
                author="alice", created=date(2026, 10, 4), sections={"Detail": "D", "How to check": "H"})
    title, desc = merge_request_text(f, reason="why")
    assert title == "[fact] T"
    assert "**Claim.** C" in desc and "`system/a`" in desc and "https://c/x" in desc and "why" in desc and "## Detail" in desc
    assert finding_id_from_branch("amend/01J9XK3M8Q7ZV2W1F4N6B5HT9D-ab12cd") == "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_propose.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.propose'`

- [ ] **Step 3: Write propose.py**

```python
"""Turn a finding into a branch and a merge request; amend, retract, and retry pushes."""

from __future__ import annotations

import copy
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .githost.base import GitHost
from .model import Entity, Evidence, Finding, new_ulid, parse_finding
from .repo import KnowledgeRepo
from .validate import ValidationError, Validator

AMENDABLE = frozenset({"title", "claim", "scope", "applies_when", "evidence", "confidence", "contradicts", "review_after"})


@dataclass
class ProposalInput:
    kind: str
    title: str
    claim: str
    scope: list[str]
    evidence: list[dict]
    confidence: str
    sections: dict[str, str]
    applies_when: str | None = None
    supersedes: list[str] = field(default_factory=list)
    review_after: str | None = None
    new_entities: list[dict] = field(default_factory=list)


@dataclass
class ProposalResult:
    finding_id: str | None = None
    branch: str | None = None
    merge_request_url: str | None = None
    reviewers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    error: str | None = None
    retry: str | None = None

    def to_dict(self) -> dict:
        if self.errors:
            return {"errors": self.errors}
        d = {"finding_id": self.finding_id, "branch": self.branch, "merge_request_url": self.merge_request_url,
             "reviewers": self.reviewers, "warnings": self.warnings}
        if self.error:
            d["error"] = self.error
            d["retry"] = self.retry
        return d


def finding_id_from_branch(branch: str) -> str:
    return branch.split("/", 1)[1][:26]


def resolve_reviewers(scope: list[str], entities: list[Entity], config: dict, author: str) -> list[str]:
    by_ref = {e.ref: e for e in entities}
    owners = sorted({u for ref in scope if ref in by_ref for u in by_ref[ref].owner})
    if not owners:
        owners = list(config.get("review", {}).get("default_reviewers", []) or [])
    return [u for u in owners if u != author]


def merge_request_text(f: Finding, reason: str | None = None) -> tuple[str, str]:
    title = f"[{f.kind}] {f.title}"
    lines = [f"**Claim.** {f.claim}", "", "**Scope.** " + ", ".join(f"`{s}`" for s in f.scope),
             f"**Confidence.** {f.confidence}", ""]
    if f.applies_when:
        lines += [f"**Applies when.** {f.applies_when}", ""]
    lines += ["**Evidence.**"] + [f"- {e.type}: {e.ref or ''} {e.note or ''}".rstrip() for e in f.evidence] + [""]
    if f.supersedes:
        lines += ["**Supersedes.** " + ", ".join(f.supersedes), ""]
    if reason:
        lines += [f"**Reason for change.** {reason}", ""]
    lines.append(f.body())
    return title, "\n".join(lines)


def _entity_from_dict(d: dict) -> Entity:
    return Entity(type=str(d.get("type", "")), slug=str(d.get("slug", "")), name=str(d.get("name", "")),
                  description=str(d.get("description", "")), aliases=list(d.get("aliases", []) or []),
                  owner=list(d.get("owner", []) or []), related=list(d.get("related", []) or []),
                  links=[Evidence(type=str(x.get("type", "")), ref=x.get("ref"), note=x.get("note")) for x in d.get("links", []) or []],
                  body=str(d.get("body", "") or ""))


def _evidence_list(items: list[dict]) -> list[Evidence]:
    return [Evidence(type=str(x.get("type", "")), ref=x.get("ref"), note=x.get("note")) for x in items]


def _errors(errs: list[ValidationError]) -> ProposalResult:
    return ProposalResult(errors=[e.to_dict() for e in errs])


class Proposer:
    def __init__(self, repo: KnowledgeRepo, validator: Validator, host: GitHost, author: str,
                 today: Callable[[], date] = date.today):
        self.repo = repo
        self.validator = validator
        self.host = host
        self.author = author
        self.today = today

    # --- public ------------------------------------------------------------

    def propose(self, inp: ProposalInput) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        fid = new_ulid()
        try:
            review_after = date.fromisoformat(inp.review_after) if inp.review_after else None
        except ValueError:
            return _errors([ValidationError("review_after", f"not an ISO date: {inp.review_after!r}")])
        new = Finding(id=fid, kind=inp.kind, title=inp.title.strip(), claim=inp.claim.strip(), scope=list(inp.scope),
                      evidence=_evidence_list(inp.evidence), confidence=inp.confidence, status="active",
                      author=self.author, created=self.today(), sections=dict(inp.sections),
                      applies_when=inp.applies_when or None, supersedes=list(inp.supersedes), review_after=review_after)
        by_id = {f.id: f for f in findings}
        missing = [sid for sid in inp.supersedes if sid not in by_id]
        if missing:
            return _errors([ValidationError("supersedes", f"finding {sid} does not exist") for sid in missing])
        changed_old: list[Finding] = []
        for sid in inp.supersedes:
            old = by_id[sid]
            old.status, old.superseded_by, old.raw_header = "superseded", fid, None
            changed_old.append(old)
        new_entities = [_entity_from_dict(d) for d in inp.new_entities]
        errs = self.validator.validate_all(findings + [new], entities + new_entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"finding/{fid}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_entity(e) for e in new_entities]
        paths += [self.repo.write_finding(new)] + [self.repo.write_finding(o) for o in changed_old]
        title, desc = merge_request_text(new)
        reviewers = resolve_reviewers(new.scope, entities + new_entities, self.repo.config, self.author)
        return self._submit(fid, branch, paths, f"feat: {title}", title, desc, reviewers)

    def amend(self, finding_id: str, reason: str, header_changes: dict | None = None,
              sections: dict | None = None) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        target = next((f for f in findings if f.id == finding_id), None)
        if target is None:
            return _errors([ValidationError("finding_id", f"finding {finding_id} does not exist")])
        bad = [k for k in (header_changes or {}) if k not in AMENDABLE]
        if bad:
            return _errors([ValidationError(k, f"'{k}' cannot be amended; allowed: {', '.join(sorted(AMENDABLE))}") for k in bad])
        for k, v in (header_changes or {}).items():
            if k == "evidence":
                target.evidence = _evidence_list(v)
            elif k == "review_after":
                target.review_after = date.fromisoformat(v) if v else None
            elif k in ("scope", "contradicts"):
                setattr(target, k, list(v))
            else:
                setattr(target, k, v)
        if sections is not None:
            target.sections = dict(sections)
        target.raw_header = None
        errs = self.validator.validate_all(findings, entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"amend/{finding_id}-{secrets.token_hex(3)}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_finding(target)]
        title, desc = merge_request_text(target, reason)
        reviewers = resolve_reviewers(target.scope, entities, self.repo.config, self.author)
        return self._submit(finding_id, branch, paths, f"fix: amend {title}", f"Amend {title}", desc, reviewers)

    def retract(self, finding_id: str, reason: str) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        target = next((f for f in findings if f.id == finding_id), None)
        if target is None:
            return _errors([ValidationError("finding_id", f"finding {finding_id} does not exist")])
        target.status, target.retracted_reason, target.raw_header = "retracted", reason, None
        errs = self.validator.validate_all(findings, entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"retract/{finding_id}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_finding(target)]
        title, desc = merge_request_text(target, reason)
        reviewers = resolve_reviewers(target.scope, entities, self.repo.config, self.author)
        return self._submit(finding_id, branch, paths, f"fix: retract {title}", f"Retract {title}", desc, reviewers)

    def push_branch(self, branch: str) -> ProposalResult:
        fid = finding_id_from_branch(branch)
        names = [n for n in self.repo.ls_tree(branch, "findings") if fid in n]
        if not names:
            return ProposalResult(finding_id=fid, branch=branch, error=f"no finding {fid} on branch {branch}")
        f = parse_finding(self.repo.show(branch, names[0]), Path(names[0]))
        title, desc = merge_request_text(f)
        reviewers = resolve_reviewers(f.scope, self.repo.load_entities(), self.repo.config, self.author)
        return self._push_and_open(fid, branch, title, desc, reviewers)

    # --- internals ---------------------------------------------------------

    def _submit(self, fid: str, branch: str, paths: list[Path], commit_message: str,
                title: str, description: str, reviewers: list[str]) -> ProposalResult:
        self.repo.commit_files(paths, commit_message)
        return self._push_and_open(fid, branch, title, description, reviewers)

    def _push_and_open(self, fid: str, branch: str, title: str, description: str, reviewers: list[str]) -> ProposalResult:
        warnings: list[str] = []
        if not reviewers:
            warnings.append("no reviewers resolved: set owner on a scoped entity or review.default_reviewers in config.yaml")
        try:
            existing = self.host.merge_request_for_branch(branch)
            if existing is None:
                self.repo.push(branch, self.host.push_options(title, description, reviewers))
                mr = self.host.open_merge_request(branch, title, description, reviewers)
            else:
                self.repo.push(branch, [])
                mr = existing
        except Exception as exc:  # git or host failure: the branch is committed locally, so offer a retry
            return ProposalResult(finding_id=fid, branch=branch, reviewers=reviewers, warnings=warnings,
                                  error=f"{type(exc).__name__}: {exc}", retry=f"tk push {branch}")
        finally:
            self.repo.sync_main()
        return ProposalResult(finding_id=fid, branch=branch, merge_request_url=mr.url, reviewers=reviewers, warnings=warnings)
```

- [ ] **Step 4: Add `tk push` to cli.py**

Add above `build_parser`:

```python
def _host_from_env(repo):
    """Build the git host named by TK_GITHOST. The GitLab branch is filled in by Task 7."""
    from .githost.local import LocalGitHost

    kind = os.environ.get("TK_GITHOST", "local")
    author = os.environ.get("TK_AUTHOR", "tk")
    if kind == "local":
        return LocalGitHost(Path(repo.remote_url()), author=author)
    raise SystemExit(f"TK_GITHOST={kind!r} is not supported yet")


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
```

In `build_parser` before `return parser`:

```python
    p = sub.add_parser("push", help="retry push and merge request creation for a committed branch")
    p.add_argument("branch")
    _add_repo_arg(p)
    p.set_defaults(func=cmd_push)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_propose.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/propose.py src/teamknowledge/cli.py tests/test_propose.py
git commit -m "feat: proposer opens findings as merge requests; amend, retract, tk push"
```

---

### Task 7: GitLab adapter

**Files:**
- Create: `src/teamknowledge/githost/gitlab.py`
- Modify: `src/teamknowledge/cli.py` (`_host_from_env` gains the GitLab branch)
- Test: `tests/test_githost_gitlab.py`

**Interfaces:**
- Consumes: `MergeRequest`, `GitHostError` from `githost.base`.
- Produces: `class GitLabHost(url: str, token: str, project: str, mode: str = "api", target_branch: str = "main", session=None)` implementing `GitHost`. `session` is any object with `get(url, params=None, headers=None, timeout=None)` and `post(url, json=None, headers=None, timeout=None)` returning objects with `.status_code`, `.json()`, `.text`; defaults to `requests.Session()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_githost_gitlab.py`:

```python
import os
from datetime import datetime, timezone

import pytest

from teamknowledge.githost.base import GitHostError
from teamknowledge.githost.gitlab import GitLabHost


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Routes (method, path) to canned payloads and records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _handle(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url.split("/api/v4", 1)[1]
        self.calls.append((method, path, params, json, headers))
        key = (method, path)
        if key not in self.routes:
            return FakeResponse(404, {"message": "404 Not Found"})
        payload = self.routes[key]
        return FakeResponse(200, payload(params) if callable(payload) else payload)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._handle("GET", url, params=params, headers=headers, timeout=timeout)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._handle("POST", url, json=json, headers=headers, timeout=timeout)


PID = "pricing%2Fteam-knowledge"
MR = {"iid": 7, "web_url": "https://gitlab.bank/pricing/team-knowledge/-/merge_requests/7",
      "source_branch": "finding/01J9", "state": "merged", "author": {"username": "alice"},
      "merged_at": "2026-10-04T10:00:00.000Z", "merge_commit_sha": "abc123", "squash_commit_sha": None}
APPROVALS = {"approved_by": [{"user": {"username": "bob"}}]}


def host(routes, mode="api"):
    return GitLabHost("https://gitlab.bank", "tok", "pricing/team-knowledge", mode=mode, session=FakeSession(routes))


def test_open_merge_request_resolves_reviewer_ids_and_sets_token():
    routes = {
        ("GET", "/users"): lambda params: [{"id": 42, "username": params["username"]}],
        ("POST", f"/projects/{PID}/merge_requests"): {**MR, "state": "opened", "merged_at": None, "merge_commit_sha": None},
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    h = host(routes)
    mr = h.open_merge_request("finding/01J9", "[fact] T", "desc", ["bob"])
    assert mr.state == "open" and mr.iid == 7 and mr.author == "alice" and mr.approvers == []
    post = next(c for c in h.session.calls if c[0] == "POST")
    assert post[3] == {"source_branch": "finding/01J9", "target_branch": "main", "title": "[fact] T",
                       "description": "desc", "reviewer_ids": [42], "remove_source_branch": True}
    assert post[4]["PRIVATE-TOKEN"] == "tok"


def test_unknown_reviewer_is_skipped():
    routes = {
        ("GET", "/users"): [],
        ("POST", f"/projects/{PID}/merge_requests"): {**MR, "state": "opened"},
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    h = host(routes)
    h.open_merge_request("finding/01J9", "t", "d", ["ghost"])
    post = next(c for c in h.session.calls if c[0] == "POST")
    assert post[3]["reviewer_ids"] == []


def test_merge_request_for_branch_parses_merged_state():
    routes = {
        ("GET", f"/projects/{PID}/merge_requests"): lambda params: [MR] if params.get("source_branch") == "finding/01J9" else [],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): APPROVALS,
    }
    mr = host(routes).merge_request_for_branch("finding/01J9")
    assert mr.state == "merged" and mr.approvers == ["bob"] and mr.merge_commit == "abc123"
    assert mr.merged_at == datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    assert host(routes).merge_request_for_branch("finding/none") is None


def test_merge_requests_for_commit_and_list_open_by():
    routes = {
        ("GET", f"/projects/{PID}/repository/commits/abc123/merge_requests"): [MR],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): APPROVALS,
        ("GET", f"/projects/{PID}/merge_requests"): lambda params: [{**MR, "state": "opened"}] if params.get("author_username") == "alice" else [],
    }
    h = host(routes)
    assert [m.iid for m in h.merge_requests_for_commit("abc123")] == [7]
    assert [m.state for m in h.list_open_by("alice")] == ["open"]
    assert h.list_open_by("bob") == []


def test_http_error_raises_githosterror():
    with pytest.raises(GitHostError, match="404"):
        host({}).merge_requests_for_commit("zzz")


def test_push_options_mode():
    h = host({}, mode="push-options")
    opts = h.push_options("[fact] T", "desc", ["bob", "carol"])
    assert opts == ["merge_request.create", "merge_request.target=main", "merge_request.title=[fact] T",
                    "merge_request.assign=bob", "merge_request.assign=carol"]
    assert host({}).push_options("t", "d", ["bob"]) == []


def test_push_options_open_polls_branch():
    routes = {
        ("GET", f"/projects/{PID}/merge_requests"): [{**MR, "state": "opened"}],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    mr = host(routes, mode="push-options").open_merge_request("finding/01J9", "t", "d", [])
    assert mr.iid == 7
    with pytest.raises(GitHostError, match="did not create"):
        host({("GET", f"/projects/{PID}/merge_requests"): []}, mode="push-options").open_merge_request("b", "t", "d", [])


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GITLAB_TOKEN"), reason="GITLAB_TOKEN not set")
def test_live_list_open():
    h = GitLabHost(os.environ.get("GITLAB_URL", "https://gitlab.com"), os.environ["GITLAB_TOKEN"], os.environ["GITLAB_PROJECT"])
    assert isinstance(h.list_open_by(os.environ.get("TK_AUTHOR", "")), list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_githost_gitlab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.githost.gitlab'`

- [ ] **Step 3: Write gitlab.py**

```python
"""GitLab as a git host: REST API mode and push-option mode."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import requests

from .base import GitHostError, MergeRequest

_STATE = {"opened": "open", "merged": "merged", "closed": "closed", "locked": "open"}


class GitLabHost:
    def __init__(self, url: str, token: str, project: str, mode: str = "api",
                 target_branch: str = "main", session=None, timeout: float = 20.0):
        if mode not in ("api", "push-options"):
            raise ValueError(f"mode must be 'api' or 'push-options', got {mode!r}")
        self.api = url.rstrip("/") + "/api/v4"
        self.project_id = quote(project, safe="")
        self.mode = mode
        self.target_branch = target_branch
        self.session = session or requests.Session()
        self.headers = {"PRIVATE-TOKEN": token}
        self.timeout = timeout

    # --- http --------------------------------------------------------------

    def _get(self, path: str, **params):
        r = self.session.get(self.api + path, params=params or None, headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise GitHostError(f"GitLab GET {path} returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, payload: dict):
        r = self.session.post(self.api + path, json=payload, headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise GitHostError(f"GitLab POST {path} returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def _mr_path(self, suffix: str = "") -> str:
        return f"/projects/{self.project_id}/merge_requests{suffix}"

    def _user_id(self, username: str) -> int | None:
        users = self._get("/users", username=username)
        return users[0]["id"] if users else None

    def _to_mr(self, data: dict) -> MergeRequest:
        approvals = self._get(self._mr_path(f"/{data['iid']}/approvals"))
        approvers = [a["user"]["username"] for a in approvals.get("approved_by", [])]
        merged_at = data.get("merged_at")
        return MergeRequest(
            iid=data["iid"], url=data["web_url"], branch=data["source_branch"],
            state=_STATE.get(data["state"], data["state"]), author=data["author"]["username"],
            approvers=approvers,
            merged_at=datetime.fromisoformat(merged_at.replace("Z", "+00:00")) if merged_at else None,
            merge_commit=data.get("merge_commit_sha") or data.get("squash_commit_sha"),
        )

    # --- GitHost -----------------------------------------------------------

    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        if self.mode != "push-options":
            return []
        opts = ["merge_request.create", f"merge_request.target={self.target_branch}", f"merge_request.title={title}"]
        opts += [f"merge_request.assign={r}" for r in reviewers]
        return opts

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest:
        if self.mode == "push-options":
            mr = self.merge_request_for_branch(branch)
            if mr is None:
                raise GitHostError(f"push did not create a merge request for {branch}; check push options are enabled")
            return mr
        ids = [uid for uid in (self._user_id(u) for u in reviewers) if uid is not None]
        data = self._post(self._mr_path(), {
            "source_branch": branch, "target_branch": self.target_branch, "title": title,
            "description": description, "reviewer_ids": ids, "remove_source_branch": True,
        })
        return self._to_mr(data)

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None:
        items = self._get(self._mr_path(), source_branch=branch, state="all", order_by="updated_at", sort="desc")
        return self._to_mr(items[0]) if items else None

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]:
        items = self._get(f"/projects/{self.project_id}/repository/commits/{sha}/merge_requests")
        return [self._to_mr(i) for i in items]

    def list_open_by(self, username: str) -> list[MergeRequest]:
        items = self._get(self._mr_path(), state="opened", author_username=username)
        return [self._to_mr(i) for i in items]
```

- [ ] **Step 4: Extend `_host_from_env` in cli.py**

Replace the body of `_host_from_env` with:

```python
    from .githost.gitlab import GitLabHost
    from .githost.local import LocalGitHost

    kind = os.environ.get("TK_GITHOST", "local")
    author = os.environ.get("TK_AUTHOR", "tk")
    if kind == "local":
        return LocalGitHost(Path(repo.remote_url()), author=author)
    if kind == "gitlab":
        cfg = repo.config.get("gitlab", {})
        token = os.environ.get("GITLAB_TOKEN")
        if not token:
            raise SystemExit("GITLAB_TOKEN is required when TK_GITHOST=gitlab")
        return GitLabHost(os.environ.get("GITLAB_URL", "https://gitlab.com"), token, cfg["project"],
                          mode=os.environ.get("TK_GITLAB_MODE", "api"), target_branch=cfg.get("target_branch", "main"))
    raise SystemExit(f"TK_GITHOST must be 'local' or 'gitlab', got {kind!r}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_githost_gitlab.py tests/test_propose.py -v`
Expected: all pass, with `test_live_list_open` skipped.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/githost/gitlab.py src/teamknowledge/cli.py tests/test_githost_gitlab.py
git commit -m "feat: GitLab adapter with REST and push-option modes"
```

---

### Task 8: Embedders

**Files:**
- Create: `src/teamknowledge/embed/__init__.py`
- Create: `src/teamknowledge/embed/base.py`
- Create: `src/teamknowledge/embed/fake.py`
- Create: `src/teamknowledge/embed/openai.py`
- Create: `src/teamknowledge/embed/bedrock.py`
- Test: `tests/test_embed.py`

**Interfaces:**
- Produces:
  - `class Embedder(Protocol)` with attributes `model: str`, `dims: int` and `embed(texts: list[str]) -> list[list[float]]`.
  - `FakeEmbedder(dims: int = 64, model: str = "fake")`; `OpenAIEmbedder(model="text-embedding-3-small", dims=1536, client=None)`; `BedrockEmbedder(model="amazon.titan-embed-text-v2:0", dims=1024, region=None, client=None)`.
  - `make_embedder(name: str | None) -> Embedder | None` where `none`/`None`/`""` return `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_embed.py`:

```python
import json
import math
import os

import pytest

from teamknowledge.embed import make_embedder
from teamknowledge.embed.bedrock import BedrockEmbedder
from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.embed.openai import OpenAIEmbedder


def test_fake_is_deterministic_and_unit_length():
    e = FakeEmbedder()
    a, b = e.embed(["hello", "hello"])
    assert a == b and len(a) == 64 and e.dims == 64 and e.model == "fake"
    assert abs(math.sqrt(sum(x * x for x in a)) - 1.0) < 1e-6
    assert e.embed(["hello"])[0] != e.embed(["goodbye"])[0]


def test_make_embedder():
    assert make_embedder("none") is None and make_embedder(None) is None and make_embedder("") is None
    assert isinstance(make_embedder("fake"), FakeEmbedder)
    with pytest.raises(ValueError):
        make_embedder("mystery")


class FakeOpenAI:
    class embeddings:
        @staticmethod
        def create(model, input):
            class R:
                data = [type("D", (), {"embedding": [float(i)] * 3})() for i in range(len(input))]
            return R()


def test_openai_embedder_uses_client():
    e = OpenAIEmbedder(dims=3, client=FakeOpenAI())
    assert e.embed(["a", "b"]) == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    assert e.model == "text-embedding-3-small"


class FakeBedrock:
    def __init__(self):
        self.calls = []

    def invoke_model(self, modelId, body, contentType, accept):
        self.calls.append(json.loads(body))
        return {"body": type("B", (), {"read": lambda self: json.dumps({"embedding": [0.5, 0.5]}).encode()})()}


def test_bedrock_embedder_calls_titan():
    client = FakeBedrock()
    e = BedrockEmbedder(dims=2, client=client)
    assert e.embed(["x", "y"]) == [[0.5, 0.5], [0.5, 0.5]]
    assert client.calls[0] == {"inputText": "x", "dimensions": 2, "normalize": True}
    assert e.model == "amazon.titan-embed-text-v2:0"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_live_openai():
    v = OpenAIEmbedder().embed(["smoke"])[0]
    assert len(v) == 1536


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TK_LIVE_BEDROCK"), reason="TK_LIVE_BEDROCK not set")
def test_live_bedrock():
    v = BedrockEmbedder().embed(["smoke"])[0]
    assert len(v) == 1024
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.embed'`

- [ ] **Step 3: Write the embed package**

`src/teamknowledge/embed/base.py`:

```python
from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    model: str
    dims: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

`src/teamknowledge/embed/fake.py`:

```python
"""Deterministic hash-based vectors for tests. Semantically meaningless, structurally valid."""

from __future__ import annotations

import hashlib
import math
import random


class FakeEmbedder:
    def __init__(self, dims: int = 64, model: str = "fake"):
        self.dims = dims
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            rng = random.Random(seed)
            v = [rng.uniform(-1, 1) for _ in range(self.dims)]
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out
```

`src/teamknowledge/embed/openai.py`:

```python
from __future__ import annotations


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small", dims: int = 1536, client=None):
        self.model = model
        self.dims = dims
        if client is None:
            from openai import OpenAI  # optional extra

            client = OpenAI()
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(d.embedding) for d in response.data]
```

`src/teamknowledge/embed/bedrock.py`:

```python
from __future__ import annotations

import json
import os


class BedrockEmbedder:
    def __init__(self, model: str = "amazon.titan-embed-text-v2:0", dims: int = 1024, region: str | None = None, client=None):
        self.model = model
        self.dims = dims
        if client is None:
            import boto3  # optional extra

            client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION"))
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            body = json.dumps({"inputText": text, "dimensions": self.dims, "normalize": True})
            response = self.client.invoke_model(modelId=self.model, body=body, contentType="application/json", accept="application/json")
            out.append([float(x) for x in json.loads(response["body"].read())["embedding"]])
        return out
```

`src/teamknowledge/embed/__init__.py`:

```python
from __future__ import annotations

from .base import Embedder


def make_embedder(name: str | None) -> Embedder | None:
    name = (name or "none").lower()
    if name == "none":
        return None
    if name == "fake":
        from .fake import FakeEmbedder

        return FakeEmbedder()
    if name == "openai":
        from .openai import OpenAIEmbedder

        return OpenAIEmbedder()
    if name == "bedrock":
        from .bedrock import BedrockEmbedder

        return BedrockEmbedder()
    raise ValueError(f"unknown embedder {name!r}; use none, fake, openai, or bedrock")


__all__ = ["Embedder", "make_embedder"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_embed.py -v`
Expected: 5 passed, 2 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/teamknowledge/embed tests/test_embed.py
git commit -m "feat: embedder interface with fake, OpenAI, and Bedrock implementations"
```

---

### Task 9: Graph client, schema, `tk graph init`, Neo4j test fixture

**Files:**
- Create: `src/teamknowledge/graph/__init__.py` (empty)
- Create: `src/teamknowledge/graph/client.py`
- Create: `src/teamknowledge/graph/schema.py`
- Create: `src/teamknowledge/settings.py`
- Modify: `src/teamknowledge/cli.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_graph_schema.py`

**Interfaces:**
- Produces:
  - `class GraphClient(uri, username, password, database="neo4j")` with attributes `uri`, `database`, `password`, and `run(query, **params) -> list[dict]` (managed transaction, each record as `record.data()`), `run_autocommit(query, **params) -> list[dict]`, `close()`, context manager support.
  - `graph.schema`: `CONSTRAINTS_AND_INDEXES: list[str]`, `vector_index_statement(dims: int) -> str`, `init_schema(client, dims: int | None) -> None` (also awaits indexes), `drop_vector_index(client)`, `wipe(client)`.
  - `settings.Settings` dataclass with fields `repo: Path | None`, `githost: str`, `gitlab_mode: str`, `gitlab_url: str`, `gitlab_token: str | None`, `author: str`, `neo4j_uri: str | None`, `neo4j_username: str`, `neo4j_password: str | None`, `neo4j_database: str`, `embedder: str`; `Settings.from_env(env=os.environ)`; `settings.make_graph_client(s) -> GraphClient` (raises `SystemExit` with a clear message if `NEO4J_URI` is unset); `settings.make_embedder_from(s)`.
  - CLI: `tk graph init`.
  - conftest fixtures: `neo4j_client` (session scope, skips without Docker), `graph` (function scope: wiped, schema initialised with `dims=64`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py`:

```python
import pytest

from teamknowledge.graph.client import GraphClient
from teamknowledge.graph.schema import drop_vector_index, init_schema, wipe

NEO4J_IMAGE = "neo4j:5.26-community"


@pytest.fixture(scope="session")
def neo4j_client():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
    try:
        container = Neo4jContainer(NEO4J_IMAGE)
        container.start()
    except Exception as exc:  # Docker missing or daemon down
        pytest.skip(f"Docker not available: {exc}")
    client = GraphClient(container.get_connection_url(), "neo4j", container.password, "neo4j")
    yield client
    client.close()
    container.stop()


@pytest.fixture
def graph(neo4j_client):
    wipe(neo4j_client)
    drop_vector_index(neo4j_client)
    init_schema(neo4j_client, dims=64)
    return neo4j_client
```

`tests/test_graph_schema.py`:

```python
import pytest

from teamknowledge.cli import main
from teamknowledge.graph.schema import drop_vector_index, init_schema, vector_index_statement, wipe
from teamknowledge.settings import Settings

pytestmark = pytest.mark.neo4j


def index_names(client):
    return {r["name"] for r in client.run("SHOW INDEXES YIELD name RETURN name")}


def test_init_schema_is_idempotent(graph):
    before = index_names(graph)
    init_schema(graph, dims=64)
    assert index_names(graph) == before
    assert {"finding_text", "entity_text", "finding_embedding", "finding_status", "finding_kind", "entity_type"} <= before
    constraints = {r["name"] for r in graph.run("SHOW CONSTRAINTS YIELD name RETURN name")}
    assert {"finding_id", "entity_ref", "source_ref", "person_name", "meta_key"} <= constraints


def test_vector_index_optional(neo4j_client):
    wipe(neo4j_client)
    drop_vector_index(neo4j_client)
    init_schema(neo4j_client, dims=None)
    assert "finding_embedding" not in index_names(neo4j_client)
    assert "`vector.dimensions`: 1024" in vector_index_statement(1024)


def test_wipe_removes_everything(graph):
    graph.run("CREATE (:Finding {id: 'x'})-[:SCOPED_TO]->(:Entity {ref: 'system/a'})")
    wipe(graph)
    assert graph.run("MATCH (n) RETURN count(n) AS c")[0]["c"] == 0


def test_cli_graph_init(neo4j_client, monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_URI", neo4j_client.uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_client.password)
    monkeypatch.setenv("TK_EMBEDDER", "fake")
    assert main(["graph", "init"]) == 0
    assert "vector index: 64 dims" in capsys.readouterr().out


def test_settings_from_env():
    s = Settings.from_env({"TK_REPO": "/x", "NEO4J_URI": "bolt://h:7687", "NEO4J_PASSWORD": "p", "TK_EMBEDDER": "fake"})
    assert str(s.repo) == "/x" and s.neo4j_username == "neo4j" and s.neo4j_database == "neo4j"
    assert s.githost == "local" and s.gitlab_mode == "api" and s.embedder == "fake"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.graph'` (or skip entirely if Docker is absent; in that case still proceed and rely on the non-Docker test `test_settings_from_env`).

- [ ] **Step 3: Write client.py, schema.py, settings.py**

`src/teamknowledge/graph/client.py`:

```python
"""Thin wrapper over the neo4j driver."""

from __future__ import annotations

from neo4j import GraphDatabase


class GraphClient:
    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.password = password
        self.uri = uri

    def run(self, query: str, **params) -> list[dict]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.database)
        return [r.data() for r in records]

    def run_autocommit(self, query: str, **params) -> list[dict]:
        with self.driver.session(database=self.database) as session:
            return [r.data() for r in session.run(query, params)]

    def close(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
```

`src/teamknowledge/graph/schema.py`:

```python
"""Constraints and indexes. Everything here exists in Neo4j Community 5.13+."""

from __future__ import annotations

from .client import GraphClient

CONSTRAINTS_AND_INDEXES = [
    "CREATE CONSTRAINT finding_id  IF NOT EXISTS FOR (f:Finding) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT entity_ref  IF NOT EXISTS FOR (e:Entity)  REQUIRE e.ref IS UNIQUE",
    "CREATE CONSTRAINT source_ref  IF NOT EXISTS FOR (s:Source)  REQUIRE s.ref IS UNIQUE",
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person)  REQUIRE p.username IS UNIQUE",
    "CREATE CONSTRAINT meta_key    IF NOT EXISTS FOR (m:Meta)    REQUIRE m.key IS UNIQUE",
    "CREATE INDEX finding_status IF NOT EXISTS FOR (f:Finding) ON (f.status)",
    "CREATE INDEX finding_kind   IF NOT EXISTS FOR (f:Finding) ON (f.kind)",
    "CREATE INDEX entity_type    IF NOT EXISTS FOR (e:Entity)  ON (e.type)",
    "CREATE FULLTEXT INDEX finding_text IF NOT EXISTS FOR (f:Finding) ON EACH [f.title, f.claim, f.body]",
    "CREATE FULLTEXT INDEX entity_text  IF NOT EXISTS FOR (e:Entity)  ON EACH [e.name, e.aliases_text, e.description]",
]


def vector_index_statement(dims: int) -> str:
    return (
        "CREATE VECTOR INDEX finding_embedding IF NOT EXISTS FOR (f:Finding) ON (f.embedding) "
        f"OPTIONS {{ indexConfig: {{ `vector.dimensions`: {int(dims)}, `vector.similarity_function`: 'cosine' }} }}"
    )


def init_schema(client: GraphClient, dims: int | None) -> None:
    for statement in CONSTRAINTS_AND_INDEXES:
        client.run(statement)
    if dims:
        client.run(vector_index_statement(dims))
    client.run("CALL db.awaitIndexes()")


def drop_vector_index(client: GraphClient) -> None:
    client.run("DROP INDEX finding_embedding IF EXISTS")


def wipe(client: GraphClient) -> None:
    client.run_autocommit("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
```

`src/teamknowledge/settings.py`:

```python
"""Environment-driven settings and factories shared by the CLI and the MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    repo: Path | None
    githost: str
    gitlab_mode: str
    gitlab_url: str
    gitlab_token: str | None
    author: str
    neo4j_uri: str | None
    neo4j_username: str
    neo4j_password: str | None
    neo4j_database: str
    embedder: str

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        repo = env.get("TK_REPO")
        return cls(
            repo=Path(repo) if repo else None,
            githost=env.get("TK_GITHOST", "local"),
            gitlab_mode=env.get("TK_GITLAB_MODE", "api"),
            gitlab_url=env.get("GITLAB_URL", "https://gitlab.com"),
            gitlab_token=env.get("GITLAB_TOKEN"),
            author=env.get("TK_AUTHOR", "tk"),
            neo4j_uri=env.get("NEO4J_URI"),
            neo4j_username=env.get("NEO4J_USERNAME", "neo4j"),
            neo4j_password=env.get("NEO4J_PASSWORD"),
            neo4j_database=env.get("NEO4J_DATABASE", "neo4j"),
            embedder=env.get("TK_EMBEDDER", "none"),
        )


def make_graph_client(s: Settings):
    from .graph.client import GraphClient

    if not s.neo4j_uri or s.neo4j_password is None:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set")
    return GraphClient(s.neo4j_uri, s.neo4j_username, s.neo4j_password, s.neo4j_database)


def make_embedder_from(s: Settings):
    from .embed import make_embedder

    return make_embedder(s.embedder)
```

- [ ] **Step 4: Add `tk graph init` to cli.py**

Add above `build_parser`:

```python
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
```

In `build_parser` before `return parser`:

```python
    graph = sub.add_parser("graph", help="graph administration").add_subparsers(dest="graph_command", required=True)
    p = graph.add_parser("init", help="create constraints and indexes")
    p.set_defaults(func=cmd_graph_init)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_schema.py -v`
Expected: all pass with Docker running. Without Docker the four `neo4j` tests skip and `test_settings_from_env` passes.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/graph src/teamknowledge/settings.py src/teamknowledge/cli.py tests/conftest.py tests/test_graph_schema.py
git commit -m "feat: Neo4j client, schema init, settings, tk graph init, Docker test fixture"
```

---

### Task 10: Sync into Neo4j, `tk sync`

**Files:**
- Create: `src/teamknowledge/graph/sync.py`
- Modify: `src/teamknowledge/cli.py`
- Test: `tests/test_graph_sync.py`

**Interfaces:**
- Consumes: `KnowledgeRepo`, `Change`; `Validator`; `Embedder`; `GitHost`, `MergeRequest`; `GraphClient`; `init_schema`, `wipe`, `drop_vector_index`; `Finding`, `Entity`.
- Produces:
  - `class SyncError(RuntimeError)`.
  - `@dataclass SyncReport(head: str, unchanged: bool = False, findings_upserted: int = 0, entities_upserted: int = 0, findings_deleted: int = 0, entities_deleted: int = 0, embedded: int = 0, embedding_failures: int = 0, review_metadata_set: int = 0, warnings: list[str] = [])`.
  - `text_hash(f: Finding) -> str`; `finding_props(f, commit: str | None) -> dict`; `entity_row(e) -> dict`; `SCHEMA_VERSION = 1`.
  - `class Syncer(repo, client, embedder=None, host=None, sleep=time.sleep)` with `meta() -> dict | None`, `sync(full: bool = False) -> SyncReport`, `embed_missing() -> int`.
  - Finding node properties: `id, kind, title, claim, body, sections (JSON string), applies_when, confidence, status, retracted_reason, superseded_by, author, created (ISO), review_after (ISO or null), path, commit, text_hash, observations (list), embedding, embedding_model, mr_url, approved_by (list), merged_at (ISO)`. Entity node properties: `ref, type, slug, name, aliases, aliases_text, description, body, path`. Source: `ref, type`. Person: `username`. Meta: `key='meta', schema_version, last_sync_commit, embedding_model, embedding_dims, synced_at`.
  - CLI: `tk sync [--full] [--embed-missing]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_graph_sync.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.sync import SyncError, Syncer
from teamknowledge.model import Entity, Evidence, Finding
from teamknowledge.propose import ProposalInput, Proposer
from teamknowledge.validate import Validator

pytestmark = pytest.mark.neo4j

EXAMPLE_ID = "01J9XK3M8Q7ZV2W1F4N6B5HT9D"


class BrokenEmbedder:
    model = "fake"
    dims = 64

    def embed(self, texts):
        raise ConnectionError("embedder down")


@pytest.fixture
def world(knowledge_repo, graph):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.", related=["system/example-system"])
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "add gateway")
    knowledge_repo.push("main")
    proposer = Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    syncer = Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), host=host, sleep=lambda s: None)
    return knowledge_repo, graph, host, proposer, syncer


def propose_and_merge(proposer, host, **kw) -> str:
    inp = ProposalInput(kind="dead-end", title=kw.pop("title", "Batch queries over 10k symbols time out"),
                        claim=kw.pop("claim", "Selects over more than ten thousand symbols hit the thirty second gateway timeout."),
                        scope=kw.pop("scope", ["system/kdb-gateway"]),
                        evidence=kw.pop("evidence", [{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}]),
                        confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."}, **kw)
    r = proposer.propose(inp)
    assert r.errors == [], r.errors
    host.approve(r.branch, "bob")
    proposer.repo.sync_main()
    return r.finding_id


def count(graph, label):
    return graph.run(f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"]


def test_first_sync_loads_everything_and_is_idempotent(world):
    repo, graph, host, proposer, syncer = world
    report = syncer.sync()
    assert report.findings_upserted == 1 and report.entities_upserted == 2 and report.embedded == 1
    assert syncer.meta()["last_sync_commit"] == repo.head_sha()
    assert syncer.meta()["embedding_model"] == "fake" and syncer.meta()["embedding_dims"] == 64
    f = graph.run("MATCH (f:Finding {id: $id}) RETURN f", id=EXAMPLE_ID)[0]["f"]
    assert f["kind"] == "dead-end" and f["status"] == "active" and len(f["embedding"]) == 64 and f["embedding_model"] == "fake"
    assert f["created"] == "2026-10-04" and f["observations"] == ["Written by hand on 2026-10-04 as part of the template."]
    edges = graph.run("MATCH (f:Finding {id: $id})-[r]->(x) RETURN type(r) AS t, coalesce(x.ref, x.username) AS k ORDER BY t", id=EXAMPLE_ID)
    assert edges == [{"t": "AUTHORED_BY", "k": "example.author"}, {"t": "SCOPED_TO", "k": "system/example-system"}]
    rel = graph.run("MATCH (a:Entity {ref: 'system/kdb-gateway'})-[:RELATED_TO]->(b) RETURN b.ref AS ref")
    assert rel == [{"ref": "system/example-system"}]
    assert graph.run("MATCH (e:Entity {ref: 'system/kdb-gateway'})-[:OWNED_BY]->(p) RETURN p.username AS u") == [{"u": "bob"}]
    assert syncer.sync().unchanged is True


def test_incremental_sync_after_merge_sets_edges_and_review_metadata(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    fid = propose_and_merge(proposer, host, scope=["system/kdb-gateway", "environment/uat"],
                            evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."},
                                      {"type": "confluence", "ref": "https://confluence.bank/x/KDB", "note": "limits page"}],
                            new_entities=[{"type": "environment", "slug": "uat", "name": "UAT", "description": "User acceptance testing environment for the desk."}])
    report = syncer.sync()
    assert report.findings_upserted == 1 and report.entities_upserted == 1 and report.review_metadata_set == 1
    f = graph.run("MATCH (f:Finding {id: $id}) RETURN f", id=fid)[0]["f"]
    assert f["mr_url"] == "local://merge-requests/1" and f["approved_by"] == ["bob"] and f["merged_at"]
    assert f["commit"] == repo.head_sha()
    cites = graph.run("MATCH (f:Finding {id: $id})-[c:CITES]->(s:Source) RETURN s.ref AS ref, s.type AS type, c.note AS note", id=fid)
    assert cites == [{"ref": "https://confluence.bank/x/KDB", "type": "confluence", "note": "limits page"}]
    assert count(graph, "Entity") == 3 and count(graph, "Finding") == 2


def test_supersede_edges_and_status(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    first = propose_and_merge(proposer, host)
    syncer.sync()
    second = propose_and_merge(proposer, host, title="Batch queries over 5k symbols time out", supersedes=[first])
    report = syncer.sync()
    assert report.findings_upserted == 2  # the new one and the edited old one
    assert graph.run("MATCH (n:Finding {id: $n})-[:SUPERSEDES]->(o) RETURN o.id AS id", n=second) == [{"id": first}]
    old = graph.run("MATCH (f:Finding {id: $id}) RETURN f.status AS s, f.superseded_by AS by", id=first)[0]
    assert old == {"s": "superseded", "by": second}


def test_deleted_file_removes_node(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    path = repo.root / "findings" / f"{EXAMPLE_ID}-example-dead-end.md"
    repo.git("rm", "-q", str(path.relative_to(repo.root)))
    repo.git("commit", "-q", "-m", "remove example")
    repo.push("main")
    report = syncer.sync()
    assert report.findings_deleted == 1
    assert count(graph, "Finding") == 0


def test_embedding_failure_is_not_fatal_and_embed_missing_recovers(world):
    repo, graph, host, proposer, syncer = world
    broken = Syncer(repo, graph, embedder=BrokenEmbedder(), host=host, sleep=lambda s: None)
    report = broken.sync()
    assert report.embedding_failures == 1 and report.warnings and report.findings_upserted == 1
    assert graph.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN count(f) AS c")[0]["c"] == 1
    assert broken.meta()["last_sync_commit"] == repo.head_sha()
    assert syncer.embed_missing() == 1
    assert graph.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN count(f) AS c")[0]["c"] == 0


def test_model_change_requires_full(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    other = Syncer(repo, graph, embedder=FakeEmbedder(dims=32, model="fake-32"), host=host, sleep=lambda s: None)
    with pytest.raises(SyncError, match="tk sync --full"):
        other.sync()
    report = other.sync(full=True)
    assert report.findings_upserted == 1 and other.meta()["embedding_dims"] == 32
    opts = graph.run("SHOW INDEXES YIELD name, options WHERE name = 'finding_embedding' RETURN options")[0]["options"]
    assert opts["indexConfig"]["vector.dimensions"] == 32


def test_validation_error_aborts_without_advancing_meta(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    before = syncer.meta()["last_sync_commit"]
    bad = repo.root / "findings" / "01J9XK3M8Q7ZV2W1F4N6B5HT9Q-bad.md"
    bad.write_text("---\nid: 01J9XK3M8Q7ZV2W1F4N6B5HT9Q\nkind: fact\n---\n")
    repo.commit_files([bad.relative_to(repo.root)], "bad")
    repo.push("main")
    with pytest.raises(SyncError, match="validation errors"):
        syncer.sync()
    assert syncer.meta()["last_sync_commit"] == before


def test_cli_sync(world, monkeypatch, capsys):
    repo, graph, host, proposer, syncer = world
    monkeypatch.setenv("NEO4J_URI", graph.uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", graph.password)
    monkeypatch.setenv("TK_EMBEDDER", "fake")
    monkeypatch.setenv("TK_GITHOST", "local")
    assert main(["sync", "--repo", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert "findings upserted: 1" in out and "entities upserted: 2" in out
    assert main(["sync", "--repo", str(repo.root)]) == 0
    assert "unchanged" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.graph.sync'`

- [ ] **Step 3: Write sync.py**

```python
"""Idempotent sync of the knowledge repo into Neo4j. The graph is a function of the files."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..embed.base import Embedder
from ..githost.base import GitHost
from ..model import Entity, Finding
from ..repo import Change, KnowledgeRepo
from ..validate import Validator
from .client import GraphClient
from .schema import drop_vector_index, init_schema, wipe

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
BATCH = 100


class SyncError(RuntimeError):
    pass


@dataclass
class SyncReport:
    head: str
    unchanged: bool = False
    findings_upserted: int = 0
    entities_upserted: int = 0
    findings_deleted: int = 0
    entities_deleted: int = 0
    embedded: int = 0
    embedding_failures: int = 0
    review_metadata_set: int = 0
    warnings: list[str] = field(default_factory=list)


def text_hash(f: Finding) -> str:
    return hashlib.sha256(f.text_for_embedding().encode()).hexdigest()


def finding_props(f: Finding, commit: str | None) -> dict:
    return {
        "kind": f.kind, "title": f.title, "claim": f.claim, "body": f.body(), "sections": json.dumps(f.sections),
        "applies_when": f.applies_when, "confidence": f.confidence, "status": f.status,
        "retracted_reason": f.retracted_reason, "superseded_by": f.superseded_by, "author": f.author,
        "created": f.created.isoformat(), "review_after": f.review_after.isoformat() if f.review_after else None,
        "path": str(f.path), "commit": commit, "text_hash": text_hash(f), "observations": f.observations(),
    }


def entity_row(e: Entity) -> dict:
    return {
        "ref": e.ref, "type": e.type, "slug": e.slug, "name": e.name, "aliases": list(e.aliases),
        "aliases_text": " ".join(e.aliases), "description": e.description, "body": e.body, "path": str(e.path),
        "related": list(e.related), "owner": list(e.owner), "links": [link.to_dict() for link in e.links],
    }


def _ref_from_path(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


ENTITY_UPSERT = """
UNWIND $rows AS row
MERGE (e:Entity {ref: row.ref})
SET e.type = row.type, e.slug = row.slug, e.name = row.name, e.aliases = row.aliases,
    e.aliases_text = row.aliases_text, e.description = row.description, e.body = row.body, e.path = row.path
WITH e, row
OPTIONAL MATCH (e)-[r:RELATED_TO|OWNED_BY|LINKS_TO]->()
DELETE r
WITH DISTINCT e, row
FOREACH (ref IN row.related | MERGE (o:Entity {ref: ref}) MERGE (e)-[:RELATED_TO]->(o))
FOREACH (u IN row.owner | MERGE (p:Person {username: u}) MERGE (e)-[:OWNED_BY]->(p))
FOREACH (l IN row.links |
  MERGE (s:Source {ref: l.ref}) ON CREATE SET s.type = l.type
  MERGE (e)-[x:LINKS_TO]->(s) SET x.note = l.note)
"""

FINDING_UPSERT = """
UNWIND $rows AS row
MERGE (f:Finding {id: row.id})
SET f += row.props
WITH f, row
OPTIONAL MATCH (f)-[r:SCOPED_TO|CITES|SUPERSEDES|CONTRADICTS|AUTHORED_BY]->()
DELETE r
WITH DISTINCT f, row
FOREACH (ref IN row.scope | MERGE (e:Entity {ref: ref}) MERGE (f)-[:SCOPED_TO]->(e))
FOREACH (c IN row.cites |
  MERGE (s:Source {ref: c.ref}) ON CREATE SET s.type = c.type
  MERGE (f)-[x:CITES]->(s) SET x.note = c.note)
FOREACH (sid IN row.supersedes | MERGE (g:Finding {id: sid}) MERGE (f)-[:SUPERSEDES]->(g))
FOREACH (cid IN row.contradicts | MERGE (g:Finding {id: cid}) MERGE (f)-[:CONTRADICTS]->(g))
MERGE (p:Person {username: row.author})
MERGE (f)-[:AUTHORED_BY]->(p)
"""

EXISTING_EMBEDDINGS = """
UNWIND $ids AS id
MATCH (f:Finding {id: id})
RETURN f.id AS id, f.text_hash AS text_hash, f.embedding_model AS model, f.embedding IS NOT NULL AS has
"""

REVIEW_METADATA = """
UNWIND $rows AS row
MATCH (f:Finding {id: row.id})
SET f.mr_url = row.mr_url, f.approved_by = row.approved_by, f.merged_at = row.merged_at
"""

WRITE_META = """
MERGE (m:Meta {key: 'meta'})
SET m.schema_version = $schema_version, m.last_sync_commit = $sha,
    m.embedding_model = $model, m.embedding_dims = $dims, m.synced_at = datetime()
"""


class Syncer:
    def __init__(self, repo: KnowledgeRepo, client: GraphClient, embedder: Embedder | None = None,
                 host: GitHost | None = None, sleep: Callable[[float], None] = time.sleep):
        self.repo = repo
        self.client = client
        self.embedder = embedder
        self.host = host
        self.sleep = sleep

    def meta(self) -> dict | None:
        rows = self.client.run("MATCH (m:Meta {key: 'meta'}) RETURN m")
        return rows[0]["m"] if rows else None

    # --- main entry points -------------------------------------------------

    def sync(self, full: bool = False) -> SyncReport:
        meta = self.meta()
        model = self.embedder.model if self.embedder else None
        dims = self.embedder.dims if self.embedder else None
        if not full and meta is not None and meta.get("embedding_model") != model:
            raise SyncError(f"embedding model changed (graph has {meta.get('embedding_model')!r}, "
                            f"configured {model!r}); run tk sync --full")
        head = self.repo.head_sha()
        report = SyncReport(head=head)
        findings, entities, errors = self.repo.load_with_errors()
        errors += Validator(self.repo.schema_dir).validate_all(findings, entities)
        if errors:
            e = errors[0]
            raise SyncError(f"{len(errors)} validation errors; first: {e.path}: {e.field}: {e.message}")
        last = meta.get("last_sync_commit") if meta else None
        if full or last is None:
            if full:
                wipe(self.client)
                drop_vector_index(self.client)
            init_schema(self.client, dims)
            changed_f, changed_e, deleted_f, deleted_e = findings, entities, [], []
        else:
            if last == head:
                report.unchanged = True
                return report
            changed_f, changed_e, deleted_f, deleted_e = self._partition(self.repo.changed_files(last, head), findings, entities)
        self._upsert_entities(changed_e)
        report.entities_upserted = len(changed_e)
        if deleted_e:
            self.client.run("UNWIND $refs AS ref MATCH (e:Entity {ref: ref}) DETACH DELETE e", refs=deleted_e)
        report.entities_deleted = len(deleted_e)
        self._upsert_findings(changed_f, report)
        if deleted_f:
            self.client.run("UNWIND $ids AS id MATCH (f:Finding {id: id}) DETACH DELETE f", ids=deleted_f)
        report.findings_deleted = len(deleted_f)
        self._review_metadata(changed_f, report)
        self.client.run(WRITE_META, schema_version=SCHEMA_VERSION, sha=head, model=model, dims=dims)
        return report

    def embed_missing(self) -> int:
        if self.embedder is None:
            raise SyncError("TK_EMBEDDER is none; nothing to embed")
        ids = {r["id"] for r in self.client.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN f.id AS id")}
        findings = [f for f in self.repo.load_findings() if f.id in ids]
        if not findings:
            return 0
        vectors = self._embed_with_retry([f.text_for_embedding() for f in findings])
        rows = [{"id": f.id, "embedding": v, "model": self.embedder.model, "text_hash": text_hash(f)} for f, v in zip(findings, vectors)]
        for i in range(0, len(rows), BATCH):
            self.client.run("UNWIND $rows AS row MATCH (f:Finding {id: row.id}) "
                            "SET f.embedding = row.embedding, f.embedding_model = row.model, f.text_hash = row.text_hash",
                            rows=rows[i:i + BATCH])
        return len(rows)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _partition(changes: list[Change], findings: list[Finding], entities: list[Entity]):
        f_by_path = {str(f.path): f for f in findings}
        e_by_path = {str(e.path): e for e in entities}
        changed_f: list[Finding] = []
        changed_e: list[Entity] = []
        deleted_f: list[str] = []
        deleted_e: list[str] = []
        for c in changes:
            p = str(c.path)
            if c.status == "D":
                if p.startswith("findings/"):
                    deleted_f.append(c.path.stem[:26])
                else:
                    deleted_e.append(_ref_from_path(c.path))
                continue
            if c.status == "R" and c.old_path is not None and str(c.old_path).startswith("entities/"):
                deleted_e.append(_ref_from_path(c.old_path))
            if p in f_by_path:
                changed_f.append(f_by_path[p])
            elif p in e_by_path:
                changed_e.append(e_by_path[p])
        return changed_f, changed_e, deleted_f, deleted_e

    def _upsert_entities(self, entities: list[Entity]) -> None:
        rows = [entity_row(e) for e in entities]
        for i in range(0, len(rows), BATCH):
            self.client.run(ENTITY_UPSERT, rows=rows[i:i + BATCH])

    def _upsert_findings(self, findings: list[Finding], report: SyncReport) -> None:
        if not findings:
            return
        vectors = self._embeddings(findings, report)
        rows = []
        for f in findings:
            props = finding_props(f, self.repo.last_commit_for(f.path))
            if f.id in vectors:
                props["embedding"] = vectors[f.id]
                props["embedding_model"] = self.embedder.model if vectors[f.id] is not None else None
            rows.append({"id": f.id, "props": props, "scope": list(f.scope), "cites": [e.to_dict() for e in f.references()],
                         "supersedes": list(f.supersedes), "contradicts": list(f.contradicts), "author": f.author})
        for i in range(0, len(rows), BATCH):
            self.client.run(FINDING_UPSERT, rows=rows[i:i + BATCH])
        report.findings_upserted = len(rows)

    def _embeddings(self, findings: list[Finding], report: SyncReport) -> dict[str, list[float] | None]:
        """Vectors for findings whose embedding must be (re)computed; ids absent keep their stored vector."""
        if self.embedder is None:
            return {}
        existing = {r["id"]: r for r in self.client.run(EXISTING_EMBEDDINGS, ids=[f.id for f in findings])}
        need = [f for f in findings
                if not ((ex := existing.get(f.id)) and ex["has"] and ex["text_hash"] == text_hash(f) and ex["model"] == self.embedder.model)]
        if not need:
            return {}
        try:
            vectors = self._embed_with_retry([f.text_for_embedding() for f in need])
        except Exception as exc:
            report.embedding_failures = len(need)
            report.warnings.append(f"embedding failed after retries ({exc}); stored without embeddings, run tk sync --embed-missing")
            return {f.id: None for f in need}
        report.embedded = len(need)
        return {f.id: v for f, v in zip(need, vectors)}

    def _embed_with_retry(self, texts: list[str], attempts: int = 3) -> list[list[float]]:
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                return self.embedder.embed(texts)
            except Exception:
                if attempt == attempts:
                    raise
                log.warning("embedding attempt %d failed; retrying in %.0fs", attempt, delay)
                self.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def _review_metadata(self, findings: list[Finding], report: SyncReport) -> None:
        if self.host is None or not findings:
            return
        rows = []
        for f in findings:
            sha = self.repo.last_commit_for(f.path)
            try:
                merge_requests = self.host.merge_requests_for_commit(sha) if sha else []
            except Exception as exc:
                report.warnings.append(f"review metadata lookup failed for {f.id}: {exc}")
                continue
            merged = [m for m in merge_requests if m.state == "merged"]
            if not merged:
                continue
            m = merged[0]
            rows.append({"id": f.id, "mr_url": m.url, "approved_by": list(m.approvers),
                         "merged_at": m.merged_at.isoformat() if m.merged_at else None})
        for i in range(0, len(rows), BATCH):
            self.client.run(REVIEW_METADATA, rows=rows[i:i + BATCH])
        report.review_metadata_set = len(rows)
```

- [ ] **Step 4: Add `tk sync` to cli.py**

Add above `build_parser`:

```python
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
```

In `build_parser` before `return parser`:

```python
    p = sub.add_parser("sync", help="upsert the repo into Neo4j")
    _add_repo_arg(p)
    p.add_argument("--full", action="store_true", help="wipe and rebuild")
    p.add_argument("--embed-missing", action="store_true", help="only fill null embeddings")
    p.set_defaults(func=cmd_sync)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_sync.py -v`
Expected: all pass with Docker. If `test_cli_sync` fails because the local host cannot be built, confirm `TK_GITHOST=local` is set and the clone has an `origin` remote.

- [ ] **Step 6: Commit**

```bash
git add src/teamknowledge/graph/sync.py src/teamknowledge/cli.py tests/test_graph_sync.py
git commit -m "feat: incremental and full sync into Neo4j with embeddings and review metadata"
```

---

### Task 11: Read queries

**Files:**
- Create: `src/teamknowledge/graph/queries.py`
- Test: `tests/test_graph_queries.py`

**Interfaces:**
- Consumes: `GraphClient`, `Embedder`, `Syncer` (tests).
- Produces:
  - `lucene_escape(text: str) -> str`; `rrf(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]`.
  - `@dataclass FindingSummary(id, kind, title, claim, scope: list[str], confidence, status, author, created, wiki_url, score: float | None = None, matched_scope: list[str] = [])` with `to_dict()`.
  - `@dataclass SearchResult(findings: list[FindingSummary], warnings: list[str] = [])` with `to_dict()`.
  - `class Reader(client, embedder=None, wiki_base_url="")` with `findings_for_scope(scope, kinds=(), include_related=True, limit=50) -> list[FindingSummary]`, `search(query, scope=(), kinds=(), limit=10) -> SearchResult`, `get_finding(finding_id) -> dict | None` (keys: every summary key plus `sections: dict`, `applies_when`, `evidence: list[dict]` (observations as `{"type": "observation", "note"}` plus cited sources), `supersedes`, `superseded_by`, `contradicts`, `retracted_reason`, `review_after`, `mr_url`, `approved_by`, `merged_at`), `list_entities(type=None, query=None, limit=20) -> list[dict]` (keys `ref, type, name, aliases, description, owner, active_findings`).

- [ ] **Step 1: Write the failing tests**

`tests/test_graph_queries.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.graph.queries import Reader, lucene_escape, rrf
from teamknowledge.graph.sync import Syncer
from teamknowledge.model import Entity, Evidence, Finding


def test_lucene_escape():
    assert lucene_escape("kdb+ gateway (uat)") == "kdb\\+ OR gateway OR \\(uat\\)"
    assert lucene_escape("timeout AND select") == "timeout OR select"
    assert lucene_escape("") == ""


def test_rrf():
    scores = rrf([["a", "b"], ["b", "c"]], k=60)
    assert scores["b"] > scores["a"] > 0 and scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)


FIDS = [f"01J9XK3M8Q7ZV2W1F4N6B5HT{i:02d}"[:26] for i in range(10, 16)]


def mk(i, kind, title, claim, scope, status="active", **kw):
    sections = {"dead-end": {"Approach": "A.", "Why it fails": "B."}, "fact": {"Detail": "D.", "How to check": "H."},
                "how-to": {"Steps": "S.", "Verify": "V."}}[kind]
    return Finding(id=FIDS[i], kind=kind, title=title, claim=claim, scope=scope,
                   evidence=[Evidence("observation", note="Observed during the 2026-10 investigation.")], confidence="observed",
                   status=status, author="alice", created=date(2026, 10, 1 + i), sections=sections, **kw)


@pytest.fixture
def reader(knowledge_repo, graph):
    ents = [Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], description="Fronts the tick databases for the pricing desk.", related=["project/gamma"]),
            Entity(type="project", slug="gamma", name="Gamma", description="The gamma pricing migration project for rates."),
            Entity(type="environment", slug="uat", name="UAT", description="User acceptance testing environment for the desk.")]
    paths = [knowledge_repo.write_entity(e) for e in ents]
    fs = [
        mk(0, "dead-end", "Batch selects over 10k symbols time out on the gateway", "Large selects exceed the thirty second gateway timeout.", ["system/kdb-gateway", "environment/uat"]),
        mk(1, "fact", "Gamma nightly batch starts at 02:00 London", "The gamma nightly batch is scheduled at two in the morning London time.", ["project/gamma"]),
        mk(2, "how-to", "Chunk symbol lists before querying the gateway", "Split symbol lists into chunks of five thousand and query concurrently.", ["system/kdb-gateway"]),
        mk(3, "fact", "UAT database is refreshed every Sunday", "The UAT database is rebuilt from production every Sunday night.", ["environment/uat"]),
        mk(4, "dead-end", "Retracted claim about gateway caching", "The gateway does not cache query results between calls.", ["system/kdb-gateway"], status="retracted", retracted_reason="Caching was added in version 4.2."),
    ]
    paths += [knowledge_repo.write_finding(f) for f in fs]
    knowledge_repo.commit_files(paths, "seed")
    knowledge_repo.push("main")
    Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), sleep=lambda s: None).sync()
    return Reader(graph, embedder=FakeEmbedder(), wiki_base_url="https://wiki.example/tk/")


pytestmark = pytest.mark.neo4j


def test_findings_for_scope_ranks_direct_before_related(reader):
    out = reader.findings_for_scope(["system/kdb-gateway"])
    ids = [o.id for o in out]
    assert ids[:2] == [FIDS[2], FIDS[0]]           # direct, newest first
    assert FIDS[1] in ids and ids.index(FIDS[1]) > 1  # gamma via RELATED_TO
    assert FIDS[4] not in ids                      # retracted excluded
    assert out[0].wiki_url == f"https://wiki.example/tk/findings/{FIDS[2]}.html"
    assert out[0].matched_scope == ["system/kdb-gateway"]


def test_findings_for_scope_without_related_and_with_kinds(reader):
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway"], include_related=False)] == [FIDS[2], FIDS[0]]
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway", "environment/uat"], kinds=["fact"])] == [FIDS[3], FIDS[1]]
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway", "environment/uat"], kinds=["fact"], include_related=False)] == [FIDS[3]]
    assert reader.findings_for_scope(["system/nothing"]) == []


def test_search_fulltext_and_fused(reader, graph):
    r = reader.search("gateway timeout")
    assert r.warnings == []
    assert FIDS[0] in [f.id for f in r.findings[:2]] and r.findings[0].score is not None
    assert FIDS[4] not in [f.id for f in r.findings]
    r2 = Reader(graph, embedder=None).search("gateway timeout")
    assert r2.findings[0].id == FIDS[0] and "full-text only" in r2.warnings[0]


def test_search_scope_boost(reader):
    plain = reader.search("Sunday gateway")
    boosted = reader.search("Sunday gateway", scope=["environment/uat"])
    assert FIDS[3] in [f.id for f in plain.findings] and FIDS[3] in [f.id for f in boosted.findings]
    uat_plain = next(f.score for f in plain.findings if f.id == FIDS[3])
    uat_boost = next(f.score for f in boosted.findings if f.id == FIDS[3])
    assert uat_boost == pytest.approx(uat_plain * 1.5)


def test_search_kinds_filter_and_empty(reader):
    assert all(f.kind == "fact" for f in reader.search("gateway", kinds=["fact"]).findings)
    assert reader.search("zzzzqqq").findings == []


def test_get_finding(reader):
    d = reader.get_finding(FIDS[0])
    assert d["sections"] == {"Approach": "A.", "Why it fails": "B."}
    assert d["scope"] == ["environment/uat", "system/kdb-gateway"]
    assert d["evidence"] == [{"type": "observation", "note": "Observed during the 2026-10 investigation."}]
    assert d["supersedes"] == [] and d["superseded_by"] is None and d["contradicts"] == []
    assert d["wiki_url"].endswith(f"{FIDS[0]}.html")
    assert reader.get_finding("01J9XK3M8Q7ZV2W1F4N6B5HT99") is None


def test_list_entities(reader):
    by_alias = reader.list_entities(query="gw")
    assert by_alias[0]["ref"] == "system/kdb-gateway" and by_alias[0]["active_findings"] == 2
    envs = reader.list_entities(type="environment")
    assert [e["ref"] for e in envs] == ["environment/uat"]
    assert len(reader.list_entities()) == 4  # includes the template example
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_queries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.graph.queries'`

- [ ] **Step 3: Write queries.py**

```python
"""Read queries over the graph: scope lookup, hybrid search, get, list entities."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from ..embed.base import Embedder
from .client import GraphClient

_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')
RRF_K = 60
SCOPE_BOOST = 1.5


def lucene_escape(text: str) -> str:
    terms = []
    for token in text.split():
        escaped = "".join(("\\" + c) if c in _LUCENE_SPECIAL else c for c in token)
        if escaped and escaped.upper() not in ("AND", "OR", "NOT"):
            terms.append(escaped)
    return " OR ".join(terms)


def rrf(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] += 1.0 / (k + rank)
    return dict(scores)


@dataclass
class FindingSummary:
    id: str
    kind: str
    title: str
    claim: str
    scope: list[str]
    confidence: str
    status: str
    author: str
    created: str
    wiki_url: str
    score: float | None = None
    matched_scope: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    findings: list[FindingSummary]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"findings": [f.to_dict() for f in self.findings], "warnings": self.warnings}


SCOPE_QUERY = """
UNWIND $refs AS ref
MATCH (e:Entity {ref: ref})
OPTIONAL MATCH (e)-[:RELATED_TO]-(r:Entity)
WITH collect(DISTINCT e) AS direct, CASE WHEN $include_related THEN collect(DISTINCT r) ELSE [] END AS rel
WITH direct, [x IN rel WHERE NOT x IN direct] AS related
UNWIND direct + related AS ent
MATCH (f:Finding {status: 'active'})-[:SCOPED_TO]->(ent)
WHERE size($kinds) = 0 OR f.kind IN $kinds
WITH f, sum(CASE WHEN ent IN direct THEN 1 ELSE 0 END) AS direct_hits,
     count(DISTINCT ent) AS hits, collect(DISTINCT ent.ref) AS matched
ORDER BY direct_hits DESC, hits DESC, f.created DESC
LIMIT $limit
MATCH (f)-[:SCOPED_TO]->(s:Entity)
RETURN f, matched, collect(s.ref) AS scope
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('finding_text', $q) YIELD node, score
WHERE node.status = 'active' AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score ORDER BY score DESC LIMIT $k
"""

VECTOR_QUERY = """
CALL db.index.vector.queryNodes('finding_embedding', $k, $vec) YIELD node, score
WHERE node.status = 'active' AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score
"""

HYDRATE_QUERY = """
UNWIND $ids AS id
MATCH (f:Finding {id: id})
OPTIONAL MATCH (f)-[:SCOPED_TO]->(e:Entity)
RETURN f, collect(e.ref) AS scope
"""

GET_QUERY = """
MATCH (f:Finding {id: $id})
OPTIONAL MATCH (f)-[:SCOPED_TO]->(e:Entity)
OPTIONAL MATCH (f)-[c:CITES]->(s:Source)
OPTIONAL MATCH (f)-[:SUPERSEDES]->(old:Finding)
OPTIONAL MATCH (newer:Finding)-[:SUPERSEDES]->(f)
OPTIONAL MATCH (f)-[:CONTRADICTS]->(x:Finding)
OPTIONAL MATCH (y:Finding)-[:CONTRADICTS]->(f)
RETURN f, collect(DISTINCT e.ref) AS scope,
       collect(DISTINCT {type: s.type, ref: s.ref, note: c.note}) AS cites,
       collect(DISTINCT old.id) AS supersedes, collect(DISTINCT newer.id) AS superseded_by,
       collect(DISTINCT x.id) + collect(DISTINCT y.id) AS contradicts
"""

ENTITY_SEARCH = """
CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score
WHERE $type IS NULL OR node.type = $type
OPTIONAL MATCH (fa:Finding {status: 'active'})-[:SCOPED_TO]->(node)
OPTIONAL MATCH (node)-[:OWNED_BY]->(p:Person)
RETURN node, count(DISTINCT fa) AS active_findings, collect(DISTINCT p.username) AS owner, score
ORDER BY score DESC LIMIT $limit
"""

ENTITY_LIST = """
MATCH (node:Entity)
WHERE $type IS NULL OR node.type = $type
OPTIONAL MATCH (fa:Finding {status: 'active'})-[:SCOPED_TO]->(node)
OPTIONAL MATCH (node)-[:OWNED_BY]->(p:Person)
RETURN node, count(DISTINCT fa) AS active_findings, collect(DISTINCT p.username) AS owner
ORDER BY node.type, node.name LIMIT $limit
"""


class Reader:
    def __init__(self, client: GraphClient, embedder: Embedder | None = None, wiki_base_url: str = ""):
        self.client = client
        self.embedder = embedder
        self.wiki_base_url = wiki_base_url.rstrip("/")

    def wiki_url(self, finding_id: str) -> str:
        return f"{self.wiki_base_url}/findings/{finding_id}.html"

    def _summary(self, f: dict, scope: list[str], score: float | None = None, matched: list[str] | None = None) -> FindingSummary:
        return FindingSummary(id=f["id"], kind=f["kind"], title=f["title"], claim=f["claim"], scope=sorted(scope),
                              confidence=f["confidence"], status=f["status"], author=f["author"], created=f["created"],
                              wiki_url=self.wiki_url(f["id"]), score=score, matched_scope=sorted(matched or []))

    def findings_for_scope(self, scope: list[str], kinds: list[str] | tuple[str, ...] = (), include_related: bool = True,
                           limit: int = 50) -> list[FindingSummary]:
        rows = self.client.run(SCOPE_QUERY, refs=list(scope), kinds=list(kinds), include_related=include_related, limit=limit)
        return [self._summary(r["f"], r["scope"], matched=r["matched"]) for r in rows]

    def search(self, query: str, scope: list[str] | tuple[str, ...] = (), kinds: list[str] | tuple[str, ...] = (),
               limit: int = 10) -> SearchResult:
        warnings: list[str] = []
        k = 3 * limit
        lucene = lucene_escape(query)
        fulltext_ids = [r["id"] for r in self.client.run(FULLTEXT_QUERY, q=lucene, kinds=list(kinds), k=k)] if lucene else []
        vector_ids: list[str] = []
        if self.embedder is None:
            warnings.append("semantic search disabled (TK_EMBEDDER=none); results are full-text only")
        else:
            try:
                vec = self.embedder.embed([query])[0]
                vector_ids = [r["id"] for r in self.client.run(VECTOR_QUERY, k=k, vec=vec, kinds=list(kinds))]
            except Exception as exc:
                warnings.append(f"semantic search unavailable ({exc}); results are full-text only")
        fused = rrf([fulltext_ids, vector_ids] if vector_ids else [fulltext_ids])
        if not fused:
            return SearchResult([], warnings)
        rows = self.client.run(HYDRATE_QUERY, ids=list(fused))
        wanted = set(scope)
        out = []
        for r in rows:
            score = fused[r["f"]["id"]]
            matched = sorted(set(r["scope"]) & wanted)
            if matched:
                score *= SCOPE_BOOST
            out.append(self._summary(r["f"], r["scope"], score=score, matched=matched))
        out.sort(key=lambda s: (-s.score, s.id))
        return SearchResult(out[:limit], warnings)

    def get_finding(self, finding_id: str) -> dict | None:
        rows = self.client.run(GET_QUERY, id=finding_id)
        if not rows:
            return None
        r = rows[0]
        f = r["f"]
        evidence = [{"type": "observation", "note": n} for n in f.get("observations") or []]
        evidence += [{k: v for k, v in c.items() if v is not None} for c in r["cites"] if c.get("ref")]
        return {
            **self._summary(f, r["scope"]).to_dict(),
            "sections": json.loads(f.get("sections") or "{}"),
            "applies_when": f.get("applies_when"),
            "evidence": evidence,
            "supersedes": sorted(r["supersedes"]),
            "superseded_by": r["superseded_by"][0] if r["superseded_by"] else None,
            "contradicts": sorted(set(r["contradicts"])),
            "retracted_reason": f.get("retracted_reason"),
            "review_after": f.get("review_after"),
            "mr_url": f.get("mr_url"),
            "approved_by": f.get("approved_by") or [],
            "merged_at": f.get("merged_at"),
        }

    def list_entities(self, type: str | None = None, query: str | None = None, limit: int = 20) -> list[dict]:
        if query:
            lucene = lucene_escape(query)
            rows = self.client.run(ENTITY_SEARCH, q=lucene, type=type, limit=limit) if lucene else []
        else:
            rows = self.client.run(ENTITY_LIST, type=type, limit=limit)
        out = []
        for r in rows:
            n = r["node"]
            out.append({"ref": n["ref"], "type": n["type"], "name": n["name"], "aliases": n.get("aliases") or [],
                        "description": n["description"], "owner": sorted(r["owner"]), "active_findings": r["active_findings"]})
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_queries.py -v`
Expected: all pass with Docker. If `test_findings_for_scope_ranks_direct_before_related` fails on ordering, print the `direct_hits`, `hits`, and `created` columns by running `SCOPE_QUERY` in the test with `RETURN f.id, direct_hits, hits` to see which tier each finding landed in; the two direct findings must have `direct_hits = 1` and the gamma finding `0`.

- [ ] **Step 5: Commit**

```bash
git add src/teamknowledge/graph/queries.py tests/test_graph_queries.py
git commit -m "feat: read queries with scope lookup and hybrid full-text plus vector search"
```

---

### Task 12: Static wiki render, `tk render`

**Files:**
- Create: `src/teamknowledge/render/__init__.py` (empty)
- Create: `src/teamknowledge/render/build.py`
- Create: `src/teamknowledge/render/templates/base.html`
- Create: `src/teamknowledge/render/templates/_macros.html`
- Create: `src/teamknowledge/render/templates/index.html`
- Create: `src/teamknowledge/render/templates/finding.html`
- Create: `src/teamknowledge/render/templates/entity.html`
- Create: `src/teamknowledge/render/templates/kind.html`
- Create: `src/teamknowledge/render/templates/sources.html`
- Modify: `src/teamknowledge/cli.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `KnowledgeRepo`, `Finding`, `Entity`.
- Produces: `render_site(repo: KnowledgeRepo, out: Path) -> list[Path]` returning the written files; `KIND_ORDER = ["dead-end", "caveat", "how-to", "decision", "fact"]`. CLI `tk render --out DIR`.
- All links inside the site are relative, computed from a per-page `root` prefix (`""`, `"../"`, or `"../../"`), so the site works under any base path.

- [ ] **Step 1: Write the failing tests**

`tests/test_render.py`:

```python
import re
from datetime import date
from pathlib import Path

from teamknowledge.cli import main
from teamknowledge.model import Entity, Evidence, Finding
from teamknowledge.render.build import render_site

EXAMPLE_ID = "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
FID = "01J9XK3M8Q7ZV2W1F4N6B5HT9K"


def seed(repo):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], owner=["bob"],
               description="Fronts the tick databases for the pricing desk.", related=["system/example-system"],
               links=[Evidence("confluence", ref="https://confluence.bank/x/KDB-GW")])
    f = Finding(id=FID, kind="fact", title="Prod gateway timeout is 120s",
                claim="The production gateway timeout is one hundred and twenty seconds.", scope=["system/kdb-gateway"],
                evidence=[Evidence("observation", note="Read from the gateway config on 2026-10-04."),
                          Evidence("confluence", ref="https://confluence.bank/x/KDB-GW", note="limits section")],
                confidence="observed", status="active", author="alice", created=date(2026, 10, 4),
                sections={"Detail": "Set in `gw.q` as **120**.", "How to check": "Read the config."}, contradicts=[EXAMPLE_ID])
    repo.write_entity(e)
    repo.write_finding(f)


def test_render_writes_all_pages(knowledge_repo, tmp_path):
    seed(knowledge_repo)
    out = tmp_path / "public"
    written = render_site(knowledge_repo, out)
    rel = {str(p.relative_to(out)) for p in written}
    assert {"index.html", "sources.html", f"findings/{FID}.html", f"findings/{EXAMPLE_ID}.html",
            "entities/system/kdb-gateway.html", "entities/system/example-system.html",
            "kinds/fact.html", "kinds/dead-end.html"} <= rel
    assert "kinds/caveat.html" in rel  # every kind page exists even when empty


def test_finding_page_content(knowledge_repo, tmp_path):
    seed(knowledge_repo)
    render_site(knowledge_repo, tmp_path / "public")
    html = (tmp_path / "public" / "findings" / f"{FID}.html").read_text()
    assert "Prod gateway timeout is 120s" in html and "<h2>Detail</h2>" in html and "<strong>120</strong>" in html
    assert 'href="../entities/system/kdb-gateway.html"' in html
    assert 'href="https://confluence.bank/x/KDB-GW"' in html and "limits section" in html
    assert f'href="../findings/{EXAMPLE_ID}.html"' in html  # contradicts link
    assert "alice" in html and "2026-10-04" in html and "observed" in html


def test_entity_page_groups_findings_and_lists_related(knowledge_repo, tmp_path):
    seed(knowledge_repo)
    render_site(knowledge_repo, tmp_path / "public")
    html = (tmp_path / "public" / "entities" / "system" / "kdb-gateway.html").read_text()
    assert "<h2>fact</h2>" in html and f'href="../../findings/{FID}.html"' in html
    assert 'href="../../entities/system/example-system.html"' in html and "bob" in html and "gw" in html


def test_sources_page_backreferences(knowledge_repo, tmp_path):
    seed(knowledge_repo)
    render_site(knowledge_repo, tmp_path / "public")
    html = (tmp_path / "public" / "sources.html").read_text()
    assert "https://confluence.bank/x/KDB-GW" in html and "Prod gateway timeout is 120s" in html and "kdb+ gateway" in html


def test_no_scripts_or_external_assets_and_links_resolve(knowledge_repo, tmp_path):
    seed(knowledge_repo)
    out = tmp_path / "public"
    render_site(knowledge_repo, out)
    for page in out.rglob("*.html"):
        html = page.read_text()
        assert "<script" not in html and "<link" not in html
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith("http"):
                continue
            assert (page.parent / href).resolve().exists(), f"{page}: {href}"


def test_cli_render(knowledge_repo, tmp_path, capsys):
    assert main(["render", "--repo", str(knowledge_repo.root), "--out", str(tmp_path / "site")]) == 0
    assert (tmp_path / "site" / "index.html").exists()
    assert "pages" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.render'`

- [ ] **Step 3: Write the templates**

`src/teamknowledge/render/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Team knowledge{% endblock %}</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}
nav a{margin-right:1rem}
h1,h2,h3{line-height:1.25}
.kind{display:inline-block;padding:.05rem .5rem;border-radius:.3rem;font-size:.8rem;background:#e8ecf8;color:#223}
.status-superseded,.status-retracted{opacity:.6}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1.25rem}
dt{font-weight:600}
ul.findings{list-style:none;padding:0}
ul.findings li{margin:.6rem 0}
.muted{color:#666;font-size:.9rem}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ddd;padding:.3rem .6rem;text-align:left;vertical-align:top}
code{background:#f4f4f4;padding:0 .25rem}
</style>
</head>
<body>
<nav>
  <a href="{{ root }}index.html">Home</a>
  <a href="{{ root }}sources.html">Sources</a>
  {% for k in kinds %}<a href="{{ root }}kinds/{{ k }}.html">{{ k }}</a>{% endfor %}
</nav>
{% block body %}{% endblock %}
</body>
</html>
```

`src/teamknowledge/render/templates/_macros.html`:

```html
{% macro finding_item(f, root) -%}
<li class="status-{{ f.status }}">
  <span class="kind">{{ f.kind }}</span>
  <a href="{{ root }}findings/{{ f.id }}.html">{{ f.title }}</a>
  <div class="muted">{{ f.claim }} &mdash; {{ f.author }}, {{ f.created }}{% if f.status != 'active' %} ({{ f.status }}){% endif %}</div>
</li>
{%- endmacro %}

{% macro finding_list(findings, root) -%}
<ul class="findings">{% for f in findings %}{{ finding_item(f, root) }}{% endfor %}</ul>
{%- endmacro %}

{% macro entity_link(ref, entities, root) -%}
{% if ref in entities %}<a href="{{ root }}entities/{{ ref }}.html">{{ entities[ref].name }}</a>{% else %}<code>{{ ref }}</code>{% endif %}
{%- endmacro %}
```

`src/teamknowledge/render/templates/index.html`:

```html
{% extends "base.html" %}{% import "_macros.html" as m %}
{% block body %}
<h1>Team knowledge</h1>
<table>
<tr><th>Kind</th><th>Active</th><th>Superseded</th><th>Retracted</th></tr>
{% for k in kinds %}<tr><td><a href="kinds/{{ k }}.html">{{ k }}</a></td><td>{{ counts[k]['active'] }}</td><td>{{ counts[k]['superseded'] }}</td><td>{{ counts[k]['retracted'] }}</td></tr>{% endfor %}
</table>
<h2>Newest findings</h2>
{{ m.finding_list(newest, root) }}
<h2>Entities</h2>
{% for type, items in entities_by_type.items() %}
<h3>{{ type }}</h3>
<ul>{% for e in items %}<li><a href="entities/{{ e.ref }}.html">{{ e.name }}</a> <span class="muted">{{ e.description }}</span></li>{% endfor %}</ul>
{% endfor %}
{% endblock %}
```

`src/teamknowledge/render/templates/finding.html`:

```html
{% extends "base.html" %}{% import "_macros.html" as m %}
{% block title %}{{ f.title }}{% endblock %}
{% block body %}
<p><span class="kind">{{ f.kind }}</span> <span class="muted">{{ f.status }}</span></p>
<h1>{{ f.title }}</h1>
<p>{{ f.claim }}</p>
<dl>
  <dt>Scope</dt><dd>{% for ref in f.scope %}{{ m.entity_link(ref, entities, root) }}{% if not loop.last %}, {% endif %}{% endfor %}</dd>
  {% if f.applies_when %}<dt>Applies when</dt><dd>{{ f.applies_when }}</dd>{% endif %}
  <dt>Confidence</dt><dd>{{ f.confidence }}</dd>
  <dt>Author</dt><dd>{{ f.author }}, {{ f.created }}</dd>
  {% if f.review_after %}<dt>Review after</dt><dd>{{ f.review_after }}</dd>{% endif %}
  {% if f.retracted_reason %}<dt>Retracted</dt><dd>{{ f.retracted_reason }}</dd>{% endif %}
  {% if f.superseded_by %}<dt>Superseded by</dt><dd><a href="{{ root }}findings/{{ f.superseded_by }}.html">{{ titles.get(f.superseded_by, f.superseded_by) }}</a></dd>{% endif %}
  {% if f.supersedes %}<dt>Supersedes</dt><dd>{% for i in f.supersedes %}<a href="{{ root }}findings/{{ i }}.html">{{ titles.get(i, i) }}</a>{% if not loop.last %}, {% endif %}{% endfor %}</dd>{% endif %}
  {% if f.contradicts or contradicted_by %}<dt>Contradicts</dt><dd>{% for i in f.contradicts + contradicted_by %}<a href="{{ root }}findings/{{ i }}.html">{{ titles.get(i, i) }}</a>{% if not loop.last %}, {% endif %}{% endfor %}</dd>{% endif %}
</dl>
{% for heading, text in f.sections.items() %}
<h2>{{ heading }}</h2>
{{ text | md }}
{% endfor %}
<h2>Evidence</h2>
<ul>
{% for e in f.evidence %}
  <li>{% if e.ref %}<a href="{{ e.ref }}">{{ e.ref }}</a>{% else %}{{ e.type }}{% endif %}{% if e.note %} &mdash; {{ e.note }}{% endif %}</li>
{% endfor %}
</ul>
{% endblock %}
```

`src/teamknowledge/render/templates/entity.html`:

```html
{% extends "base.html" %}{% import "_macros.html" as m %}
{% block title %}{{ e.name }}{% endblock %}
{% block body %}
<p class="muted">{{ e.type }}</p>
<h1>{{ e.name }}</h1>
<p>{{ e.description }}</p>
{% if e.body %}{{ e.body | md }}{% endif %}
<dl>
  {% if e.aliases %}<dt>Aliases</dt><dd>{{ e.aliases | join(", ") }}</dd>{% endif %}
  {% if e.owner %}<dt>Owners</dt><dd>{{ e.owner | join(", ") }}</dd>{% endif %}
  {% if e.related %}<dt>Related</dt><dd>{% for ref in e.related %}{{ m.entity_link(ref, entities, root) }}{% if not loop.last %}, {% endif %}{% endfor %}</dd>{% endif %}
  {% if e.links %}<dt>Links</dt><dd>{% for l in e.links %}<a href="{{ l.ref }}">{{ l.ref }}</a>{% if l.note %} ({{ l.note }}){% endif %}{% if not loop.last %}, {% endif %}{% endfor %}</dd>{% endif %}
</dl>
{% for k in kinds %}{% if active_by_kind[k] %}
<h2>{{ k }}</h2>
{{ m.finding_list(active_by_kind[k], root) }}
{% endif %}{% endfor %}
{% if inactive %}
<h2>Superseded and retracted</h2>
{{ m.finding_list(inactive, root) }}
{% endif %}
{% endblock %}
```

`src/teamknowledge/render/templates/kind.html`:

```html
{% extends "base.html" %}{% import "_macros.html" as m %}
{% block title %}{{ kind }}{% endblock %}
{% block body %}
<h1>{{ kind }}</h1>
<p class="muted">{{ description }}</p>
{% if findings %}{{ m.finding_list(findings, root) }}{% else %}<p class="muted">No active findings of this kind yet.</p>{% endif %}
{% endblock %}
```

`src/teamknowledge/render/templates/sources.html`:

```html
{% extends "base.html" %}
{% block title %}Sources{% endblock %}
{% block body %}
<h1>Sources</h1>
{% for type, items in sources_by_type.items() %}
<h2>{{ type }}</h2>
<table>
<tr><th>Reference</th><th>Cited by</th></tr>
{% for ref, s in items %}
<tr><td><a href="{{ ref }}">{{ ref }}</a></td>
<td>{% for f, note in s.findings %}<div><a href="findings/{{ f.id }}.html">{{ f.title }}</a>{% if note %} <span class="muted">({{ note }})</span>{% endif %}</div>{% endfor %}
{% for e, note in s.entities %}<div><a href="entities/{{ e.ref }}.html">{{ e.name }}</a>{% if note %} <span class="muted">({{ note }})</span>{% endif %}</div>{% endfor %}</td></tr>
{% endfor %}
</table>
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: Write build.py**

```python
"""Static wiki from the repo files. Reads files, never Neo4j, so it builds when the database is down."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from ..model import Entity, Finding
from ..repo import KnowledgeRepo

KIND_ORDER = ["dead-end", "caveat", "how-to", "decision", "fact"]
STATUSES = ["active", "superseded", "retracted"]


def _md(text: str | None) -> Markup:
    return Markup(markdown.markdown(text or "", extensions=["fenced_code", "tables"]))


def _env() -> Environment:
    env = Environment(loader=PackageLoader("teamknowledge.render", "templates"), autoescape=select_autoescape(["html"]))
    env.filters["md"] = _md
    return env


def render_site(repo: KnowledgeRepo, out: Path) -> list[Path]:
    out = Path(out)
    env = _env()
    findings = sorted(repo.load_findings(), key=lambda f: (f.created, f.id), reverse=True)
    entities = sorted(repo.load_entities(), key=lambda e: (e.type, e.name.lower()))
    kinds_spec = yaml.safe_load((repo.schema_dir / "kinds.yaml").read_text())["kinds"]
    kinds = [k for k in KIND_ORDER if k in kinds_spec] + [k for k in kinds_spec if k not in KIND_ORDER]
    ent_by_ref = {e.ref: e for e in entities}
    titles = {f.id: f.title for f in findings}

    contradicted_by: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        for cid in f.contradicts:
            contradicted_by[cid].append(f.id)

    by_entity: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        for ref in f.scope:
            by_entity[ref].append(f)

    sources: dict[str, dict] = {}
    for f in findings:
        for ev in f.references():
            s = sources.setdefault(ev.ref, {"type": ev.type, "findings": [], "entities": []})
            s["findings"].append((f, ev.note))
    for e in entities:
        for link in e.links:
            s = sources.setdefault(link.ref, {"type": link.type, "findings": [], "entities": []})
            s["entities"].append((e, link.note))
    sources_by_type: dict[str, list] = defaultdict(list)
    for ref, s in sorted(sources.items()):
        sources_by_type[s["type"]].append((ref, s))

    counts = {k: {st: sum(1 for f in findings if f.kind == k and f.status == st) for st in STATUSES} for k in kinds}
    entities_by_type: dict[str, list[Entity]] = defaultdict(list)
    for e in entities:
        entities_by_type[e.type].append(e)

    written: list[Path] = []

    def write(rel: str, template: str, root: str, **ctx) -> None:
        page = out / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(env.get_template(template).render(root=root, kinds=kinds, entities=ent_by_ref, **ctx))
        written.append(page)

    write("index.html", "index.html", "", counts=counts, newest=[f for f in findings if f.status == "active"][:20],
          entities_by_type=dict(entities_by_type))
    write("sources.html", "sources.html", "", sources_by_type=dict(sources_by_type))
    for f in findings:
        write(f"findings/{f.id}.html", "finding.html", "../", f=f, titles=titles, contradicted_by=contradicted_by.get(f.id, []))
    for e in entities:
        mine = by_entity.get(e.ref, [])
        active_by_kind = {k: [f for f in mine if f.kind == k and f.status == "active"] for k in kinds}
        write(f"entities/{e.ref}.html", "entity.html", "../../", e=e, active_by_kind=active_by_kind,
              inactive=[f for f in mine if f.status != "active"])
    for k in kinds:
        write(f"kinds/{k}.html", "kind.html", "../", kind=k, description=kinds_spec[k].get("description", ""),
              findings=[f for f in findings if f.kind == k and f.status == "active"])
    return written
```

- [ ] **Step 5: Add `tk render` to cli.py**

Add above `build_parser`:

```python
def cmd_render(args: argparse.Namespace) -> int:
    from .render.build import render_site
    from .repo import KnowledgeRepo

    written = render_site(KnowledgeRepo(args.repo), args.out)
    print(f"rendered {len(written)} pages to {args.out}")
    return 0
```

In `build_parser` before `return parser`:

```python
    p = sub.add_parser("render", help="build the static wiki")
    _add_repo_arg(p)
    p.add_argument("--out", type=Path, default=Path("public"))
    p.set_defaults(func=cmd_render)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: all pass. `markupsafe` is a dependency of Jinja2, so the import needs no new requirement.

- [ ] **Step 7: Commit**

```bash
git add src/teamknowledge/render src/teamknowledge/cli.py tests/test_render.py
git commit -m "feat: static wiki render from repo files, tk render"
```

---

### Task 13: MCP server, `tk serve-mcp`

**Files:**
- Create: `src/teamknowledge/mcp_server.py`
- Modify: `src/teamknowledge/settings.py` (add `make_host`)
- Modify: `src/teamknowledge/cli.py` (`_host_from_env` delegates to `make_host`; add `serve-mcp`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `Proposer`, `ProposalInput`; `Reader`; `GitHost`; `Settings`, `make_graph_client`, `make_embedder_from`; `KnowledgeRepo`; `Validator`.
- Produces:
  - `settings.make_host(s: Settings, repo: KnowledgeRepo) -> GitHost` (raises `SystemExit` with a clear message when misconfigured).
  - `@dataclass Services(proposer: Proposer | None, reader: Reader | None, host: GitHost | None, author: str, wiki_base_url: str = "")`.
  - `build_services(s: Settings) -> Services`; `build_server(services: Services) -> FastMCP`; `main() -> None` runs stdio.
  - Tools: `propose_finding`, `amend_finding`, `retract_finding`, `findings_for_scope`, `search_findings`, `get_finding`, `list_entities`, `my_proposals`. Each returns a dict. Read tools return `{"error": "knowledge graph unreachable: ...", "wiki_url": ...}` on database failure and `{"error": "read tools disabled: NEO4J_URI not set"}` when no reader is configured. Write tools return `{"error": "write tools disabled: TK_REPO not set"}` when no proposer is configured.
  - CLI: `tk serve-mcp`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py`:

```python
import json
from datetime import date
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.client import GraphClient
from teamknowledge.graph.queries import Reader
from teamknowledge.graph.sync import Syncer
from teamknowledge.mcp_server import Services, build_server
from teamknowledge.model import Entity
from teamknowledge.propose import Proposer
from teamknowledge.validate import Validator
from teamknowledge.cli import build_parser

TOOLS = {"propose_finding", "amend_finding", "retract_finding", "findings_for_scope", "search_findings",
         "get_finding", "list_entities", "my_proposals"}


async def call(server, name, args):
    async with connect(server._mcp_server) as client:
        result = await client.call_tool(name, args)
        return json.loads(result.content[0].text)


@pytest.fixture
def writer_services(knowledge_repo):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.")
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "gateway")
    knowledge_repo.push("main")
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    proposer = Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    return Services(proposer=proposer, reader=None, host=host, author="alice", wiki_base_url="https://wiki.example/tk")


GOOD = {"kind": "dead-end", "title": "Batch queries over 10k symbols time out",
        "claim": "Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        "scope": ["system/kdb-gateway"], "evidence": [{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
        "confidence": "observed", "sections": {"Approach": "One select.", "Why it fails": "Timeout."}}


async def test_lists_all_tools(writer_services):
    server = build_server(writer_services)
    async with connect(server._mcp_server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools.tools} == TOOLS


async def test_propose_amend_retract_and_my_proposals(writer_services):
    server = build_server(writer_services)
    r = await call(server, "propose_finding", GOOD)
    assert r["merge_request_url"] == "local://merge-requests/1" and r["reviewers"] == ["bob"]
    mine = await call(server, "my_proposals", {})
    assert mine["proposals"][0]["branch"] == r["branch"] and mine["proposals"][0]["state"] == "open"
    bad = await call(server, "propose_finding", {**GOOD, "scope": ["system/kdb-gw"]})
    assert bad["errors"][0]["field"] == "scope[0]"
    writer_services.host.approve(r["branch"], "bob")
    a = await call(server, "amend_finding", {"finding_id": r["finding_id"], "reason": "tighten", "header_changes": {"confidence": "inferred"}})
    assert a["branch"].startswith("amend/")
    x = await call(server, "retract_finding", {"finding_id": r["finding_id"], "reason": "the gateway was upgraded and the limit is gone"})
    assert x["branch"] == f"retract/{r['finding_id']}"


async def test_read_tools_disabled_without_reader(writer_services):
    server = build_server(writer_services)
    r = await call(server, "search_findings", {"query": "x"})
    assert "read tools disabled" in r["error"]


async def test_write_tools_disabled_without_proposer():
    server = build_server(Services(proposer=None, reader=None, host=None, author="x"))
    r = await call(server, "propose_finding", GOOD)
    assert "write tools disabled" in r["error"]


async def test_unreachable_graph_reports_wiki_url():
    client = GraphClient("bolt://127.0.0.1:1", "neo4j", "x")
    services = Services(proposer=None, reader=Reader(client, wiki_base_url="https://wiki.example/tk"), host=None, author="x",
                        wiki_base_url="https://wiki.example/tk")
    r = await call(build_server(services), "findings_for_scope", {"scope": ["system/a"]})
    assert "unreachable" in r["error"] and r["wiki_url"] == "https://wiki.example/tk"


@pytest.mark.neo4j
async def test_read_tools_against_graph(knowledge_repo, graph):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"],
               description="The q process that fronts the tick databases for the pricing desk.")
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "gateway")
    knowledge_repo.push("main")
    Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), sleep=lambda s: None).sync()
    reader = Reader(graph, embedder=FakeEmbedder(), wiki_base_url="https://wiki.example/tk")
    server = build_server(Services(proposer=None, reader=reader, host=None, author="x", wiki_base_url="https://wiki.example/tk"))
    scoped = await call(server, "findings_for_scope", {"scope": ["system/example-system"]})
    assert scoped["findings"][0]["id"] == "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
    found = await call(server, "search_findings", {"query": "template validates"})
    assert found["findings"][0]["id"] == "01J9XK3M8Q7ZV2W1F4N6B5HT9D" and found["warnings"] == []
    one = await call(server, "get_finding", {"finding_id": "01J9XK3M8Q7ZV2W1F4N6B5HT9D"})
    assert one["sections"]["Approach"].startswith("Keep an empty")
    missing = await call(server, "get_finding", {"finding_id": "01J9XK3M8Q7ZV2W1F4N6B5HT99"})
    assert "not found" in missing["error"]
    ents = await call(server, "list_entities", {"query": "gw"})
    assert ents["entities"][0]["ref"] == "system/kdb-gateway"


def test_serve_mcp_subcommand_exists():
    args = build_parser().parse_args(["serve-mcp"])
    assert args.command == "serve-mcp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.mcp_server'`

- [ ] **Step 3: Add `make_host` to settings.py**

Append to `settings.py`:

```python
def make_host(s: Settings, repo):
    from pathlib import Path

    from .githost.gitlab import GitLabHost
    from .githost.local import LocalGitHost

    if s.githost == "local":
        return LocalGitHost(Path(repo.remote_url()), author=s.author)
    if s.githost == "gitlab":
        cfg = repo.config.get("gitlab", {})
        if not s.gitlab_token:
            raise SystemExit("GITLAB_TOKEN is required when TK_GITHOST=gitlab")
        return GitLabHost(s.gitlab_url, s.gitlab_token, cfg["project"], mode=s.gitlab_mode,
                          target_branch=cfg.get("target_branch", "main"))
    raise SystemExit(f"TK_GITHOST must be 'local' or 'gitlab', got {s.githost!r}")
```

Replace the body of `_host_from_env` in `cli.py` with:

```python
    from .settings import Settings, make_host

    return make_host(Settings.from_env(), repo)
```

- [ ] **Step 4: Write mcp_server.py**

```python
"""The team-knowledge MCP server. Runs locally over stdio inside each person's Claude Code.

Write tools open merge requests through the git host. Read tools query Neo4j, which holds
accepted findings only. The server never writes to Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from .githost.base import GitHost
from .graph.queries import Reader
from .propose import ProposalInput, Proposer
from .settings import Settings, make_embedder_from, make_graph_client, make_host

INSTRUCTIONS = (
    "Team Knowledge holds structured, reviewed findings scoped by entities (project, system, environment, desk, tool). "
    "Use findings_for_scope and search_findings to look things up; use list_entities to resolve scope refs before proposing. "
    "propose_finding opens a merge request that a colleague reviews; nothing is visible to others until it is merged."
)


@dataclass
class Services:
    proposer: Proposer | None
    reader: Reader | None
    host: GitHost | None
    author: str
    wiki_base_url: str = ""


def build_services(s: Settings) -> Services:
    from .repo import KnowledgeRepo
    from .validate import Validator

    repo = KnowledgeRepo(s.repo) if s.repo else None
    wiki = (repo.config.get("wiki", {}) or {}).get("base_url", "") if repo else ""
    host = make_host(s, repo) if repo else None
    proposer = Proposer(repo, Validator(repo.schema_dir), host, author=s.author) if repo and host else None
    reader = Reader(make_graph_client(s), make_embedder_from(s), wiki) if s.neo4j_uri else None
    return Services(proposer=proposer, reader=reader, host=host, author=s.author, wiki_base_url=wiki)


def build_server(s: Services) -> FastMCP:
    mcp = FastMCP("team-knowledge", instructions=INSTRUCTIONS)

    def need_writer() -> dict | None:
        return None if s.proposer else {"error": "write tools disabled: TK_REPO not set"}

    def read(fn) -> dict:
        if s.reader is None:
            return {"error": "read tools disabled: NEO4J_URI not set", "wiki_url": s.wiki_base_url}
        try:
            return fn()
        except Exception as exc:  # driver errors, network errors
            return {"error": f"knowledge graph unreachable: {type(exc).__name__}: {exc}", "wiki_url": s.wiki_base_url}

    @mcp.tool()
    def propose_finding(kind: str, title: str, claim: str, scope: list[str], evidence: list[dict[str, Any]],
                        confidence: str, sections: dict[str, str], applies_when: str | None = None,
                        supersedes: list[str] | None = None, review_after: str | None = None,
                        new_entities: list[dict[str, Any]] | None = None) -> dict:
        """Propose a new finding as a merge request.

        kind: dead-end | caveat | how-to | decision | fact. scope: entity refs like system/kdb-gateway (resolve with
        list_entities; add missing ones in new_entities with type, slug, name, description). evidence: at least one item,
        {"type": "observation", "note": ...} or {"type": "confluence|gitlab|runbook|url", "ref": ..., "note": ...}.
        confidence: observed | inferred | reported. sections: the kind's required headings mapped to markdown text.
        Returns finding_id, branch, merge_request_url, reviewers, or {"errors": [...]} with field, message, suggestion.
        """
        if err := need_writer():
            return err
        return s.proposer.propose(ProposalInput(kind=kind, title=title, claim=claim, scope=scope, evidence=evidence,
                                                confidence=confidence, sections=sections, applies_when=applies_when,
                                                supersedes=supersedes or [], review_after=review_after,
                                                new_entities=new_entities or [])).to_dict()

    @mcp.tool()
    def amend_finding(finding_id: str, reason: str, header_changes: dict[str, Any] | None = None,
                      sections: dict[str, str] | None = None) -> dict:
        """Open a merge request correcting an accepted finding without changing its claim's identity.

        header_changes may contain title, claim, scope, applies_when, evidence, confidence, contradicts, review_after.
        """
        if err := need_writer():
            return err
        return s.proposer.amend(finding_id, reason, header_changes, sections).to_dict()

    @mcp.tool()
    def retract_finding(finding_id: str, reason: str) -> dict:
        """Open a merge request marking a finding retracted, with the reason."""
        if err := need_writer():
            return err
        return s.proposer.retract(finding_id, reason).to_dict()

    @mcp.tool()
    def findings_for_scope(scope: list[str], kinds: list[str] | None = None, include_related: bool = True,
                           limit: int = 50) -> dict:
        """Active findings scoped to any of the given entity refs, expanded one hop over related entities."""
        return read(lambda: {"findings": [f.to_dict() for f in s.reader.findings_for_scope(scope, kinds or [], include_related, limit)]})

    @mcp.tool()
    def search_findings(query: str, scope: list[str] | None = None, kinds: list[str] | None = None, limit: int = 10) -> dict:
        """Hybrid full-text and semantic search over active findings; scope refs boost matches."""
        return read(lambda: s.reader.search(query, scope or [], kinds or [], limit).to_dict())

    @mcp.tool()
    def get_finding(finding_id: str) -> dict:
        """The full finding: sections, evidence, review metadata, supersedes and contradicts links."""
        def go():
            d = s.reader.get_finding(finding_id)
            return d if d is not None else {"error": f"finding {finding_id} not found"}
        return read(go)

    @mcp.tool()
    def list_entities(type: str | None = None, query: str | None = None, limit: int = 20) -> dict:
        """Resolve the entity vocabulary by type and fuzzy name or alias; use before proposing."""
        return read(lambda: {"entities": s.reader.list_entities(type, query, limit)})

    @mcp.tool()
    def my_proposals() -> dict:
        """The caller's open merge requests on the knowledge repo."""
        if s.host is None:
            return {"error": "git host not configured"}
        try:
            mrs = s.host.list_open_by(s.author)
        except Exception as exc:
            return {"error": f"git host unreachable: {exc}"}
        return {"proposals": [{"iid": m.iid, "branch": m.branch, "url": m.url, "state": m.state} for m in mrs]}

    return mcp


def main() -> None:
    build_server(build_services(Settings.from_env())).run()
```

- [ ] **Step 5: Add `tk serve-mcp` to cli.py**

Add above `build_parser`:

```python
def cmd_serve_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as serve

    serve()
    return 0
```

In `build_parser` before `return parser`:

```python
    p = sub.add_parser("serve-mcp", help="run the MCP server over stdio")
    p.set_defaults(func=cmd_serve_mcp)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: all pass. If `result.content[0].text` is not JSON because the installed `mcp` version returns structured content separately, use `result.structuredContent` when present and fall back to parsing the text; put that fallback in the `call` helper.

- [ ] **Step 7: Commit**

```bash
git add src/teamknowledge/mcp_server.py src/teamknowledge/settings.py src/teamknowledge/cli.py tests/test_mcp_server.py
git commit -m "feat: MCP server with write and read tools, tk serve-mcp"
```

---

### Task 14: Retrieval evaluation, `tk eval retrieval`

**Files:**
- Create: `src/teamknowledge/eval_fixtures.py`
- Create: `src/teamknowledge/eval_retrieval.py`
- Modify: `src/teamknowledge/cli.py`
- Test: `tests/test_eval.py`

**Interfaces:**
- Consumes: `init_knowledge_repo`, `KnowledgeRepo`, `Syncer`, `Reader`, `VECTOR_QUERY`, `Embedder`, `GraphClient`, `Finding`, `Entity`, `Evidence`.
- Produces:
  - `eval_fixtures.ENTITIES: list[Entity]`, `eval_fixtures.FINDINGS: list[tuple[str, str, str, list[str]]]` (kind, title, claim, scope), `eval_fixtures.QUERIES: list[tuple[str, list[int]]]` (query, expected finding indices), `eval_fixtures.finding_id(i: int) -> str`.
  - `eval_retrieval.run_eval(client: GraphClient, embedder: Embedder | None, workdir: Path) -> dict` with keys `fulltext`, `vector`, `fused` (recall at 3, floats in `[0, 1]`), `queries`, `findings`, `embedder`.
  - CLI: `tk eval retrieval` starts its own Neo4j container (needs the `eval` extra and Docker) and prints the three numbers. It never touches a configured production graph.

- [ ] **Step 1: Write the fixtures**

`src/teamknowledge/eval_fixtures.py`:

```python
"""A small corpus and query set for measuring retrieval. Finance-flavoured, deliberately keyword-rich."""

from __future__ import annotations

from .model import Entity

ENTITIES = [
    Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw", "gateway"], description="The q process that fronts the tick databases for the pricing desk."),
    Entity(type="system", slug="pricing-engine", name="Pricing engine", description="Shared curve construction and pricing service used by gamma and delta."),
    Entity(type="system", slug="risk-batch", name="Risk batch", description="The nightly job that computes risk and PnL for the rates desk."),
    Entity(type="environment", slug="uat", name="UAT", description="User acceptance testing environment, refreshed weekly from production."),
    Entity(type="environment", slug="prod", name="Production", aliases=["prod"], description="The production environment serving the trading desks."),
    Entity(type="project", slug="gamma", name="Gamma", description="The gamma pricing migration project for the rates desk."),
    Entity(type="project", slug="delta", name="Delta", description="The delta market data and snapshot project."),
    Entity(type="tool", slug="kdb", name="kdb+", aliases=["q"], description="The kdb+ time series database and q language."),
    Entity(type="tool", slug="python", name="Python", description="Python, the main application language on the desk."),
    Entity(type="desk", slug="rates", name="Rates desk", description="The interest rates trading desk."),
]

FINDINGS: list[tuple[str, str, str, list[str]]] = [
    ("dead-end", "Batch selects over 10k symbols time out on the UAT gateway", "Large selects over more than ten thousand symbols exceed the thirty second UAT gateway timeout.", ["system/kdb-gateway", "environment/uat"]),
    ("how-to", "Chunk symbol lists at 5k before querying the gateway", "Split symbol lists into chunks of five thousand and run the selects concurrently to stay under the timeout.", ["system/kdb-gateway", "tool/kdb"]),
    ("fact", "Prod gateway timeout is 120 seconds", "The production gateway enforces a one hundred and twenty second timeout per call.", ["system/kdb-gateway", "environment/prod"]),
    ("caveat", "Gateway returns an empty table rather than an error on timeout", "When a call times out the gateway returns an empty table instead of raising, so callers must check row counts.", ["system/kdb-gateway"]),
    ("fact", "Gamma nightly batch starts at 02:00 London", "The gamma nightly batch is scheduled at two in the morning London time and takes about ninety minutes.", ["project/gamma", "system/risk-batch"]),
    ("dead-end", "Running the risk batch twice in one night corrupts the PnL cache", "A second risk batch run on the same date overwrites the PnL cache with partial results.", ["system/risk-batch"]),
    ("how-to", "Rerun a failed risk batch safely", "Clear the PnL cache for the date, then rerun the batch with the rerun flag set.", ["system/risk-batch"]),
    ("caveat", "UAT database is refreshed from prod every Sunday", "The UAT database is rebuilt from production every Sunday night, so UAT-only test data disappears weekly.", ["environment/uat"]),
    ("decision", "Gamma uses Python 3.12 rather than 3.11", "Gamma standardised on Python three point twelve for the pattern matching and performance improvements.", ["project/gamma", "tool/python"]),
    ("dead-end", "pandas to_sql is too slow for loading curves into the pricing engine", "Loading yield curves with pandas to_sql takes forty minutes; use the bulk copy loader instead.", ["system/pricing-engine", "tool/python"]),
    ("how-to", "Bulk load curves into the pricing engine", "Write curves to a CSV and call the bulk copy loader, which finishes in under a minute.", ["system/pricing-engine"]),
    ("fact", "Pricing engine rejects curves with duplicate tenors", "The pricing engine validates curve tenors and rejects any curve where a tenor appears twice.", ["system/pricing-engine"]),
    ("caveat", "Pricing engine silently uses yesterday's curve when today's is missing", "If today's curve is absent the pricing engine falls back to the previous business day without warning.", ["system/pricing-engine", "environment/prod"]),
    ("decision", "Delta stores market data snapshots in kdb rather than Postgres", "Delta chose kdb for market data snapshots because of the time series query performance.", ["project/delta", "tool/kdb"]),
    ("dead-end", "Connecting to kdb from Python with qpython hangs on large results", "qpython blocks indefinitely on result sets over a million rows; use pykx instead.", ["tool/kdb", "tool/python"]),
    ("how-to", "Query kdb from Python with pykx", "Install pykx, set the licence environment variable, and open a synchronous connection to the gateway.", ["tool/kdb", "tool/python"]),
    ("fact", "Rates desk books are locked at 18:30 London", "The rates desk locks its books at half past six in the evening London time for end of day.", ["desk/rates"]),
    ("caveat", "Trades booked after 18:30 appear in the next day's risk", "Anything booked after the rates desk lock shows up in the following day's risk batch, not today's.", ["desk/rates", "system/risk-batch"]),
    ("decision", "Gamma pricing results are persisted as parquet not CSV", "Gamma writes pricing results as parquet for schema enforcement and compression.", ["project/gamma", "system/pricing-engine"]),
    ("dead-end", "Deploying to UAT on Friday afternoon collides with the Sunday refresh", "UAT deployments late on Friday are lost when the Sunday refresh rebuilds the environment.", ["environment/uat"]),
    ("how-to", "Request a UAT refresh outside the Sunday schedule", "Raise a ticket with the platform team before noon and the ad hoc refresh runs the same evening.", ["environment/uat"]),
    ("fact", "Prod kdb gateway runs version 4.1", "The production kdb gateway is on version four point one and UAT is on four point two.", ["system/kdb-gateway", "environment/prod", "environment/uat"]),
    ("caveat", "Risk batch logs rotate at midnight and lose the current run", "Log rotation at midnight truncates the current risk batch log, so copy logs before midnight when debugging.", ["system/risk-batch"]),
    ("decision", "Delta uses the shared pricing engine instead of its own", "Delta adopted the shared pricing engine to avoid duplicating curve logic.", ["project/delta", "system/pricing-engine"]),
    ("dead-end", "Increasing the gateway timeout to 300s causes connection pool exhaustion", "Raising the gateway timeout starved the connection pool; chunking queries is the right fix.", ["system/kdb-gateway"]),
    ("how-to", "Get read access to the prod kdb gateway", "Request the gateway reader role through the access portal; approval takes one business day.", ["system/kdb-gateway", "environment/prod"]),
    ("fact", "Risk batch publishes PnL to the rates desk dashboard by 04:00", "The risk batch publishes PnL to the rates dashboard by four in the morning on a normal night.", ["system/risk-batch", "desk/rates"]),
    ("caveat", "Python 3.12 removed distutils which breaks the legacy curve loader", "The legacy curve loader imports distutils, which Python three point twelve removed; use the setuptools shim.", ["tool/python", "system/pricing-engine"]),
    ("dead-end", "Mocking the gateway in unit tests with a real q process is too flaky", "Starting a real q process in tests fails intermittently on CI; use the recorded response fixtures.", ["system/kdb-gateway", "tool/kdb"]),
    ("how-to", "Reproduce a gamma pricing run locally", "Pull the parquet inputs for the date, set the gamma config environment variable, and run the pricing CLI.", ["project/gamma", "system/pricing-engine"]),
]

QUERIES: list[tuple[str, list[int]]] = [
    ("gateway timeout symbols", [0, 1]),
    ("how long is the prod gateway timeout", [2]),
    ("empty table on timeout", [3]),
    ("when does the gamma nightly batch run", [4]),
    ("rerun risk batch", [5, 6]),
    ("UAT refresh Sunday", [7, 19, 20]),
    ("which Python version does gamma use", [8]),
    ("loading curves slowly pandas", [9, 10]),
    ("duplicate tenors curve rejected", [11]),
    ("pricing engine uses yesterday's curve", [12]),
    ("qpython hangs", [14, 15]),
    ("rates desk book lock time", [16, 17]),
    ("parquet pricing results", [18]),
    ("kdb gateway version prod", [21]),
    ("risk batch logs rotate midnight", [22]),
    ("connection pool exhaustion timeout", [24]),
    ("read access prod gateway", [25]),
    ("PnL dashboard time", [26]),
    ("distutils removed python 3.12", [27]),
    ("flaky q process tests", [28]),
]

SECTIONS = {
    "dead-end": ["Approach", "Why it fails"],
    "caveat": ["Symptom", "Cause", "Workaround"],
    "how-to": ["Steps", "Verify"],
    "decision": ["Options considered", "Rationale", "Consequences"],
    "fact": ["Detail", "How to check"],
}


def finding_id(i: int) -> str:
    return f"01J9XK3M8Q7ZV2W1F4N6B5H{i:03d}"
```

- [ ] **Step 2: Write the failing test**

`tests/test_eval.py`:

```python
import pytest

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.eval_fixtures import FINDINGS, QUERIES, finding_id
from teamknowledge.eval_retrieval import run_eval
from teamknowledge.cli import build_parser


def test_fixture_shapes():
    assert len(FINDINGS) == 30 and len(QUERIES) == 20
    assert all(0 <= i < 30 for _, idx in QUERIES for i in idx)
    assert len(finding_id(0)) == 26


@pytest.mark.neo4j
def test_run_eval_reports_three_arms(graph, tmp_path):
    result = run_eval(graph, FakeEmbedder(), tmp_path)
    assert set(result) >= {"fulltext", "vector", "fused", "queries", "findings", "embedder"}
    assert result["queries"] == 20 and result["findings"] == 30 and result["embedder"] == "fake"
    for arm in ("fulltext", "vector", "fused"):
        assert 0.0 <= result[arm] <= 1.0
    assert result["fulltext"] >= 0.5  # keyword-rich queries must hit on full-text alone


def test_eval_subcommand_exists():
    args = build_parser().parse_args(["eval", "retrieval"])
    assert args.command == "eval"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'teamknowledge.eval_fixtures'`

- [ ] **Step 4: Write eval_retrieval.py**

```python
"""Measure recall at 3 for full-text only, vector only, and fused retrieval over the fixture corpus."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .embed.base import Embedder
from .eval_fixtures import ENTITIES, FINDINGS, QUERIES, SECTIONS, finding_id
from .graph.client import GraphClient
from .graph.queries import VECTOR_QUERY, Reader
from .graph.sync import Syncer
from .model import Evidence, Finding
from .repo import init_knowledge_repo

K = 3


def build_fixture_repo(workdir: Path):
    repo = init_knowledge_repo(workdir / "eval-repo", local_remote=workdir / "eval-remote.git", author="eval")
    paths = [repo.write_entity(e) for e in ENTITIES]
    for i, (kind, title, claim, scope) in enumerate(FINDINGS):
        sections = {h: f"{h} for: {claim}" for h in SECTIONS[kind]}
        f = Finding(id=finding_id(i), kind=kind, title=title, claim=claim, scope=scope,
                    evidence=[Evidence("observation", note="Fixture observation written for the retrieval evaluation.")],
                    confidence="observed", status="active", author="eval", created=date(2026, 10, 1), sections=sections)
        paths.append(repo.write_finding(f))
    repo.commit_files(paths, "eval corpus")
    repo.push("main")
    return repo


def recall_at_k(ranked_ids: list[str], expected: list[str], k: int = K) -> float:
    hits = len(set(ranked_ids[:k]) & set(expected))
    return hits / len(expected)


def run_eval(client: GraphClient, embedder: Embedder | None, workdir: Path) -> dict:
    repo = build_fixture_repo(Path(workdir))
    Syncer(repo, client, embedder=embedder).sync(full=True)
    fulltext_reader = Reader(client, embedder=None)
    fused_reader = Reader(client, embedder=embedder)
    scores = {"fulltext": 0.0, "vector": 0.0, "fused": 0.0}
    for query, indices in QUERIES:
        expected = [finding_id(i) for i in indices]
        scores["fulltext"] += recall_at_k([f.id for f in fulltext_reader.search(query, limit=K).findings], expected)
        if embedder is not None:
            vec = embedder.embed([query])[0]
            rows = client.run(VECTOR_QUERY, k=K, vec=vec, kinds=[])
            scores["vector"] += recall_at_k([r["id"] for r in rows], expected)
            scores["fused"] += recall_at_k([f.id for f in fused_reader.search(query, limit=K).findings], expected)
    n = len(QUERIES)
    return {"fulltext": round(scores["fulltext"] / n, 3), "vector": round(scores["vector"] / n, 3),
            "fused": round(scores["fused"] / n, 3), "queries": n, "findings": len(FINDINGS),
            "embedder": embedder.model if embedder else "none"}
```

- [ ] **Step 5: Add `tk eval retrieval` to cli.py**

Add above `build_parser`:

```python
def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    import tempfile

    from .eval_retrieval import run_eval
    from .graph.client import GraphClient
    from .settings import Settings, make_embedder_from

    try:
        from testcontainers.neo4j import Neo4jContainer
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
```

In `build_parser` before `return parser`:

```python
    ev = sub.add_parser("eval", help="evaluations").add_subparsers(dest="eval_command", required=True)
    p = ev.add_parser("retrieval", help="recall@3 for full-text, vector, and fused search on the fixture corpus")
    p.set_defaults(func=cmd_eval_retrieval)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval.py -v`
Expected: all pass with Docker. If `test_run_eval_reports_three_arms` fails on the `>= 0.5` bound, print the per-query misses by temporarily returning them from `run_eval`; a low score usually means the Lucene query dropped terms or the full-text index had not finished populating (`init_schema` awaits indexes, but check `CALL db.awaitIndexes()` ran after the full sync's index recreation).

- [ ] **Step 7: Commit**

```bash
git add src/teamknowledge/eval_fixtures.py src/teamknowledge/eval_retrieval.py src/teamknowledge/cli.py tests/test_eval.py
git commit -m "feat: retrieval evaluation with recall@3 across full-text, vector, and fused arms"
```

---

### Task 15: Claude Code skill, README, end-to-end test

**Files:**
- Create: `skills/team-knowledge/SKILL.md`
- Create: `README.md`
- Test: `tests/test_e2e.py`
- Test: `tests/test_template_ci.py`

**Interfaces:**
- Consumes: everything. Produces the user-facing documents and the final proof that the loop works end to end through the CLI.

- [ ] **Step 1: Write the skill**

`skills/team-knowledge/SKILL.md`:

```markdown
---
name: team-knowledge
description: Record a hard-won team finding as a reviewed merge request, or look up what the team already knows about a project, system, environment, desk, or tool. User-invoked; never runs on its own. Use when the user says "record this", "note that X doesn't work", "/team-knowledge", "what do we know about", "anything known about", or asks whether an approach has been tried before.
---

# Team knowledge

The `team-knowledge` MCP server holds the team's structured findings. Findings are short, schema-validated
files scoped by entities. New findings go through a merge request that a colleague approves, so a draft
should read the way a careful colleague would want to read it. Nothing in this skill runs unless the user asks.

## Ask mode

Trigger: `/team-knowledge ask <question or scope>`, or a plain question like "what do we know about the UAT gateway".

1. Resolve scope. Turn the systems, projects, environments, desks, and tools the user names into entity refs
   with `list_entities` (fuzzy, matches aliases). If the current repo has a `.team-knowledge.yaml` with a
   `scope:` list, treat those refs as the default scope when the user gives none.
2. For a scoped question call `findings_for_scope` with those refs. For an open question call `search_findings`
   with the user's words, passing any resolved refs as `scope` so they boost. Use both when unsure.
3. Present a short list: kind, title, claim, author and date, wiki link. Lead with dead-ends and caveats when
   the user is about to attempt something. Say plainly when nothing was found. Never invent a finding.
4. Offer `get_finding` for detail only if the user wants it.

Encourage, but never force, an Ask at the start of work on a scoped system.

## Record mode

Trigger: `/team-knowledge record`, or "record that", "note this for the team", "that should go in the knowledge base".

1. Gather what was learned from the session: the approach, what happened, the evidence (errors seen, merge
   requests, Confluence pages, observations with dates), and where it applies.
2. Choose the kind. `dead-end` for an approach that does not work here. `caveat` for behaviour that surprises
   and causes mistakes. `how-to` for a recipe that works. `decision` for a choice and its rationale. `fact` for a
   stable property of a system or environment.
3. Resolve scope with `list_entities`. If an entity is genuinely new, include it in `new_entities` with type,
   slug, name, and a one-paragraph description. Ask the user only when a ref is genuinely ambiguous.
4. Draft the header and sections. Title: one specific declarative sentence, 10 to 120 characters. Claim: the
   finding in one to three sentences, specific enough to be wrong. Evidence: at least one item; observations
   carry a dated note. Confidence: `observed` if the user saw it, `inferred` if deduced, `reported` if told.
   Sections: only the kind's headings, in order, under 400 words total.
5. Show a compact preview: kind, title, scope, claim. Ask for one confirmation. Then call `propose_finding`.
6. Report the merge request URL and the reviewers. If the tool returns `errors`, fix what the suggestions make
   obvious and ask the user only for what you cannot resolve. If it returns `error` with a `retry` command,
   tell the user the finding is committed locally and give them the command.

Superseding an existing finding is `propose_finding` with `supersedes: [old id]`. Correcting one is
`amend_finding`. Withdrawing one is `retract_finding` with a reason.

## Quality bar

- Scope precisely. A finding about the UAT gateway is scoped to `system/kdb-gateway` and `environment/uat`, not to the project that happened to hit it.
- Claims are falsifiable. "The gateway is slow" is not a finding; "selects over 10k symbols exceed the 30s UAT gateway timeout" is.
- Do not pad. Required sections only, no preamble, no sign-off.
- Never record secrets, credentials, or client data.

## Tool reference

| Tool | Use |
|---|---|
| `list_entities(type?, query?)` | resolve scope refs; see owners and active counts |
| `findings_for_scope(scope, kinds?, include_related?)` | deterministic lookup by entity |
| `search_findings(query, scope?, kinds?)` | hybrid search; `warnings` says if semantic search was unavailable |
| `get_finding(finding_id)` | full detail |
| `propose_finding(...)` | open a merge request for a new finding |
| `amend_finding(finding_id, reason, header_changes?, sections?)` | correct an accepted finding |
| `retract_finding(finding_id, reason)` | withdraw a finding |
| `my_proposals()` | the user's open merge requests |
```

- [ ] **Step 2: Write the tool repo README**

`README.md`:

```markdown
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

## Develop

    uv run pytest                  # Neo4j tests need Docker; they skip without it
    uv run pytest -m "not neo4j"   # fast subset
    uv run ruff check src tests
```

- [ ] **Step 3: Write the template CI test**

`tests/test_template_ci.py`:

```python
import yaml

from teamknowledge.repo import template_dir


def test_template_ci_shape():
    ci = yaml.safe_load((template_dir() / "gitlab-ci.yml").read_text())
    assert ci["stages"] == ["validate", "publish"]
    assert ci["variables"]["GIT_DEPTH"] == 0
    assert ci["validate"]["script"] == ["tk validate --all"]
    assert ci["pages"]["script"] == ["tk sync", "tk render --out public"]
    assert ci["pages"]["resource_group"] == "sync"
    assert ci["pages"]["artifacts"]["paths"] == ["public"]
```

- [ ] **Step 4: Write the end-to-end test**

`tests/test_e2e.py`:

```python
"""The whole loop through the CLI: init, propose, approve, sync, query, render."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.queries import Reader
from teamknowledge.model import Entity
from teamknowledge.propose import ProposalInput, Proposer
from teamknowledge.repo import KnowledgeRepo
from teamknowledge.validate import Validator

pytestmark = pytest.mark.neo4j


def tk(*args, env):
    result = subprocess.run([sys.executable, "-m", "teamknowledge.cli", *args], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_whole_loop(tmp_path, graph):
    env = {**os.environ, "TK_GITHOST": "local", "TK_AUTHOR": "alice", "TK_EMBEDDER": "fake",
           "NEO4J_URI": graph.uri, "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": graph.password, "NEO4J_DATABASE": "neo4j"}
    clone, remote = tmp_path / "kr", tmp_path / "kr.git"
    tk("init", str(clone), "--local-remote", str(remote), env=env)
    repo = KnowledgeRepo(clone)

    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.")
    repo.write_entity(e)
    repo.commit_files([e.path], "gateway")
    repo.push("main")
    tk("validate", "--repo", str(clone), env=env)

    host = LocalGitHost(remote, author="alice")
    proposer = Proposer(repo, Validator(repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    r = proposer.propose(ProposalInput(
        kind="dead-end", title="Batch queries over 10k symbols time out",
        claim="Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        scope=["system/kdb-gateway"], evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
        confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."}))
    assert r.merge_request_url and not r.errors

    assert "finding/" in tk("review", "list", "--repo", str(clone), env=env)
    tk("review", "approve", r.branch, "--as", "bob", "--repo", str(clone), env=env)
    repo.sync_main()

    out = tk("sync", "--repo", str(clone), env=env)
    assert "findings upserted: 2" in out and "review metadata set: 1" in out

    reader = Reader(graph, embedder=FakeEmbedder())
    found = reader.findings_for_scope(["system/kdb-gateway"])
    assert [f.id for f in found] == [r.finding_id]
    assert reader.search("gateway timeout").findings[0].id == r.finding_id
    detail = reader.get_finding(r.finding_id)
    assert detail["approved_by"] == ["bob"] and detail["mr_url"] == "local://merge-requests/1"

    tk("render", "--repo", str(clone), "--out", str(tmp_path / "site"), env=env)
    page = (tmp_path / "site" / "findings" / f"{r.finding_id}.html").read_text()
    assert "Batch queries over 10k symbols time out" in page

    assert "unchanged" in tk("sync", "--repo", str(clone), env=env)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: everything passes with Docker running; `live` tests skip. Then run `uv run ruff check src tests` and fix anything it reports (unused imports are the usual culprit; the `test_render.py` import of `Path` and `date` may be unused).

- [ ] **Step 6: Commit**

```bash
git add skills/team-knowledge/SKILL.md README.md tests/test_e2e.py tests/test_template_ci.py
git commit -m "feat: Claude Code skill, README, end-to-end loop test"
```

---

## Plan self-review

**Spec coverage.**

| Spec section | Task |
|---|---|
| 5.1 finding file, 5.4 identifiers | 2 |
| 5.2 kinds, 5.3 entity file, 5.5 transitions, 5.6 schemas | 3 |
| 6 repo layout and config | 4 |
| 7 review flow (branches, reviewers, approval, push options) | 5, 6, 7 |
| 8 git host adapter | 5, 7 |
| 9 MCP server, env vars, `.mcp.json`, tool shapes | 9 (settings), 13 |
| 10 skill | 15 |
| 11 graph model, schema statements, read queries | 9, 10, 11 |
| 12 sync | 10 |
| 13 render | 12 |
| 14 CI | 4 (template), 15 (shape test) |
| 15 embeddings | 8, 10, 11 |
| 16 failure handling | 6 (push failure), 10 (embedder down, validation abort, host down), 13 (graph unreachable) |
| 17 testing, retrieval evaluation | every task, 14, 15 |
| 18 tool repo layout and commands | 1, 4, 5, 6, 9, 10, 12, 13, 14 |
| 19 rebuild at work | README in 15 plus the spec itself |

**Deviations from the spec, all deliberate.** The knowledge-repo template ships inside the package as `knowledge_template/` so `tk init` works after `pip install`; the spec was updated. The `--all` flag on `tk validate` is accepted and is the default behaviour. Review metadata uses `git log --first-parent -1 -- <path>` rather than `--merges`, because history simplification hides merge commits for a path that equals the branch parent; `--first-parent` finds the merge commit on main, or the direct commit when the project uses fast-forward or squash merges. The amended finding keeps its filename even if the title changes; renaming is cosmetic and would cost a `git mv`. On embedding failure every finding in the failed batch is stored with a null embedding, matching the spec's "stored without an embedding and flagged" rather than keeping a stale vector.

**Type consistency check.** `ValidationError.to_dict()` is what `ProposalResult.errors` carries and what the MCP tools return. `MergeRequest` fields are identical in `local.py` and `gitlab.py`. `Reader.search` returns `SearchResult`, and `mcp_server` calls `.to_dict()` on it. `GraphClient.uri` and `.password` are read by tests and by `test_e2e`. `Syncer(repo, client, embedder, host, sleep)` is constructed identically in Tasks 10, 11, 13, 14, 15. `finding_id(i)` in the eval fixtures yields 26 valid Crockford characters.

**Placeholder scan.** No TBD, TODO, or "similar to Task N". Every code step has code.
