"""Per-video spend tracking and the hard cost cap (PLAN.md constraint 7)."""

from .db import DB
from .errors import CostCapExceeded
from .log import log


class CostTracker:
    def __init__(self, db: DB, job_id: str, cap_usd: float):
        self.db = db
        self.job_id = job_id
        self.cap_usd = cap_usd

    @property
    def total(self) -> float:
        return self.db.total_cost(self.job_id)

    def reserve(self, estimated_usd: float, task: str) -> None:
        """Abort before a call whose known price would push the job over the cap."""
        if self.total + estimated_usd > self.cap_usd:
            raise CostCapExceeded(
                f"{task} would cost ~${estimated_usd:.4f}, taking the job to "
                f"${self.total + estimated_usd:.4f} against a cap of ${self.cap_usd:.2f}"
            )

    def add(self, provider: str, task: str, model: str, usd: float, detail: dict | None = None) -> None:
        self.db.add_cost(self.job_id, provider, task, model, usd, detail or {})
        total = self.total
        log("cost", self.job_id, provider=provider, task=task, model=model, usd=round(usd, 6), total_usd=round(total, 6))
        if total > self.cap_usd:
            raise CostCapExceeded(f"job spend ${total:.4f} exceeded the cap of ${self.cap_usd:.2f} after {task}")
