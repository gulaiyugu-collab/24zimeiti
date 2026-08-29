"""PythonAnywhere WSGI entry for the Project024 FastAPI app.

Set PROJECT024_ACCESS_PASSWORD in the PythonAnywhere WSGI configuration before
loading this module. The temporary Basic Auth gate remains enabled.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent / "工作文件" / "app"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a2wsgi import ASGIMiddleware  # noqa: E402
from app.main import app  # noqa: E402

application = ASGIMiddleware(app)
