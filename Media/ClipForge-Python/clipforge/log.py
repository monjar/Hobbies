"""Structured JSON logs on stderr. Every line carries the job ID when there is one."""

import json
import sys
import time


def log(event: str, job_id: str | None = None, level: str = "info", **fields) -> None:
    record = {"ts": round(time.time(), 3), "level": level, "event": event, "job_id": job_id}
    record.update(fields)
    print(json.dumps(record, default=str), file=sys.stderr, flush=True)
