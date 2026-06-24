"""Load local development env files without adding a dotenv dependency."""

from __future__ import annotations

import os
from pathlib import Path


_ENV_FILES = (".env", ".jira.env")
_ALIASES = {
    "JIRA_EMAIL": "JIRA_API_USER",
    "JIRA_SITE": "JIRA_CLOUD_URL",
}


def load_local_env(root: str | Path | None = None, *, override: bool = False) -> None:
    """Load .env and .jira.env from the project root when present.

    Existing process env wins by default so hosted deployments can provide real
    secrets through their native environment mechanism.
    """

    project_root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    for filename in _ENV_FILES:
        _load_env_file(project_root / filename, override=override)
    _apply_aliases(override=override)


def _load_env_file(path: Path, *, override: bool) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _clean_value(value.strip())
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def _apply_aliases(*, override: bool) -> None:
    for source, target in _ALIASES.items():
        value = os.getenv(source)
        if value and (override or target not in os.environ):
            os.environ[target] = value


def _clean_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
