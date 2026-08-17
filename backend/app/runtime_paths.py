"""Resolve packaged content without changing normal source or container paths."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Return the immutable application content root.

    ``APP_CONTENT_ROOT`` is set only by the Windows offline launcher. Keeping
    this separate from the writable runtime directory lets a packaged build
    read its bundled scenario and frontend assets after extraction.
    """

    configured_root = os.environ.get("APP_CONTENT_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).resolve()
    return Path(__file__).resolve().parents[2]
