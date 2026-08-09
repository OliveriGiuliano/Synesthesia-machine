"""Consistent bounded formatting for authored UI help text."""

from __future__ import annotations

from html import escape
from textwrap import wrap

TOOLTIP_WRAP_THRESHOLD = 120
TOOLTIP_LINE_WIDTH = 72


def format_tooltip(text: str) -> str:
    """Keep concise help plain and wrap longer help into a compact Qt rich-text panel."""

    stripped = text.strip()
    if not stripped:
        return ""
    if "\n" not in stripped and len(stripped) <= TOOLTIP_WRAP_THRESHOLD:
        return stripped

    lines: list[str] = []
    for paragraph in stripped.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(
            wrap(
                paragraph.strip(),
                width=TOOLTIP_LINE_WIDTH,
                break_long_words=True,
                break_on_hyphens=False,
            )
        )
    body = "<br>".join(escape(line, quote=False) if line else "&nbsp;" for line in lines)
    return f'<qt><div style="white-space: nowrap;">{body}</div></qt>'


__all__ = ["TOOLTIP_LINE_WIDTH", "TOOLTIP_WRAP_THRESHOLD", "format_tooltip"]
