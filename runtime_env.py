"""Safe runtime environment loading for local/Bothost deployments."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_runtime_env(app_dir: Path) -> bool:
    """Load ``.env`` if present without overriding real process environment."""
    dotenv_path = Path(app_dir) / ".env"
    if not dotenv_path.is_file():
        return False
    return bool(load_dotenv(dotenv_path=dotenv_path, override=False))


__all__ = ["load_runtime_env"]
