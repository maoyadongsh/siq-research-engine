"""Production WSGI entrypoint for the PDF parser."""

import atexit

from pdf_parser_app_impl import app, initialize_app, stop_queue_worker

initialize_app(start_worker=True)
atexit.register(stop_queue_worker)

__all__ = ["app"]
