import subprocess
import sys
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "privacy_scan.py"


def test_privacy_scan_rejects_tracked_profile_and_token(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "profile.json").write_text("{}", encoding="utf-8")
    (tmp_path / "source.py").write_text(
        "token = '" + "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'", encoding="utf-8"
    )
    subprocess.run(["git", "add", "--", "profile.json", "source.py"], cwd=tmp_path, check=True)

    result = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 1
    assert "Private path is tracked" in result.stderr
    assert "Telegram bot token" in result.stderr
