# Reference Identity Implementation Plan

Use Superpowers executing-plans inline, TDD and a fresh final reviewer. User has authorized implementation without further confirmation.

- [x] Add failing full-name identity/ambiguity and downstream grounding tests.
- [x] Replace substring authorization with unique complete locator extraction; strengthen C/E generation/repair guidance; update affected canonical fixtures and handoff docs in same batch.
- [x] Run targeted/full tests and lint; independent final review, fix important findings with RED/GREEN.
- [x] Rebuild upload image; verify saved reports and fresh image C/E, record digest and remaining quality gaps.

Owned: validation/citations.py, handlers/survey.py, handlers/idea.py, citation/report tests, docs. Preserve unrelated work, official materials and source-generated D implementations. Long-term goal stays active pending substantive quality work and platform reevaluation.

本阶段完成：872默认测试通过，新镜像C01/E02均HTTP200且输出落盘一致。引用身份合法，E02仍有2项软提示；C01实质事实遗漏/无据外推仍待下一阶段。平台分数未复测。
