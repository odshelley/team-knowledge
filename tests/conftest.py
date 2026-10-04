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
