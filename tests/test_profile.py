import json

import pytest

from job_search_automation.profile import (
    ProfileError,
    initialize_profile,
    load_profile,
    missing_reply_fields,
    resume_file,
    save_custom_fact,
    save_profile_field,
)


def test_initializing_profile_preserves_facts_and_replaces_placeholders(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"name": "REPLACE_WITH_YOUR_NAME", "email": "real@example.test"}),
        encoding="utf-8",
    )
    initialize_profile(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["name"] == ""
    assert raw["email"] == "real@example.test"
    assert raw["skills"] == []
    assert missing_reply_fields(raw) == ("name", "about", "contact")


def test_optional_application_fields_and_local_resume(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"name": "Тест", "about": "Учусь", "contact": "связь"}), encoding="utf-8"
    )
    save_profile_field(path, "email", "test@example.test")
    save_profile_field(path, "phone", "+7 900 000 00 00")
    save_profile_field(path, "city", "Новосибирск")
    (tmp_path / "resumes").mkdir()
    (tmp_path / "resumes" / "cv.pdf").write_bytes(b"%PDF-1.4")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["resume_path"] = "resumes/cv.pdf"
    path.write_text(json.dumps(raw), encoding="utf-8")

    profile = load_profile(path)
    assert (profile.email, profile.phone, profile.city) == (
        "test@example.test",
        "+7 900 000 00 00",
        "Новосибирск",
    )
    assert resume_file(profile, path) == tmp_path / "resumes" / "cv.pdf"


def test_missing_resume_does_not_block_profile_or_search(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"name": "Тест", "about": "Учусь", "contact": "связь"}), encoding="utf-8"
    )
    profile = load_profile(path)

    assert profile.resume_path == ""
    with pytest.raises(ProfileError, match="resume_path"):
        resume_file(profile, path)


def test_custom_profile_facts_can_be_saved_and_removed(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"name": "Тест", "about": "Учусь", "contact": "связь"}), encoding="utf-8"
    )
    save_custom_fact(path, "GitHub", "https://github.com/example")
    assert ("GitHub", "https://github.com/example") in load_profile(path).facts
    save_custom_fact(path, "GitHub", "-")
    assert load_profile(path).facts == ()
