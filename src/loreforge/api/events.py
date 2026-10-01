"""In-process event bus for live step logs (feeds the SSE stream).

Each run has a list of subscriber queues. When a step runs (in a background thread),
its log callback publishes lines here; the SSE endpoint drains the queue to the browser.
Single-process desktop app, so a module-level registry is fine.
"""
from __future__ import annotations

import queue
import threading

_subscribers: dict[str, list[queue.Queue]] = {}
_lock = threading.Lock()

# Sentinel published to tell an SSE stream a step finished.
DONE = "__LOREFORGE_STEP_DONE__"


def subscribe(run_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(run_id)
        if subs and q in subs:
            subs.remove(q)


def publish(run_id: str, message: str) -> None:
    with _lock:
        subs = list(_subscribers.get(run_id, []))
    for q in subs:
        q.put(message)


def make_logger(run_id: str):
    """Return a log(line) callable that publishes to this run's subscribers."""
    def _log(line: str) -> None:
        publish(run_id, line)
    return _log
