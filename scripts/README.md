# Repository Utility Scripts

These scripts operate on this repository, not on a running Codex worker session.

## `install.sh`

Copies the packaged skills into:

```text
${CODEX_HOME:-$HOME/.codex}/skills/general/
```

Existing installed copies are moved to timestamped backups under:

```text
${CODEX_HOME:-$HOME/.codex}/skills/.backup/
```

Run:

```bash
./scripts/install.sh
```

## `validate.sh`

Checks that both skills have valid `SKILL.md` frontmatter and compiles bundled Python scripts without writing `__pycache__` directories.

Run:

```bash
./scripts/validate.sh
```

