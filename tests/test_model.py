import re
from datetime import date
from pathlib import Path

import pytest

from teamknowledge.model import (
    Evidence,
    Finding,
    ParseError,
    finding_filename,
    new_ulid,
    parse_entity,
    parse_finding,
    parse_sections,
    serialize_entity,
    serialize_finding,
    slugify,
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


def test_preamble_preserved_on_round_trip():
    text_with_preamble = """---
id: 01J9XK3M8Q7ZV2W1F4N6B5HT9D
kind: fact
title: Example
claim: A claim.
scope: []
evidence: []
confidence: uncertain
status: active
author: test
created: 2026-01-01
supersedes: []
contradicts: []
---

This is some preamble text before the first section.

## Section A

Content of section A.

## Section B

Content of section B.
"""
    f = parse_finding(text_with_preamble)
    assert f.preamble == "This is some preamble text before the first section."

    text1 = serialize_finding(f)
    g = parse_finding(text1)
    assert g.preamble == f.preamble
    assert g.sections == f.sections

    text2 = serialize_finding(g)
    assert text2 == text1


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
