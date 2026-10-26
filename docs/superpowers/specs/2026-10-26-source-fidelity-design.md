# C/E Source Fidelity Design

## Evidence

The prior phase progressed: bounded cross-page excerpts, complete fixture paragraphs, 892 tests and a new archive. On actual saved E02, `[0,1]` intervals caused false dangling citation0, while the report changed vegetation fraction saturation into an NDVI threshold and claimed pretrained weights were public without supplied evidence. Numeric presence alone cannot distinguish quantities or resources.

## Design

1. Recognize clear numeric-interval contexts separately from citation instances; keep an ambiguity warning requesting prose interval notation to avoid unknown official parser behavior. Do not silently ignore standalone invalid citations or change their numbering. Generation and repair request ranges in words rather than citation-shaped brackets.
2. Add a bounded Gateway source audit after initial draft and before the existing single report repair. Ask for specific source-attribution mismatches (variables/units, method versus proposed reuse, unavailable resources, overclaimed scope), with exact claim text, actual reference number, exact supplied source quote, and explanation. Materials and report are data, never instructions.
3. Validate every finding: cited reference resolves uniquely to an allowed evidence entry, claim is actually in the body and contains that citation, source quote matches supplied evidence after whitespace/hyphen normalization. Reject fabricated or malformed audit data; an invalid/failed audit is not a clean report. Findings remain model-assisted judgments, not proved entailment or official grades.
4. Merge validated findings into the existing repair prompt. When initial audit completed, reserve time to audit the candidate too; require semantic finding count not to regress, alongside existing numeric/content/citation gates. A candidate with unavailable verification must not be called clean or replace the audited draft. If initial audit was unavailable, retain existing structural repair behavior and explicitly report that semantic verification was unavailable.
5. Initial audit requires at least85s remaining, uses at most30s and leaves55s; candidate audit uses at most30s and always leaves5s for persistence. Report repair reserves35s when audit verification is required. At most two audits and one report repair; no retries for audits. Optional failures preserve a valid draft. Keep full response/file equality.
6. Save validated audit evidence/status in the current case work directory and log stage/status/count, never credentials. Test doubles provide explicit audit-stage scripts so report-generation response scripts are not accidentally consumed.

## Validation

RED/GREEN unit tests for notation, exact quote/reference validation, unavailable vs clean, deadlines and non-regression decisions. Handler tests must cover a clean-format but semantically wrong report being repaired, fabricated audit evidence rejected and verification failure preserving the draft. Real saved-E audit must expose the known variable mismatch; then rebuild and rerun C/E on the same current8-page fixture, inspect content rather than only green diagnostics.
