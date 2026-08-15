import pipeline
from registry import REGISTRY


def test_the_shipped_checks_are_registered():
    assert "not_empty" in REGISTRY
    assert "is_ascii" in REGISTRY


def test_a_clean_value_fails_nothing():
    assert pipeline.run("hello") == []


def test_codes_stay_unique():
    codes = [meta["code"] for meta in REGISTRY.values()]
    assert len(codes) == len(set(codes))


def test_min_length_is_registered_as_e103():
    assert "min_length" in REGISTRY, "min_length 没有出现在注册表里"
    assert REGISTRY["min_length"]["code"] == "E103"


def test_min_length_rejects_values_under_three_characters():
    assert "E103" in pipeline.run("ab")
    assert "E103" not in pipeline.run("abc")


def test_no_whitespace_is_registered_as_e104():
    assert "no_whitespace" in REGISTRY, "no_whitespace 没有出现在注册表里"
    assert REGISTRY["no_whitespace"]["code"] == "E104"


def test_no_whitespace_rejects_values_containing_spaces():
    assert "E104" in pipeline.run("hello world")
    assert "E104" not in pipeline.run("helloworld")


def test_describe_lists_every_check():
    assert pipeline.describe() == {
        "is_ascii": "E102",
        "min_length": "E103",
        "no_whitespace": "E104",
        "not_empty": "E101",
    }
