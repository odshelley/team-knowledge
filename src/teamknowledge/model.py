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


def _str_list_raw(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


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
    text = _dump_header(f.header())
    if f.preamble:
        text += "\n" + f.preamble + "\n"
    else:
        text += "\n"
    text += f.body()
    return text


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
