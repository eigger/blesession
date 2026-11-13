"""Refuse publishing a mismatched tag or a commit outside main."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main(tag: str) -> None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise SystemExit("Release tag must be vMAJOR.MINOR.PATCH")
    source = ast.parse(Path("src/blesession/__init__.py").read_text(encoding="utf-8"))
    versions = [
        ast.literal_eval(node.value)
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if versions != [tag[1:]]:
        raise SystemExit("Release tag does not match the package version")
    commit = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if commit != git("rev-parse", "HEAD"):
        raise SystemExit("Checked-out commit does not match the release tag")
    if subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"]).returncode:
        raise SystemExit("Release commit is not on main")
    print(f"Validated {tag} at {commit}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: validate_release.py TAG")
    main(sys.argv[1])
