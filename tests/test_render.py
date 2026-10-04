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


def test_evidence_url_is_sanitized(knowledge_repo, tmp_path):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway",
               description="Fronts the tick databases for the pricing desk.")
    f = Finding(id=FID, kind="fact", title="Prod gateway timeout is 120s",
                claim="The production gateway timeout is one hundred and twenty seconds.", scope=["system/kdb-gateway"],
                evidence=[Evidence("url", ref="javascript:alert(1)"),
                          Evidence("confluence", ref="https://confluence.bank/x/KDB-GW", note="limits section")],
                confidence="observed", status="active", author="alice", created=date(2026, 10, 4),
                sections={"Detail": "x", "How to check": "y"})
    knowledge_repo.write_entity(e)
    knowledge_repo.write_finding(f)
    render_site(knowledge_repo, tmp_path / "public")
    html = (tmp_path / "public" / "findings" / f"{FID}.html").read_text()
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert "#" in hrefs
    assert not any("javascript:" in href for href in hrefs)
    assert "https://confluence.bank/x/KDB-GW" in hrefs  # a normal https ref is unchanged


def test_cli_render(knowledge_repo, tmp_path, capsys):
    assert main(["render", "--repo", str(knowledge_repo.root), "--out", str(tmp_path / "site")]) == 0
    assert (tmp_path / "site" / "index.html").exists()
    assert "pages" in capsys.readouterr().out
