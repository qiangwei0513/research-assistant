# Reference Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, RED/GREEN and a fresh final reviewer.

**Goal:** 消除可唯一修复的缺引用条目，按实际质量层次采纳修复并留下候选证据。
**Architecture:** 纯文本参考补全与独立修复决策，C/E共享，保留原有最多一轮模型修复。
**Tech Stack:** Python标准库、现有EvidencePack/CitationAnalysis/pytest。
**Spec:** docs/superpowers/specs/2026-10-25-reference-repair-design.md

## Global Constraints

- 仅授权材料、原编号、完整原文件名；不改正文事实与编号，不生成正式评分。
- 正文800下限、180秒上限、已有合法稿保留、原输出与落盘合同保持。
- 每批代码同步文档；不改官方材料、D源码、凭据、外部提交。

## Review Focus

- 无引用表、编号重排、重复编号、未知材料、超范围引用均不能猜测补全。
- 补全不能改正文、增加未被正文引用的论文或悄悄重编号。
- 同样问题总数但合法性变好可采纳；修数字不能换进新的非法引文。
- 内容/数值或必需覆盖退化不能因少一个软问题而采纳。
- 初稿与候选走相同检查，并记录被拒候选的具体问题类型和原因。

### Task 1: 共享修复和接入

**Files:** 新增 validation/report_repair.py、tests/unit/test_reference_repair.py；修改 handlers/idea.py、survey.py及相关测试与接力文档。
**Interfaces:** complete_missing_references(markdown,pack)->ReferenceRepair；decide_repair(before,after,content_before=0,content_after=0,min_sources=0)->RepairDecision。
- [x] 先写补全歧义、严重程度、数值/内容/覆盖门槛、C/E路径回归并跑RED。
- [x] 实现确定性补条目与统一决策，接入初稿/候选并详细日志。
- [x] 定向、全套、静态检查；独立审查重要发现RED→GREEN。

### Task 2: 镜像验收

**Files:** 开发证据、文档、镜像归档。
- [x] 保存C01旧报告原样补全核验；新镜像真实C/E验证。
- [x] 构建并记录摘要与证据边界；长期目标保持active。


本阶段本地实施与镜像验证完成。平台新成绩未验证，长期目标仍active。保存C01补全后问题0；新镜像C01问题0，E02剩1处thin_context。严格文件身份全局解析与事实语义覆盖另行推进。
