"""v3 派题记录。

结构校验是纯函数；文件与哈希核验需要工作区路径，单独走 verify_questions。
schema_version=2 或整数 cases_per_category 的记录只作为明确标识的历史调试配置接受。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from research_assistant.errors import DispatchValidationError
from research_assistant.runtime.paths import WorkspacePaths

RULES_VERSION_V3 = "research-assistant-objective-v3"
DISPATCH_POLICY_VERSION_V3 = "research-assistant-dispatch-v3"
SELECTION_MODE_V3 = "random_a_fixed_bcde"
# 1/3/2/3/2 是评分关键常量；用 MappingProxyType 冻结，
# 与 task.SCHEMA_REF_BY_FUNCTION / results.ALLOWED_TOP_LEVEL_FIELDS 一致。
QUOTA_V3: Mapping[str, int] = MappingProxyType({"A": 1, "B": 3, "C": 2, "D": 3, "E": 2})
TOTAL_CASES_V3 = 11
SCHEMA_VERSION_V3 = 3

REQUIRED_RECORD_FIELDS: tuple[str, ...] = (
    "dispatch_id",
    "team",
    "schema_version",
    "selection_mode",
    "dispatch_policy_version",
    "dispatch_policy_sha256",
    "rules_version",
    "cases_per_category",
    "case_ids",
    "cases",
)
REQUIRED_CASE_FIELDS: tuple[str, ...] = (
    "case_id",
    "function",
    "title",
    "question_file",
    "question_sha256",
)


@dataclass(frozen=True)
class DispatchCase:
    case_id: str
    function: str
    title: str
    question_file: str
    question_sha256: str
    difficulty: str | None = None


@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    team: str
    schema_version: int
    selection_mode: str
    dispatch_policy_version: str
    dispatch_policy_sha256: str
    rules_version: str
    cases_per_category: Mapping[str, int]
    difficulty_by_case: Mapping[str, str]
    case_ids: tuple[str, ...]
    cases: tuple[DispatchCase, ...]

    @property
    def is_legacy(self) -> bool:
        return self.schema_version != SCHEMA_VERSION_V3

    def summary(self) -> dict[str, Any]:
        """可写日志的摘要：不含题目正文，不含任何凭据。"""
        return {
            "dispatch_id": self.dispatch_id,
            "team": self.team,
            "schema_version": self.schema_version,
            "rules_version": self.rules_version,
            "dispatch_policy_version": self.dispatch_policy_version,
            "dispatch_policy_sha256": self.dispatch_policy_sha256,
            "case_count": len(self.case_ids),
            "cases_per_category": dict(self.cases_per_category),
            "legacy": self.is_legacy,
        }

    def assert_team(self, expected_team: str) -> None:
        if self.team != expected_team:
            raise DispatchValidationError(
                f"dispatch team {self.team!r} does not match the runtime team {expected_team!r}"
            )

    @classmethod
    def load(cls, path: Path) -> DispatchRecord:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise DispatchValidationError(
                f"cannot read dispatch record: {exc.strerror or exc.__class__.__name__}"
            ) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise DispatchValidationError(f"dispatch record is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise DispatchValidationError("dispatch record must be a JSON object")
        return cls._parse(payload)

    @classmethod
    def _parse(cls, payload: Mapping[str, Any]) -> DispatchRecord:
        for field in REQUIRED_RECORD_FIELDS:
            if field not in payload:
                raise DispatchValidationError(f"dispatch record is missing field: {field}")

        schema_version = payload["schema_version"]
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise DispatchValidationError("schema_version must be an integer")

        quota_raw = payload["cases_per_category"]
        legacy = schema_version != SCHEMA_VERSION_V3
        quota: dict[str, int] = {}
        if not legacy:
            if not isinstance(quota_raw, Mapping):
                raise DispatchValidationError(
                    "cases_per_category must be a dictionary in schema_version 3"
                )
            for category, count in quota_raw.items():
                if not isinstance(count, int) or isinstance(count, bool):
                    raise DispatchValidationError(
                        f"cases_per_category[{category!r}] must be an integer"
                    )
                quota[str(category)] = count

        case_ids_raw = payload["case_ids"]
        if not isinstance(case_ids_raw, list) or not all(
            isinstance(item, str) for item in case_ids_raw
        ):
            raise DispatchValidationError("case_ids must be an array of strings")
        case_ids = tuple(case_ids_raw)

        if len(set(case_ids)) != len(case_ids):
            seen: set[str] = set()
            duplicates: list[str] = []
            for case_id in case_ids:
                if case_id in seen and case_id not in duplicates:
                    duplicates.append(case_id)
                seen.add(case_id)
            raise DispatchValidationError(f"case_ids contains duplicate entries: {duplicates}")

        cases_raw = payload["cases"]
        if not isinstance(cases_raw, list):
            raise DispatchValidationError("cases must be an array")
        cases: list[DispatchCase] = []
        for index, entry in enumerate(cases_raw):
            if not isinstance(entry, Mapping):
                raise DispatchValidationError(f"cases[{index}] must be an object")
            for field in REQUIRED_CASE_FIELDS:
                if field not in entry:
                    raise DispatchValidationError(f"cases[{index}] is missing {field}")
                if not isinstance(entry[field], str):
                    raise DispatchValidationError(f"cases[{index}].{field} must be a string")
            difficulty = entry.get("difficulty")
            if difficulty is not None and not isinstance(difficulty, str):
                raise DispatchValidationError(f"cases[{index}].difficulty must be a string")
            cases.append(
                DispatchCase(
                    case_id=entry["case_id"],
                    function=entry["function"],
                    title=entry["title"],
                    question_file=entry["question_file"],
                    question_sha256=entry["question_sha256"],
                    difficulty=difficulty,
                )
            )

        difficulty_by_case_raw = payload.get("difficulty_by_case") or {}
        if not isinstance(difficulty_by_case_raw, Mapping):
            raise DispatchValidationError("difficulty_by_case must be an object")

        record = cls(
            dispatch_id=str(payload["dispatch_id"]),
            team=str(payload["team"]),
            schema_version=schema_version,
            selection_mode=str(payload["selection_mode"]),
            dispatch_policy_version=str(payload["dispatch_policy_version"]),
            dispatch_policy_sha256=str(payload["dispatch_policy_sha256"]),
            rules_version=str(payload["rules_version"]),
            cases_per_category=quota,
            difficulty_by_case={str(k): str(v) for k, v in difficulty_by_case_raw.items()},
            case_ids=case_ids,
            cases=tuple(cases),
        )
        record._validate_structure(legacy)
        return record

    def _validate_structure(self, legacy: bool) -> None:
        if legacy:
            return
        if self.rules_version != RULES_VERSION_V3:
            raise DispatchValidationError(
                f"rules_version must be {RULES_VERSION_V3!r} for schema_version 3, "
                f"got {self.rules_version!r}"
            )
        if dict(self.cases_per_category) != dict(QUOTA_V3):
            raise DispatchValidationError(
                f"cases_per_category must be {dict(QUOTA_V3)}, got {dict(self.cases_per_category)}"
            )
        if len(self.case_ids) != TOTAL_CASES_V3:
            raise DispatchValidationError(
                f"a v3 dispatch must list exactly {TOTAL_CASES_V3} case_ids, "
                f"got {len(self.case_ids)}"
            )
        if len(self.cases) != len(self.case_ids):
            raise DispatchValidationError(
                "cases must correspond one-to-one with case_ids "
                f"({len(self.cases)} cases vs {len(self.case_ids)} ids)"
            )
        if {case.case_id for case in self.cases} != set(self.case_ids):
            raise DispatchValidationError("cases must correspond one-to-one with case_ids")
        actual: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        for case_id in self.case_ids:
            prefix = case_id.split("-", 1)[0]
            if prefix not in actual:
                raise DispatchValidationError(f"case_id {case_id!r} has an unknown category")
            actual[prefix] += 1
        if actual != dict(QUOTA_V3):
            raise DispatchValidationError(
                f"case_ids do not match cases_per_category: counted {actual}, "
                f"declared {dict(self.cases_per_category)}"
            )

    def verify_questions(self, workspace: WorkspacePaths) -> None:
        assigned_resolved = workspace.assigned_dir.resolve()
        for case in self.cases:
            relative = case.question_file
            if relative.startswith("/") or "\\" in relative or ".." in relative.split("/"):
                raise DispatchValidationError(
                    f"question_file must be a safe relative path: {relative!r}"
                )
            path = (workspace.root / relative).resolve()
            if assigned_resolved not in path.parents:
                raise DispatchValidationError(
                    f"question_file must live inside this team's assigned/: {relative!r}"
                )
            if not path.is_file():
                raise DispatchValidationError(f"question file is missing: {relative}")
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise DispatchValidationError(
                    f"cannot read question file {relative}: "
                    f"{exc.strerror or exc.__class__.__name__}"
                ) from exc
            if digest != case.question_sha256:
                raise DispatchValidationError(
                    f"question_sha256 mismatch for {case.case_id}: declared "
                    f"{case.question_sha256[:12]}, actual {digest[:12]}"
                )
