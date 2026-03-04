"""
Deterministic splitter for BRIEFING_LONG: keeps Telegram under limit;
header only in first chunk, footer only in last; splits by country/tema blocks.
"""
from __future__ import annotations

import re

# Telegram limit 4096; use 3500 to stay safe and leave margin.
DEFAULT_MAX_CHARS = 3500

# Contract markers (must match gni/templates/briefing_long.md)
HEADER_MARKER = "🌐 GNI — BRIEFING GLOBAL"
FOOTER_MARKER = "🔐 GNI — Um passo à frente."

# Flag emoji: two regional indicator symbols (U+1F1E6..U+1F1FF)
_FLAG_BLOCK_START = re.compile(r"\n\n(?=[\U0001F1E6-\U0001F1FF]{2})")


def _extract_header(text: str) -> tuple[str, str]:
    """Return (header_line, rest). Header is first line containing HEADER_MARKER."""
    if HEADER_MARKER not in text:
        return "", text
    idx = text.find(HEADER_MARKER)
    line_end = text.find("\n", idx)
    if line_end == -1:
        line_end = len(text)
    else:
        line_end += 1
    return text[:line_end], text[line_end:].lstrip("\n")


def _extract_footer(text: str) -> tuple[str, str]:
    """Return (body_without_footer, footer_line). Footer is line containing FOOTER_MARKER."""
    if FOOTER_MARKER not in text:
        return text, ""
    idx = text.rfind(FOOTER_MARKER)
    line_start = text.rfind("\n", 0, idx)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    footer = text[line_start:].strip()
    body = text[:line_start].rstrip("\n")
    return body, footer


def _split_body_by_blocks(body: str) -> list[str]:
    """Split body by flag-emoji block delimiters (\\n\\n🇺🇸 etc). Returns list of blocks."""
    if not body.strip():
        return []
    parts = _FLAG_BLOCK_START.split(body)
    blocks = [p.strip() for p in parts if p.strip()]
    return blocks


def _split_large_block(block: str, max_chars: int) -> list[str]:
    """Split a single block by paragraphs (\\n\\n); if one paragraph exceeds max_chars, split by size."""
    if len(block) <= max_chars:
        return [block] if block else []
    paragraphs = re.split(r"\n\n+", block)
    out: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_strip = p.strip()
        if not p_strip:
            continue
        need = len(p_strip) + (2 if current else 0)
        if current_len + need > max_chars and current:
            out.append("\n\n".join(current))
            current = []
            current_len = 0
        if len(p_strip) > max_chars:
            # Single paragraph too large: hard split by max_chars
            for i in range(0, len(p_strip), max_chars):
                out.append(p_strip[i : i + max_chars])
            continue
        current.append(p_strip)
        current_len += need
    if current:
        out.append("\n\n".join(current))
    return out


def split_briefing_long(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """
    Split LONG briefing so each chunk is at most max_chars. Deterministic.

    - Header (line with "🌐 GNI — BRIEFING GLOBAL") only in the first chunk.
    - Footer (line with "🔐 GNI — Um passo à frente.") only in the last chunk.
    - Prefer splitting by country/tema blocks (\\n\\n followed by flag emoji).
    - Never cut in the middle of a block; if a block is too large, split by paragraphs.

    Returns list of strings (1 if text fits, else multiple). Empty input => [].
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    header, after_header = _extract_header(text)
    body, footer = _extract_footer(after_header)

    blocks = _split_body_by_blocks(body)
    if not blocks:
        # No flag blocks: treat whole body as one block and split by paragraphs
        blocks = [body] if body else []

    # Expand any block that's too large into sub-blocks (by paragraph)
    expanded: list[str] = []
    for b in blocks:
        if len(b) > max_chars:
            expanded.extend(_split_large_block(b, max_chars))
        else:
            expanded.append(b)

    # Pack into chunks: first chunk gets header + content; last gets content + footer
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    header_len = len(header) + (1 if header else 0)
    footer_len = len(footer) + (1 if footer else 0)

    for block in expanded:
        max_body = (max_chars - header_len) if not chunks else max_chars
        if len(block) > max_body:
            block_parts = _split_large_block(block, max_body)
            for bp in block_parts:
                need_bp = len(bp) + (2 if current else 0)
                if current_len + need_bp > max_body and current:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_len = 0
                    max_body = max_chars
                current.append(bp)
                current_len += need_bp
            continue
        need = len(block) + (2 if current else 0)
        if current_len + need > max_body and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
            max_body = max_chars
        current.append(block)
        current_len += need

    if current:
        chunks.append("\n\n".join(current))

    # Prepend header to first, append footer to last
    if not chunks:
        return [header + "\n" + footer] if (header or footer) else []
    result: list[str] = []
    for i, c in enumerate(chunks):
        if i == 0 and header:
            c = header + "\n" + c
        if i == len(chunks) - 1 and footer:
            c = c + "\n" + footer
        result.append(c)

    # If last chunk with footer exceeds limit, split it and keep footer only on final part
    if result and footer and len(result[-1]) > max_chars:
        last = result.pop()
        # Remove footer from last to get body
        if last.endswith("\n" + footer):
            last_body = last[: -len(footer) - 1].rstrip()
        else:
            last_body = last
        extra = _split_large_block(last_body, max_chars - len(footer) - 2)
        for part in extra[:-1]:
            result.append(part)
        result.append(extra[-1] + "\n" + footer if extra else footer)
    return result
