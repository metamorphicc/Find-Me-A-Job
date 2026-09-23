import json

import pytest

from job_search_automation.profile import (
    ProfileError,
    load_profile,
    resume_file,
    save_profile_field,
)


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
