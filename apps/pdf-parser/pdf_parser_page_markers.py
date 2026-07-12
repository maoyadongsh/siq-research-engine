"""PDF page marker injection helpers for parsed Markdown artifacts."""

from __future__ import annotations

import re

from pdf_source_viewer import coerce_json_artifact, page_content_payload_from_content_list

PDF_PAGE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:<!--\s*PDF_PAGE:\s*(\d+)\s*-->|\[PDF_PAGE:\s*(\d+)\])\s*\n?"
)


def _page_marker_line(page_number):
    return f"[PDF_PAGE: {int(page_number)}]"


def _strip_page_markers(markdown):
    return PDF_PAGE_MARKER_RE.sub("", str(markdown or ""))


def _normalized_anchor_text(text):
    normalized = []
    for ch in str(text or ""):
        if ch.isalnum():
            normalized.append(ch.lower())
    return "".join(normalized)


def _page_body_is_sparse(markdown):
    # Short pages are common in reports (section openers, glossary intros, and
    # continuation labels). Replacing any existing text can duplicate the full
    # content_list payload, so backfill only a genuinely empty marker span.
    return not str(markdown or "").strip()


def _page_payload_is_represented(markdown, rebuilt_body):
    """Return whether enough page-specific payload already exists in Markdown."""
    markdown_normalized = _normalized_anchor_text(markdown)
    payload_normalized = _normalized_anchor_text(rebuilt_body)
    chunk_size = 40
    chunks = [
        payload_normalized[offset : offset + chunk_size]
        for offset in range(0, len(payload_normalized) - chunk_size + 1, chunk_size)
    ]
    if not chunks:
        return payload_normalized in markdown_normalized
    matched = sum(chunk in markdown_normalized for chunk in chunks)
    # Require broad payload coverage. A low threshold can match recurring report
    # headers and suppress recovery of a genuinely missing page.
    return matched / len(chunks) >= 0.60


def _markdown_from_page_payload(page_payload):
    if not isinstance(page_payload, dict):
        return ""
    blocks = page_payload.get("blocks") or []
    lines = []

    def append_line(text):
        text = str(text or "").strip()
        if not text:
            return
        if lines and lines[-1] == text:
            return
        lines.append(text)

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            level = block.get("text_level")
            if isinstance(level, int) and level > 0 and len(text) <= 80:
                append_line(f"{'#' * min(level, 6)} {text}")
            else:
                append_line(text)
        elif block_type == "list":
            for item in block.get("list_items") or []:
                append_line(str(item or "").strip())
        elif block_type == "table":
            for item in block.get("caption") or []:
                append_line(item)
            table_html = str(block.get("table_html") or "").strip()
            if table_html:
                append_line(table_html)
            for item in block.get("footnote") or []:
                append_line(item)
        elif block_type == "image":
            image_path = str(block.get("image_path") or "").strip()
            if image_path:
                append_line(f"![]({image_path})")
            for item in block.get("caption") or []:
                append_line(item)
            for item in block.get("footnote") or []:
                append_line(item)

    return "\n\n".join(lines).strip()


def _backfill_sparse_markdown_pages(markdown, content_list):
    content_list = coerce_json_artifact(content_list)
    text = str(markdown or "")
    if not text or not isinstance(content_list, list):
        return text, []

    matches = list(re.finditer(r"(?m)^\[PDF_PAGE:\s*(\d+)\]\s*$", text))
    if not matches:
        return text, []

    rebuilt = []
    restored_pages = []
    for index, match in enumerate(matches):
        page_number = int(match.group(1))
        marker_line = match.group(0)
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if _page_body_is_sparse(body):
            payload = page_content_payload_from_content_list(content_list, page_number)
            rebuilt_body = _markdown_from_page_payload(payload)
            if rebuilt_body and not _page_payload_is_represented(text, rebuilt_body):
                rebuilt.append(marker_line + "\n" + rebuilt_body.strip() + "\n")
                restored_pages.append(page_number)
                continue
        rebuilt.append(marker_line + body)
    return "".join(rebuilt), restored_pages


def _normalized_text_with_map(text):
    normalized = []
    raw_index_map = []
    for idx, ch in enumerate(str(text or "")):
        if ch.isalnum():
            lowered = ch.lower()
            normalized.append(lowered)
            raw_index_map.extend([idx] * len(lowered))
    return "".join(normalized), raw_index_map


def _compact_text_fragment(text, max_length=80):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()


def _collect_text_fragments(payload):
    fragments = []
    if isinstance(payload, str):
        fragments.append(payload)
    elif isinstance(payload, list):
        for item in payload[:8]:
            fragments.extend(_collect_text_fragments(item))
    elif isinstance(payload, dict):
        for key in ("text", "content", "title", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value)
                break
    return fragments


def _append_unique_fragment(bucket, seen, text):
    fragment = _compact_text_fragment(text)
    normalized = _normalized_anchor_text(fragment)
    if len(normalized) < 2 or normalized.isdigit() or normalized in seen:
        return
    seen.add(normalized)
    bucket.append(fragment)


def _page_anchor_candidates(content_list):
    content_list = coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []

    pages = {}
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        page = pages.setdefault(
            page_idx,
            {
                "primary": [],
                "secondary": [],
                "primary_seen": set(),
                "secondary_seen": set(),
            },
        )
        item_type = item.get("type")
        if item_type == "page_number":
            continue

        primary_fragments = []
        secondary_fragments = []
        if item_type == "text":
            primary_fragments.extend(_collect_text_fragments(item.get("text")))
        elif item_type == "list":
            primary_fragments.extend(_collect_text_fragments(item.get("list_items")))
        elif item_type == "table":
            primary_fragments.extend(_collect_text_fragments(item.get("table_caption")))
            primary_fragments.extend(_collect_text_fragments(item.get("table_footnote")))
        elif item_type == "image":
            if item.get("img_path"):
                primary_fragments.append(item.get("img_path"))
            primary_fragments.extend(_collect_text_fragments(item.get("image_caption")))
            primary_fragments.extend(_collect_text_fragments(item.get("image_footnote")))
        elif item_type == "header":
            secondary_fragments.extend(_collect_text_fragments(item.get("text")))
        else:
            primary_fragments.extend(_collect_text_fragments(item.get("text")))

        for fragment in primary_fragments:
            _append_unique_fragment(page["primary"], page["primary_seen"], fragment)
        for fragment in secondary_fragments:
            _append_unique_fragment(page["secondary"], page["secondary_seen"], fragment)

    ordered_pages = []
    for page_idx in sorted(pages):
        fragments = (pages[page_idx]["primary"] + pages[page_idx]["secondary"])[:8]
        if not fragments:
            continue
        candidates = []
        seen = set()
        max_join = min(4, len(fragments))
        for size in range(max_join, 0, -1):
            combined = "".join(fragments[:size])
            normalized = _normalized_anchor_text(combined)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"text": combined, "normalized": normalized})
        for fragment in fragments[:6]:
            normalized = _normalized_anchor_text(fragment)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"text": fragment, "normalized": normalized})
        if candidates:
            ordered_pages.append({"page_number": page_idx + 1, "candidates": candidates})
    return ordered_pages


def _candidate_is_unique(markdown_normalized, candidate_normalized, uniqueness_cache):
    cached = uniqueness_cache.get(candidate_normalized)
    if cached is not None:
        return cached
    first = markdown_normalized.find(candidate_normalized)
    unique = first != -1 and markdown_normalized.find(candidate_normalized, first + 1) == -1
    uniqueness_cache[candidate_normalized] = unique
    return unique


def _select_page_anchor_match(markdown_normalized, page_candidates, start_pos, uniqueness_cache):
    best_match = None
    for order, candidate in enumerate(page_candidates):
        normalized = candidate.get("normalized") or ""
        if len(normalized) < 2:
            continue
        pos = markdown_normalized.find(normalized, start_pos)
        if pos == -1:
            continue
        unique = _candidate_is_unique(markdown_normalized, normalized, uniqueness_cache)
        score = (0 if unique else 1, order, pos, -len(normalized))
        if best_match is None or score < best_match["score"]:
            best_match = {
                "pos": pos,
                "length": len(normalized),
                "score": score,
            }
            if unique and order == 0:
                break
    return best_match


def _apply_page_marker_insertions(markdown, insertions):
    if not insertions:
        return markdown
    output = []
    last_index = 0
    for insert_at, marker, _page_number in sorted(insertions, key=lambda item: (item[0], item[2])):
        insert_at = max(last_index, insert_at)
        output.append(markdown[last_index:insert_at])
        output.append(marker)
        last_index = insert_at
    output.append(markdown[last_index:])
    return "".join(output)


def _page_marker_line_start(markdown, position):
    position = max(0, min(int(position), len(markdown)))
    if position >= len(markdown):
        return len(markdown)
    return markdown.rfind("\n", 0, position) + 1


def _fill_missing_page_marker_insertions(insertions, total_pages, markdown):
    if not insertions:
        return insertions

    page_to_pos = {int(page_number): int(insert_at) for insert_at, _marker, page_number in insertions}
    if not page_to_pos:
        return insertions

    known_pages = sorted(page_to_pos)
    max_known_page = known_pages[-1]
    total_pages = max(int(total_pages or 0), max_known_page)
    if total_pages <= 0:
        return insertions

    markdown_length = len(markdown)
    for page_number in range(1, total_pages + 1):
        if page_number in page_to_pos:
            continue
        prev_known_page = next((page for page in reversed(known_pages) if page < page_number), None)
        next_known_page = next((page for page in known_pages if page > page_number), None)
        if prev_known_page is not None and next_known_page is not None:
            prev_pos = page_to_pos[prev_known_page]
            next_pos = page_to_pos[next_known_page]
            page_span = next_known_page - prev_known_page
            if page_span > 0 and next_pos > prev_pos:
                ratio = (page_number - prev_known_page) / page_span
                insert_at = int(prev_pos + (next_pos - prev_pos) * ratio)
            else:
                insert_at = next_pos
        elif prev_known_page is not None:
            insert_at = markdown_length
        else:
            insert_at = page_to_pos[next_known_page] if next_known_page is not None else markdown_length
        insert_at = _page_marker_line_start(markdown, insert_at)
        insertions.append((insert_at, _page_marker_line(page_number) + "\n", page_number))
        page_to_pos[page_number] = insert_at
    return insertions


def _inject_pdf_page_markers(markdown, content_list, total_pages=None):
    original_markdown = str(markdown or "")
    if not original_markdown:
        return original_markdown

    page_candidates = _page_anchor_candidates(content_list)
    if not page_candidates and not total_pages:
        return original_markdown

    base_markdown = _strip_page_markers(original_markdown)
    markdown_normalized, raw_index_map = _normalized_text_with_map(base_markdown)
    if not markdown_normalized or not raw_index_map:
        return original_markdown

    if not page_candidates:
        insertions = [(0, _page_marker_line(1) + "\n", 1)]
        insertions = _fill_missing_page_marker_insertions(insertions, total_pages, base_markdown)
        if len(insertions) <= 1 and PDF_PAGE_MARKER_RE.search(original_markdown):
            return original_markdown
        return _apply_page_marker_insertions(base_markdown, insertions)

    insertions = [(0, _page_marker_line(page_candidates[0]["page_number"]) + "\n", page_candidates[0]["page_number"])]
    occupied_lines = {0}
    uniqueness_cache = {}
    last_norm_pos = 0

    first_match = _select_page_anchor_match(
        markdown_normalized,
        page_candidates[0]["candidates"],
        last_norm_pos,
        uniqueness_cache,
    )
    if first_match is not None:
        last_norm_pos = first_match["pos"] + first_match["length"]

    for page in page_candidates[1:]:
        match = _select_page_anchor_match(
            markdown_normalized,
            page["candidates"],
            last_norm_pos,
            uniqueness_cache,
        )
        if match is None:
            continue
        raw_pos = raw_index_map[match["pos"]]
        line_start = base_markdown.rfind("\n", 0, raw_pos) + 1
        if line_start in occupied_lines:
            last_norm_pos = match["pos"] + match["length"]
            continue
        occupied_lines.add(line_start)
        insertions.append((line_start, _page_marker_line(page["page_number"]) + "\n", page["page_number"]))
        last_norm_pos = match["pos"] + match["length"]

    insertions = _fill_missing_page_marker_insertions(insertions, total_pages, base_markdown)
    if len(insertions) <= 1 and PDF_PAGE_MARKER_RE.search(original_markdown):
        return original_markdown
    return _apply_page_marker_insertions(base_markdown, insertions)


def _pdf_page_markers_by_line(markdown):
    markers = []
    for match in PDF_PAGE_MARKER_RE.finditer(str(markdown or "")):
        page_text = match.group(1) or match.group(2)
        try:
            page_number = int(page_text)
        except (TypeError, ValueError):
            continue
        markers.append(
            {
                "line": str(markdown or "").count("\n", 0, match.start()) + 1,
                "page_number": page_number,
            }
        )
    return markers
