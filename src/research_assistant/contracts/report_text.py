"""Shared report-body boundary, excluding bibliography without changing the saved text."""

from __future__ import annotations

import re

_REFERENCE = re.compile(r"^\s{0,3}#{1,6}\s*参考文献\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def report_body_characters(markdown: str) -> int:
    lines: list[str] = []
    fence: str | None = None
    for line in markdown.splitlines(keepends=True):
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        elif fence is None and _REFERENCE.match(line):
            break
        lines.append(line)
    return len("".join(lines).strip())
