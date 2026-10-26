# Evidence Coverage Implementation Plan

Use Superpowers executing-plans inline, TDD, and a fresh final reviewer. User has authorized autonomous code/doc updates.

- [x] Add RED tests for long abstract budget, late results/limitations, spatial diversity and exact page provenance.
- [x] Implement bounded source excerpts and excerpt markers; update generation and repair prompts, handoff documentation in same batch.
- [x] Targeted/full verification and independent review; address important findings with regression evidence.
- [ ] Build and export image, verify real C/E, inspect content fidelity, record artifact digest and remaining gaps.

Owned: documents/evidence.py, new documents/excerpts.py, C/E handlers, tests/unit/test_evidence_coverage.py, handoff/spec docs. Preserve official source materials, prior results and unrelated dirty work. Do not equate citation diagnostics with factual correctness.

2026-10-26：实现/892测试/独立审查/新镜像构建完成；实际8页源文C01验收通过。最后验收项暂不勾选：E02虽然HTTP200与落盘一致，数学区间引文解析和变量来源语义仍未通过，保留失败证据继续处理。当前镜像9d492c3c…82a6。
