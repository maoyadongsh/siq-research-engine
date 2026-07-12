"""Production WSGI entrypoint for the PDF parser."""

from pdf_parser_app_impl import app, initialize_app

initialize_app(start_worker=True)

__all__ = ["app"]
