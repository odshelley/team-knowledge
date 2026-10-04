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
        # For errors with no path (e.g., allOf conditional requirements),
        # try to extract the field name from the message
        msg = error.message
        if "'required property" in msg or "is a required property" in msg:
            match = msg.split("'")[1] if "'" in msg else None
            if match:
                return match
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
