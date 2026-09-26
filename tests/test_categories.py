from job_search_automation.categories import REMOTIVE_CATEGORIES, hh_role_ids
from job_search_automation.config import load_config, load_search_settings, search_settings_path


def test_new_and_saved_old_config_default_to_development_stack(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nqueries = ["Python"]\n', encoding="utf-8")
    config = load_config(path)
    assert config.search.categories == ("software",)
    assert "next.js" in config.search.title_keywords
    assert "typescript" in config.search.stack_keywords
    assert "qa" in config.search.excluded_title_keywords
    assert hh_role_ids(config.search.categories) == ("96",)
    assert REMOTIVE_CATEGORIES.get("Quality Assurance") is None

    saved = search_settings_path(config.database_path)
    saved.parent.mkdir(parents=True)
    saved.write_text(
        '{"queries":["Python"],"excluded_keywords":[],"area_ids":[],"experience_ids":[],'
        '"remote_only":true,"strict_remote":true,"days":7,"per_query":20,'
        '"sources":["hh"],"kinds":["job"]}',
        encoding="utf-8",
    )
    loaded = load_search_settings(config.search, saved)
    assert loaded.categories == ("software",)
    assert "next.js" in loaded.title_keywords
    assert "typescript" in loaded.stack_keywords
    assert "qa" in loaded.excluded_title_keywords


def test_category_mode_allows_empty_text_queries(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nqueries = []\ncategories = ["software"]\n', encoding="utf-8")
    assert load_config(path).search.queries == ()
