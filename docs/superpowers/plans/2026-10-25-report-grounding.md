# Report Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline with fresh final review.

**Goal:** 阻止短正文靠参考文献满足800字符，并发现明确论文数值在所引PDF缺少来源。
**Architecture:** 共享正文计数工具，轻量数字归因诊断接入现有 C/E 引文分析与单次修复。
**Tech Stack:** Python标准库、pytest、现有 Gateway/PDF 解析。
**Spec:** docs/superpowers/specs/2026-10-25-report-grounding-design.md

## Global Constraints

- C/E原输出字段与落盘相等不变；正文至少800字符，引用表不计正文。
- 不增加模型调用轮数；180秒与已有合法稿回退规则保持。
- 仅诊断授权论文，无正文证据不伪造；数字存在不等于语义证明。
- 同批更新文档，不改官方材料/已生成D源码/凭据。

## Review Focus

- 长参考文献不能补短正文；正文800字符合法，代码中的伪参考标题不截断正文。
- 同主题其他论文的数字不能支持所引论文；未知材料交由已有引文规则处理。
- 计划目标、模拟观察与实测结果区分；不得把设计数值当伪造事实。
- 百分数/小数和常见中英文表示避免假误诊；引用编号/年份不污染正文数值。
- 修复若引文更好但新增无据数值不得采纳；所有变更继续回退保留合法稿。

### Task 1: 正文与归因

**Files:** 新增 contracts/report_text.py、validation/grounding.py；修改 contracts/results.py、handlers/idea.py、handlers/survey.py；新增 tests/unit/test_report_grounding.py。
**Interfaces:** report_body_characters(markdown)->int；analyze_report_evidence(markdown, documents, allowed)->CitationAnalysis；现有输出构造器保持接口。
- [x] 先编写正文填充、围栏标题、数值跨文献误归因、设计值例外与修复采纳回归，跑RED。
- [x] 实现并接入生成/修复路径；保留合法原稿，同步接力文档。
- [x] 定向与全套、ruff、独立审查与重要问题RED→GREEN。

### Task 2: 真实验证与交付

**Files:** live测试（必要时）、文档与镜像归档。
- [x] 复查保存E02报告，证明可发现上述无据数值；真实C/E生成验证。
- [x] 构建镜像及新归档，记录精确摘要与证据边界，目标保持active。


本阶段本地实施与镜像验证完成。平台新分数未获得；C01镜像报告仍有1处dangling_number，另立下一阶段修复采纳/引文质量工作。长期目标继续active，未标记赛事目标完成。
