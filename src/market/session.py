from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

import pandas as pd


MORNING_START = time(9, 0)
MORNING_END = time(11, 30)
AFTERNOON_START = time(12, 30)
AFTERNOON_END = time(15, 30)


@dataclass(frozen=True)
class SessionRule:
    name: str
    start_time: str
    end_time: str
    action: str = "block_new_entries"
    position_multiplier: float = 1.0


def session_name(timestamp: pd.Timestamp) -> str:
    current = pd.Timestamp(timestamp).time()
    if MORNING_START <= current < MORNING_END:
        return "morning"
    if current == MORNING_END:
        return "morning_close"
    if AFTERNOON_START <= current < AFTERNOON_END:
        return "afternoon"
    if current == AFTERNOON_END:
        return "afternoon_close"
    if MORNING_END < current < AFTERNOON_START:
        return "lunch_break"
    return "closed"


def is_continuous_auction(timestamp: pd.Timestamp) -> bool:
    return session_name(timestamp) in {"morning", "afternoon"}


def time_in_range(timestamp: pd.Timestamp, start_time: str, end_time: str) -> bool:
    hhmm = pd.Timestamp(timestamp).strftime("%H:%M")
    if start_time <= end_time:
        return start_time <= hhmm <= end_time
    return hhmm >= start_time or hhmm <= end_time


def load_session_rules(config: dict[str, Any]) -> list[SessionRule]:
    raw_rules = config.get("market", {}).get("session_filters", [])
    if isinstance(raw_rules, dict):
        raw_rules = [raw_rules]
    if not isinstance(raw_rules, list):
        return []
    rules: list[SessionRule] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        rules.append(
            SessionRule(
                name=str(raw.get("name", f"session_rule_{index + 1}")),
                start_time=str(raw.get("start_time", "")),
                end_time=str(raw.get("end_time", "")),
                action=str(raw.get("action", "block_new_entries")),
                position_multiplier=float(raw.get("position_multiplier", 1.0)),
            )
        )
    return rules


def session_block_reason(timestamp: pd.Timestamp, config: dict[str, Any]) -> str:
    current_session = session_name(timestamp)
    if current_session in {"closed", "lunch_break"}:
        return f"session_closed({current_session})"
    for rule in load_session_rules(config):
        if not rule.start_time or not rule.end_time:
            continue
        if rule.action == "block_new_entries" and time_in_range(timestamp, rule.start_time, rule.end_time):
            return rule.name
    return ""

