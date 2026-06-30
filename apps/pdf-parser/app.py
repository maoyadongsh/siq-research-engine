#!/usr/bin/env python3
"""Compatibility entrypoint for the PDF parser app."""

from __future__ import annotations

import os
import sys

import pdf_parser_app_impl as _impl


if __name__ != "__main__":
    sys.modules[__name__] = _impl
else:
    if os.environ.get("FLASK_ENV", "").lower() == "production":
        raise RuntimeError(
            "Do not run PDF parser with Flask app.run in production. Use a WSGI server such as gunicorn."
        )
    _impl.initialize_app(start_worker=True)
    _impl.app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 15000)),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
