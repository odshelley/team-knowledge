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


def safe_url(value: str | None) -> str:
    """Only http(s) links and site-relative paths are safe to render as href; everything else becomes '#'."""
    v = value or ""
    if v.startswith(("http://", "https://")):
        return v
    if v.startswith(("/", "./", "../")) and ":" not in v.split("/", 1)[0]:
        return v
    return "#"


def _env() -> Environment:
    env = Environment(loader=PackageLoader("teamknowledge.render", "templates"), autoescape=select_autoescape(["html"]))
    env.filters["md"] = _md
    env.filters["safe_url"] = safe_url
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
