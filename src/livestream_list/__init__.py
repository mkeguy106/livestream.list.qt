"""Livestream List - A Linux application for monitoring livestreams."""

from importlib.metadata import version as _pkg_version
from pathlib import Path


def _dev_version() -> str | None:
    """Return live git-describe version if running from a source checkout.

    This avoids showing stale hatch-vcs metadata (baked at `pip install -e .`
    time) when the repo has since been tagged or had new commits. Returns
    None for non-source installs (wheels, Flatpak) where .git is absent.
    """
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        return None
    import subprocess

    try:
        desc = subprocess.check_output(
            ["git", "-C", str(repo_root), "describe", "--tags", "--dirty", "--always"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if not desc:
        return None
    return f"{desc} ({branch})" if branch and branch not in ("main", "HEAD") else desc


__version__: str = _dev_version() or _pkg_version("livestream-list-qt")
