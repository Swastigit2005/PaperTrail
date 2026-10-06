"""PaperTrail web entry point.

- On Vercel this module's ``app`` becomes the Python function; ``public/`` is served by the CDN.
- Locally / in Docker: ``uvicorn app:app --port 7860`` serves the API and the frontend together.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))  # works whether or not the package is pip-installed

from papertrail.web.api import app, mount_static  # noqa: E402

if not os.getenv("VERCEL"):
    mount_static(ROOT / "public")

__all__ = ["app"]
