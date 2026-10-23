"""monotonic 预算，沿调用链显式传递。

平台侧的 max_duration_ms=5400000 是阶段总上限，不由单题 Agent 分配；
timeslice_budget=300 的单位未确认，本模块不使用它。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from research_assistant.errors import BudgetExhausted


@dataclass(frozen=True)
class Deadline:
    started_at: float
    budget_seconds: float

    @classmethod
    def start(cls, budget_seconds: float) -> Deadline:
        return cls(started_at=time.monotonic(), budget_seconds=budget_seconds)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def remaining(self) -> float:
        return self.budget_seconds - self.elapsed()

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def take(self, stage: str, minimum: float, *, case_id: str | None = None) -> float:
        """领取本阶段可用秒数；不足 minimum 时抛 BudgetExhausted。"""
        remaining = self.remaining()
        if remaining < minimum:
            raise BudgetExhausted(
                f"stage {stage} needs at least {minimum:.3f}s but only "
                f"{remaining:.3f}s of {self.budget_seconds:.3f}s remains",
                case_id=case_id,
            )
        return remaining

    @contextmanager
    def guard(self, stage: str, *, case_id: str | None = None) -> Iterator[None]:
        """with 块结束时若已超支则抛 BudgetExhausted。"""
        yield
        if self.expired:
            raise BudgetExhausted(
                f"stage {stage} overran the budget of {self.budget_seconds:.3f}s "
                f"by {-self.remaining():.3f}s",
                case_id=case_id,
            )
