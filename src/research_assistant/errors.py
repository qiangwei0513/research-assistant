"""异常层级与 HTTP 状态映射。

错误响应体只用于非 2xx，与五类答案 JSON 的字段白名单互不相干。
消息必须脱敏：不得包含 API key、完整请求头或模型响应全文。
"""

from __future__ import annotations

from typing import Any


class ResearchAssistantError(Exception):
    """所有可预期错误的基类。"""

    http_status: int = 500

    def __init__(self, message: str, *, case_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.case_id = case_id


class TaskContractError(ResearchAssistantError):
    """八字段缺失、类型错误、编号非法或 function 与 output_schema_ref 不一致。"""

    http_status = 422


class DispatchValidationError(ResearchAssistantError):
    """派题记录版本、配额、题号唯一性或题目哈希不符。"""

    http_status = 422


class PathAuthorizationError(ResearchAssistantError):
    """绝对提交路径、`..`、反斜杠、符号链接越界或访问他队目录。"""

    http_status = 403


class MaterialUnavailableError(ResearchAssistantError):
    """材料路径合法但文件不存在或不可读。"""

    http_status = 422


class ConfigError(ResearchAssistantError):
    """Gateway 变量组缺失或两组冲突；只报组名与原因，不报值。"""

    http_status = 500


class BudgetExhausted(ResearchAssistantError):
    """整题预算耗尽或某阶段剩余时间不足。"""

    http_status = 504


class GatewayError(ResearchAssistantError):
    """网络失败、非 2xx、响应不可解析或 finish_reason 不是 stop。"""

    http_status = 502


class PersistenceError(ResearchAssistantError):
    """写入失败、回读不一致或权限不足；不得报告成功。"""

    http_status = 500


class HandlerNotImplemented(ResearchAssistantError):
    """该类别在 P1 尚未实现。"""

    http_status = 501


def error_body(exc: ResearchAssistantError) -> dict[str, Any]:
    return {
        "error": {
            "type": type(exc).__name__,
            "message": exc.message,
            "case_id": exc.case_id,
        }
    }
