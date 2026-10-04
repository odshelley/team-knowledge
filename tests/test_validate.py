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
    base = {"type": type, "slug": slug, "name": "kdb+ gateway", "description": "The q process that fronts the tick databases.",
            "path": Path(f"entities/{type}/{slug}.md"), "dir_type": type}
    base.update(kw)
    return Entity(**base)


def finding(**kw) -> Finding:
    base = {
        "id": FID, "kind": "dead-end", "title": "Batch queries over 10k symbols time out",
        "claim": "Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        "scope": ["system/kdb-gateway"], "evidence": [Evidence("observation", note="Reproduced on 2026-10-04 with 12k symbols.")],
        "confidence": "observed", "status": "active", "author": "osian.shelley", "created": date(2026, 10, 4),
        "sections": {"Approach": "One select.", "Why it fails": "Timeout."},
        "path": Path(f"findings/{FID}-batch.md"),
    }
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
