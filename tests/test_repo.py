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
