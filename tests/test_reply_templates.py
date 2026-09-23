import json
from dataclasses import replace

import pytest
from test_telegram_bot import vacancy

from job_search_automation.profile import CandidateProfile
from job_search_automation.reply_templates import (
    TemplateError,
    load_templates,
    render_reply,
    save_template_field,
    select_template,
)


def test_default_templates_choose_role_and_fall_back(tmp_path) -> None:
    templates = load_templates(tmp_path / "reply-templates.json")

    assert select_template(vacancy(), templates).key == "python"
    assert (
        select_template(replace(vacancy(), title="Стажёр-аналитик"), templates).key == "internship"
    )
    assert select_template(replace(vacancy(), title="Менеджер проекта"), templates).key == "general"


def test_edited_template_is_saved_and_renders_only_supplied_facts(tmp_path) -> None:
    path = tmp_path / "reply-templates.json"
    save_template_field(
        path, "python", "body", "{name}: {title} в {company}\n{skills_line}\n{contact_line}"
    )
    template = select_template(vacancy(), load_templates(path))
    profile = CandidateProfile("Иван", "О себе", "@candidate", (), "", "")

    assert render_reply(profile, vacancy(), template) == (
        "Иван: Junior Python <Developer> в Example & Co\nСвязаться со мной: @candidate"
    )


def test_rejects_unknown_placeholders_without_changing_saved_template(tmp_path) -> None:
    path = tmp_path / "reply-templates.json"
    original = load_templates(path)

    with pytest.raises(TemplateError):
        save_template_field(path, "python", "body", "{name.__class__}")

    assert load_templates(path) == original
    assert not path.exists()


def test_freelance_template_is_selected_and_old_template_file_migrates(tmp_path) -> None:
    path = tmp_path / "reply-templates.json"
    path.write_text(
        json.dumps(
            {
                key: {"body": "{name}", "keywords": []}
                for key in ("internship", "python", "analytics", "general")
            }
        ),
        encoding="utf-8",
    )
    templates = load_templates(path)
    assert select_template(replace(vacancy(), kind="freelance"), templates).key == "freelance_ru"
    assert (
        select_template(replace(vacancy(), kind="freelance", market="global"), templates).key
        == "freelance_global"
    )


def test_template_form_field_values_are_saved(tmp_path) -> None:
    path = tmp_path / "reply-templates.json"
    save_template_field(path, "python", "form_value", "Salary = 100000")
    template = next(item for item in load_templates(path) if item.key == "python")
    assert template.form_values == (("Salary", "100000"),)
