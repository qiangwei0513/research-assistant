"""Pack all retrieval evidence into bounded batches without discarding filenames."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from research_assistant.documents.retrieval import RetrievalCandidate, summarize_for_prompt

MAX_CANDIDATE_CHARS = 24000
MAX_BATCH_PAPERS = 12


def serialized_size(rows: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(list(rows), ensure_ascii=False, indent=1))


def compact_summary(summary: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    """Shorten evidence fields, preserving the actual filename and all document membership."""
    row = dict(summary)
    while serialized_size([row]) > max_chars:
        previous = serialized_size([row])
        for key, value in row.items():
            if key == "filename":
                continue
            if isinstance(value, (str, list)):
                row[key] = value[: len(value) // 2]
        if serialized_size([row]) == previous:
            raise ValueError("candidate filename exceeds the evidence character budget")
    return row


def batch_summaries(
    candidates: Sequence[RetrievalCandidate],
    *,
    max_chars: int = MAX_CANDIDATE_CHARS,
    max_papers: int = MAX_BATCH_PAPERS,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for candidate in candidates:
        row = compact_summary(summarize_for_prompt(candidate), max_chars)
        if current and (len(current) >= max_papers or serialized_size([*current, row]) > max_chars):
            batches.append(current)
            current = []
        current.append(row)
    if current:
        batches.append(current)
    return batches
