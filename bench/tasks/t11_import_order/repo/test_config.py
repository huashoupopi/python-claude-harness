import loader
import pagination
import settings

ITEMS = list(range(100))


def test_defaults_are_used_when_nothing_is_set():
    assert settings.get("page_size") == 20
    assert settings.get("retry_limit") == 3


def test_pagination_uses_the_default_page_size():
    assert len(pagination.page(ITEMS, 0)) == 20
    assert pagination.page_count(ITEMS) == 5


def test_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("APP_RETRY_LIMIT", "7")
    assert settings.get("retry_limit") == 7


def test_pagination_follows_an_env_override(monkeypatch):
    monkeypatch.setenv("APP_PAGE_SIZE", "5")
    assert len(pagination.page(ITEMS, 0)) == 5
    assert pagination.page_count(ITEMS) == 20


def test_value_goes_back_to_the_default_once_the_env_is_gone(monkeypatch):
    monkeypatch.setenv("APP_TIMEOUT_S", "1")
    assert settings.get("timeout_s") == 1
    monkeypatch.delenv("APP_TIMEOUT_S")
    assert settings.get("timeout_s") == 30


def test_repeated_reads_do_not_rebuild_the_config():
    settings.get("page_size")
    before = loader.load_count
    for _ in range(10):
        settings.get("page_size")
        settings.get("retry_limit")
    assert loader.load_count == before, "同一份环境下重复读配置不应该反复重建"
