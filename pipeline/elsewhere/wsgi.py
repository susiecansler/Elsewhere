"""ASGI entrypoint for deployment.

Render's start command needs an importable module-level `app`; the CLI's
`serve` builds one at call time. Same application, different front door.
"""

from __future__ import annotations

import os

from elsewhere.web import create_app

app = create_app(
    source=os.environ.get("ELSEWHERE_SOURCE", "austin"),
    target=os.environ.get("ELSEWHERE_TARGET", "chicago"),
)
