#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python - "$REPO_ROOT" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
skills = [
    root / "skills/general/long-running-autonomous-project-management",
    root / "skills/general/tmux-codex-parallel-workers",
]

for skill in skills:
    path = skill / "SKILL.md"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"missing frontmatter: {path}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise SystemExit(f"unclosed frontmatter: {path}")
    frontmatter = text[4:end]
    for required in ("name:", "description:"):
        if required not in frontmatter:
            raise SystemExit(f"missing {required} in {path}")
    print(f"skill ok: {skill.name}")
PY

python - "$REPO_ROOT" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for path in sorted((root / "skills").rglob("*.py")):
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    print(f"python ok: {path.relative_to(root)}")
PY

if find "$REPO_ROOT" -type d -name __pycache__ | grep -q .; then
  echo "warning: __pycache__ directories found" >&2
  find "$REPO_ROOT" -type d -name __pycache__ >&2
  exit 1
fi

echo "validation ok"
