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
