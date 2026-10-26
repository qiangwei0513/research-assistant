# Runtime Budget Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复赛事 180 秒单题流程中实际存在的解析、D 生成/自测和报告修复预算漏洞。
**Architecture:** 沿用 Python 单服务和现有接口。通过可终止解析进程、扣除预留的 Deadline、整批自测预算和保留合法报告实现有界恢复。
**Tech Stack:** Python 3.11 / pypdf / FastAPI / pytest / Docker linux/amd64。
**Spec:** `docs/superpowers/specs/2026-10-24-runtime-budget-hardening-design.md`

## Global Constraints

- objective-v3 / dispatch-v3；11 题，A/B/C/D/E=1/3/2/3/2。
- D 源码来自当前会话，完整保存，不本地改算法；正式 helper 文件为权威结果。
- 每批代码变更同步接力记录；本地通过不等于 R03 平台通过。
- 不修改官方材料、凭据、历史验证产物，不推送分支。

## Review Focus

- 单篇 PDF 长时间阻塞时，整批预算仍能结束并回收子进程（Task 1）。
- PDF 哈希/元数据读取异常时，其他论文仍可使用（Task 1）。
- 多个 D 样例失败时不能重复领取整批 timeout（Task 2）。
- D 重生成失败必须保留上一份源码与正式执行时间（Task 2）。
- C/E 修复轮超时或非法结果时保留已合法的初稿（Task 3）。

### Task 1: 文档提取预算

**Files:** `documents/extractor.py`, 新建 `documents/worker.py`, `documents/model.py`, `tests/unit/test_documents.py`, 接力文档。
**Interfaces:** `extract_one(path, *, filename) -> Document` 保持；`extract_documents(..., time_budget_seconds)` 在有预算时通过子进程提取。
- [x] 写回归：实际阻塞 worker 在预算到期后被终止；解析崩溃留下 CORRUPT；批内继续；真实 PDF 子进程结果与直接结果一致。
- [x] 运行回归，确认失败。
- [x] 实现进程提取、TIMEOUT 状态和异常隔离；同步文档。
- [x] `uv run pytest tests/unit/test_documents.py -q` 通过。

### Task 2: D 阶段预算与恢复

**Files:** `handlers/reproduction.py`, `reproduction/selftest.py`, `tests/unit/test_reproduction.py`, 新增自测预算回归文件，接力文档。
**Interfaces:** Gateway 接口保持；调用时用替换 deadline 的 SolveContext。`run_self_test(timeout)` 表示整批时间。
- [x] 写回归：用模拟时钟证明每次 timeout 递减且用尽后不启动下一样例；生成上下文剩余时间比整题少 72 秒；二次生成异常保留首次源码；无源码时合法 failed。
- [x] 运行回归，确认失败。
- [x] 实现阶段预算与失败恢复；同步文档。
- [x] `uv run pytest tests/unit/test_reproduction.py tests/unit/test_selftest_budget.py -q` 通过。

### Task 3: 报告修复容错与交付验证

**Files:** 按独立审查结果限定 C/E handler 和对应测试；`AGENTS.md` 与两份接力文档。
**Interfaces:** 五类公开输出不变，合法报告初稿保留。
- [x] 复现审查发现，增加 RED→GREEN 回归；若无需实现则记录依据。
- [x] 运行 `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`。
- [x] 新鲜上下文评审本轮改动，修复重要发现。
- [x] 构建并验证交付镜像、归档；记录当前摘要、测试证据和 R03 外部依赖。


本计划实施与本地交付验证已完成（2026-10-25）；证据见接力记录。平台复评尚未执行，长期目标仍 active。D-02 后续变形性质验证属于下一阶段，不计为本计划已完成项。
