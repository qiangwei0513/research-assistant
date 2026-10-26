# C/E reference identity design

## Evidence and scope

The current `_match_allowed_filename` uses substring membership and iterates a frozenset. Thus `unknown_paper.pdf` can resolve to authorized `paper.pdf`, and one entry containing two PDF names can arbitrarily resolve to either. Coverage and numeric grounding both inherit that error. This phase fixes reference identity, not factual entailment or the entire Markdown parser.

## Decision

Extract one complete locator before checking authorization. Accept an exact filename-only entry (including spaces), paired backtick/single/double quoted filename with metadata, or an explicit `PDF:` / `文件名：` locator. Never accept an authorized suffix of an unknown name. Multiple PDF locators, suffixes such as `.pdf.bak`, and free author/title prose without a delimited locator remain unresolved. Plain single-token unknown PDFs remain diagnosable as unknown; unresolved text requests explicit formatting rather than guessing identity.

This deliberately tightens ambiguous legacy author/title references; generation and repair prompts must request `[编号] 作者/标题. `完整文件名.pdf`` or filename-only entries. Existing positive fixtures must demonstrate a real locator rather than the removed heuristic. Preserve exact case, Unicode and punctuation, and do not alter actual report content locally.

All consumers (coverage, numeric grounding, repair selection and completion) use the same parsed identity. No extra LLM call, no new output field, no official score claim. Material scope and response/file equality are unchanged.

## Verification

RED/GREEN examples for embedded and overlapping names, space-containing names, unique quoted/labelled names, multiple names and malformed suffixes. A downstream grounding example must prove an unknown filename cannot borrow authorized numeric evidence. Run report handler regressions, whole suite, a fresh independent review, build and image C/E smoke; record live versus saved evidence separately.
