"""Sanitized per-turn execution traces (issue B2).

Traces record real stage events (executed, skipped, failed, denied) with
durations. They never contain prompts, model output, or customer data; only
stage names, statuses, durations, and safe identifiers. Retention is 24 hours
with a size cap. Access is authorized to the owning session only.
"""

import secrets
import time
from threading import Lock


TRACE_TTL_SECONDS = 24 * 60 * 60
MAX_TRACES = 100


class TraceStore:
    """In-memory trace store with retention limits and ownership lists."""

    def __init__(self, ttl_seconds=TRACE_TTL_SECONDS, clock=time.time,
                 max_traces=MAX_TRACES):
        self.ttl_seconds = ttl_seconds
        self.max_traces = max_traces
        self.clock = clock
        self._traces = {}
        self._lock = Lock()

    def begin(self) -> dict:
        """Create a trace and return its mutable object."""
        trace = {
            "trace_id": secrets.token_urlsafe(16),
            "turn_id": None,
            "created": self.clock(),
            "events": [],
            "outcome": "pending",
        }
        with self._lock:
            self._sweep()
            while len(self._traces) >= self.max_traces:
                oldest = min(self._traces, key=lambda tid: self._traces[tid]["created"])
                del self._traces[oldest]
            self._traces[trace["trace_id"]] = trace
        return trace

    def record(self, trace: dict, stage: str, status: str,
               duration_ms=None, detail=None) -> None:
        event = {"stage": stage, "status": status}
        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms)
        if detail is not None:
            event["detail"] = detail
        with self._lock:
            trace["events"].append(event)

    def finish(self, trace: dict, outcome: str) -> None:
        trace["outcome"] = outcome

    def view(self, trace_id: str):
        """A sanitized snapshot, or None if absent or expired."""
        with self._lock:
            self._sweep()
            trace = self._traces.get(trace_id)
        if trace is None:
            return None
        return {
            "trace_id": trace["trace_id"],
            "turn_id": trace["turn_id"],
            "outcome": trace["outcome"],
            "events": list(trace["events"]),
        }

    def forget(self, trace_id: str) -> None:
        with self._lock:
            self._traces.pop(trace_id, None)

    def _sweep(self) -> None:
        expired = [
            key for key, trace in self._traces.items()
            if trace["created"] + self.ttl_seconds < self.clock()
        ]
        for key in expired:
            del self._traces[key]
