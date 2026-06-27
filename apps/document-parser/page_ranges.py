"""Page range parsing shared by local and upstream document providers."""

from __future__ import annotations


def parse_page_ranges(value: str | None, page_count: int | None = None) -> list[int]:
    """Parse SIQ page range syntax into one-based page numbers.

    Supports comma-separated pages and inclusive ranges, for example:
    ``1-3,7,10-12``. Empty input means every page, represented as ``[]`` so
    callers can choose the cheapest all-pages path.
    """

    raw = str(value or "").strip()
    if not raw:
        return []

    pages: list[int] = []
    seen: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = [item.strip() for item in token.split("-", 1)]
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"Invalid page range: {token}")
            start = int(start_text)
            end = int(end_text)
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"Invalid page range: {token}")
            candidates = range(start, end + 1)
        else:
            if not token.isdigit():
                raise ValueError(f"Invalid page number: {token}")
            page = int(token)
            if page <= 0:
                raise ValueError(f"Invalid page number: {token}")
            candidates = [page]

        for page in candidates:
            if page_count is not None and page > page_count:
                continue
            if page not in seen:
                seen.add(page)
                pages.append(page)

    if raw and not pages:
        raise ValueError("Page range does not overlap the document")
    return pages


def selected_page_indexes(value: str | None, page_count: int) -> list[int]:
    pages = parse_page_ranges(value, page_count=page_count)
    if not pages:
        return list(range(page_count))
    return [page - 1 for page in pages]
