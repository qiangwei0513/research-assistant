# C/E Evidence Coverage Design

## Current evidence

Previous phase made progress: complete filename identity and fresh image validation, 872 passing tests. Current `build_evidence_pack` subtracts unbounded metadata abstract then consumes pages from the beginning. A 2200-character budget can contain a 3780-character abstract and no body; ordinary long introductions hide later results. Saved C01 invented a climate applicability boundary despite zero citation/numeric warnings. The earlier saturation-omission allegation is withdrawn: the saved two-page fixture PDF did not contain its configured third paragraph. Saved E02 repair introduced four dangling citations and was correctly rejected.

## Implementation decisions

- Bound abstract plus source excerpt characters by the requested per-document budget. If everything fits, preserve it. Otherwise reserve body space (abstract at most one quarter, at most 1000 characters); leave full original Document untouched for verification. Metadata fields and rendering labels are outside this content-character budget.
- When remaining source text fits, retain all pages. Otherwise split into contiguous, bounded excerpts at sentence/paragraph boundaries where possible. Retain original page index/label; never splice disconnected sentences into a purported quotation.
- Cover results/conclusions, limitations/counterevidence, and methods/data using generic English/Chinese cues, reserving a quarter for spatial diversity. Prefer later result/limitation headings over equal-strength front summaries. Limit truncated documents to 16 excerpts and maintain nearest-position distances incrementally. Preserve original order in the rendered evidence. The selector is a deterministic recall improvement, not an entailment classifier or promise of complete coverage.
- Mark excerpted evidence explicitly. Missing details in excerpts must not be described as absent from the original paper. Separate original claims from the author's analysis or proposed hypotheses.
- Generation should prioritize specific findings, thresholds, limitations and contrasting evidence, rather than minimum-length compliance. Keep current output/time budgets. Repair requests explicitly retain the complete reference list.
- No new model round, dependency or external source. B uses its existing retrieval evidence path; D source/admission logic unchanged. Only C/E evidence packs are affected.

## Verification

TDD: long abstracts cannot overshoot or suppress all body; late results and limitations survive front matter; unmarked pages receive spatial coverage; every excerpt is contiguous in its cited original page; short papers remain intact; shrinking budgets actually shrinks content. Then whole suite, independent review, new image and real C/E on existing self-built fixtures. Distinguish input coverage and observed report quality from official scoring or semantic proof.
