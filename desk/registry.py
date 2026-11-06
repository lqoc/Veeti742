"""The 5 running agents, created once by main.py (or by tests)."""
from __future__ import annotations

AGENTS: dict = {}
ORDER = ("scanner", "auditor", "analyst", "executor", "risk")


def build() -> dict:
    from .agents.analyst import Analyst
    from .agents.auditor import Auditor
    from .agents.executor import Executor
    from .agents.risk import Risk
    from .agents.scanner import Scanner

    if not AGENTS:
        for cls in (Scanner, Auditor, Analyst, Executor, Risk):
            a = cls()
            AGENTS[a.name] = a
    return AGENTS


def get(name: str):
    return build()[name]
