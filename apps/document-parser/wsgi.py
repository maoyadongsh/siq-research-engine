"""Production WSGI entrypoint for the document parser."""

import atexit

from app import app, stop_worker

atexit.register(stop_worker)

__all__ = ["app"]
