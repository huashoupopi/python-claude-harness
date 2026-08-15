from chunker import chunk
from dedupe import dedupe
from flatten import flatten
from normalize_case import normalize_case
from percent import percent
from strip_prefix import strip_prefix


def test_normalize_case_folds_to_lowercase():
    assert normalize_case("  Hello World  ") == "hello world"


def test_strip_prefix_only_strips_from_the_front():
    assert strip_prefix("api_key", "api_") == "key"
    assert strip_prefix("legacy_api_key", "api_") == "legacy_api_key"


def test_chunk_covers_every_item():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunk([1, 2, 3], 3) == [[1, 2, 3]]


def test_dedupe_keeps_first_seen_order():
    assert dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_flatten_removes_one_level():
    assert flatten([[1, 2], [3], []]) == [1, 2, 3]


def test_percent_formats_one_decimal():
    assert percent(1, 3) == "33.3%"
    assert percent(1, 2) == "50.0%"


def test_percent_handles_a_zero_denominator():
    assert percent(0, 0) == "0.0%"


def test_normalize_case_leaves_an_empty_value_alone():
    assert normalize_case("") == ""


def test_chunk_of_an_empty_sequence_is_empty():
    assert chunk([], 3) == []


def test_dedupe_of_an_empty_list_is_empty():
    assert dedupe([]) == []
