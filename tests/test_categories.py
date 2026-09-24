from job_search_automation.config import load_config, load_search_settings, search_settings_path


def test_new_and_saved_old_config_default_to_it_categories(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nqueries = ["Python"]\n', encoding="utf-8")
    config = load_config(path)
    assert config.search.categories == ("software", "it_ops")

    saved = search_settings_path(config.database_path)
    saved.parent.mkdir(parents=True)
    saved.write_text(
        '{"queries":["Python"],"excluded_keywords":[],"area_ids":[],"experience_ids":[],'
        '"remote_only":true,"strict_remote":true,"days":7,"per_query":20,'
        '"sources":["hh"],"kinds":["job"]}',
        encoding="utf-8",
    )
    assert load_search_settings(config.search, saved).categories == ("software", "it_ops")


def test_category_mode_allows_empty_text_queries(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nqueries = []\ncategories = ["software"]\n', encoding="utf-8")
    assert load_config(path).search.queries == ()
