"""In-process provider health and circuit breaker.

A provider that fails repeatedly is temporarily skipped so the capability
router can move to the next source instead of paying the same timeout on every
request.  This is intentionally process-local: a restart clears it, and it is
not a replacement for an external monitoring system.

Rules:
  * 3 consecutive failures for (provider, capability) open a 180s cooldown;
  * a success immediately closes the circuit;
  * business "no data" errors should not be recorded as provider outages;
  * state is exposed as counters only, never request headers or credentials.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 180.0


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None
    last_error: str = ""


_LOCK = threading.Lock()
_CIRCUITS: dict[tuple[str, str], _Circuit] = {}


def _key(provider: str, capability: str) -> tuple[str, str]:
    return (provider, capability)


def is_available(provider: str, capability: str, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    with _LOCK:
        circuit = _CIRCUITS.get(_key(provider, capability))
        if circuit is None or circuit.opened_at is None:
            return True
        if current - circuit.opened_at >= COOLDOWN_SECONDS:
            circuit.opened_at = None
            circuit.failures = 0
            return True
        return False


def record_success(provider: str, capability: str) -> None:
    with _LOCK:
        _CIRCUITS.pop(_key(provider, capability), None)


def record_failure(provider: str, capability: str, error: str = "") -> None:
    with _LOCK:
        circuit = _CIRCUITS.setdefault(_key(provider, capability), _Circuit())
        circuit.failures += 1
        circuit.last_error = error[:160]
        if circuit.failures >= FAILURE_THRESHOLD and circuit.opened_at is None:
            circuit.opened_at = time.monotonic()


def snapshot() -> list[dict]:
    with _LOCK:
        now = time.monotonic()
        rows = []
        for (provider, capability), circuit in sorted(_CIRCUITS.items()):
            remaining = 0.0
            if circuit.opened_at is not None:
                remaining = max(0.0, COOLDOWN_SECONDS - (now - circuit.opened_at))
            rows.append({
                "provider": provider,
                "capability": capability,
                "failures": circuit.failures,
                "open": bool(circuit.opened_at is not None and remaining > 0),
                "cooldown_remaining_seconds": round(remaining, 1),
                "last_error": circuit.last_error,
            })
        return rows


def reset() -> None:
    with _LOCK:
        _CIRCUITS.clear()
