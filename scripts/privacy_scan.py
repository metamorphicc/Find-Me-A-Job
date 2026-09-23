"""Check that private job-search data and common credentials are absent from Git."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PRIVATE_NAMES = {
    ".env",
    "config.toml",
    "profile.json",
    "applications_ledger.md",
}
PRIVATE_DIRS = {"data", "reports", "resumes", "artifacts", "screenshots", "browser-profiles"}
PRIVATE_SUFFIXES = {".pdf", ".doc", ".docx", ".db", ".sqlite"}
SECRET_PATTERNS = {
    "Telegram bot token": re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{35,}\b"),
    "GitHub token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def scan(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    problems = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        parts = [part.casefold() for part in relative.parts]
        if (
            parts[-1] in PRIVATE_NAMES
            or relative.suffix.casefold() in PRIVATE_SUFFIXES
            or any(part in PRIVATE_DIRS for part in parts[:-1])
        ):
            problems.append(f"Private path is tracked: {relative}")
            continue
        blob = subprocess.run(
            ["git", "show", f":{relative.as_posix()}"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        if len(blob) > 2_000_000:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(blob):
                problems.append(f"Possible {label} in {relative}")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    try:
        problems = scan(root)
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        print(f"Privacy scan could not run: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("Privacy scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
