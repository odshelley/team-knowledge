"""The knowledge repo on disk: file access, git operations, diffs, scaffolding."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .model import (
    Entity,
    Finding,
    ParseError,
    entity_path,
    finding_filename,
    parse_entity,
    parse_finding,
    serialize_entity,
    serialize_finding,
)
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
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=env, check=False)
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def remote_url(self) -> str:
        return self.git("remote", "get-url", "origin").strip()

    def sync_main(self, branch: str = "main") -> None:
        self.git("fetch", "-q", "origin", check=False)
        if self.git("rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False).strip():
            self.git("checkout", "-q", "-B", branch, f"origin/{branch}")
        else:
            self.git("checkout", "-q", branch)

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
    shutil.copytree(template_dir(), dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".DS_Store"))
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
