import yaml

from teamknowledge.repo import template_dir


def test_template_ci_shape():
    ci = yaml.safe_load((template_dir() / "gitlab-ci.yml").read_text())
    assert ci["stages"] == ["validate", "publish"]
    assert ci["variables"]["GIT_DEPTH"] == 0
    assert ci["validate"]["script"] == ["tk validate --all"]
    assert ci["pages"]["script"] == ["tk sync", "tk render --out public"]
    assert ci["pages"]["resource_group"] == "sync"
    assert ci["pages"]["artifacts"]["paths"] == ["public"]
