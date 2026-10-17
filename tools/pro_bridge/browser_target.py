"""Check host-supplied browser observations; never drive or attest a browser."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit


IDENTITY_FIELDS = ("mode", "project_id", "conversation_id", "browser_context", "tab_id", "model_label", "effort_label")
MAX_OBSERVATION_AGE = 120


def read_object(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("browser input must be a JSON object")
    return value


def _label(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def validate_target(target: dict) -> None:
    if not isinstance(target, dict) or set(target) != set(IDENTITY_FIELDS):
        raise ValueError("browser target requires exactly: " + ", ".join(IDENTITY_FIELDS))
    if target["mode"] not in ("CHAT", "WORK"):
        raise ValueError("browser target mode must be CHAT or WORK")
    for key in IDENTITY_FIELDS:
        if key == "project_id" and target[key] is None:
            continue  # Explicitly outside a project; missing/unknown is not null.
        if not _label(target[key]):
            raise ValueError("invalid browser target field: " + key)
    if target["model_label"] == "UI_SELECTION_REQUIRED" or target["effort_label"] == "UI_SELECTION_REQUIRED":
        raise ValueError("browser target requires resolved model and effort labels")


def check_target(target: dict, observation: dict, *, now: float) -> dict:
    validate_target(target)
    if not isinstance(observation, dict):
        raise ValueError("browser observation must be a JSON object")
    reasons = []
    mismatches = [key for key in IDENTITY_FIELDS if key not in observation or observation[key] != target[key]]
    if mismatches:
        reasons.append("target_mismatch")
    observed_at = observation.get("observed_at")
    if (type(observed_at) not in (int, float) or not 0 <= observed_at <= now
            or now - observed_at > MAX_OBSERVATION_AGE):
        reasons.append("observation_stale_or_invalid")
    for key in ("logged_in", "composer_ready", "selection_unambiguous"):
        if observation.get(key) is not True:
            reasons.append(key + "_not_confirmed")
    # These are evidence references from the host, not proof against forgery.
    for key in ("mode_evidence", "project_evidence", "conversation_evidence"):
        if not _label(observation.get(key)):
            reasons.append(key + "_required")
    try:
        page_url = observation.get("page_url", "")
        url = urlsplit(page_url if isinstance(page_url, str) else "")
        valid_url = (url.scheme == "https" and url.hostname == "chatgpt.com"
                     and url.port in (None, 443) and not url.username and not url.password)
    except (TypeError, ValueError):
        valid_url = False
    if not valid_url:
        reasons.append("unsupported_page_origin")
    mode_supported = target["mode"] == "CHAT"
    if not mode_supported:
        reasons.append("work_dispatch_not_supported")
    return {
        "check_passed": not reasons,
        "target_matches": not mismatches,
        "mode_supported": mode_supported,
        "mismatched_fields": mismatches,
        "reasons": reasons,
        "expected_usage_pool": "CHAT" if mode_supported else "WORK_CODEX_SHARED",
        "actual_usage_charge": "NOT_OBSERVABLE",
        "evidence_origin": "host_browser_observation",
        "browser_independently_inspected": False,
        "send_authorized": False,
        "claim_required": True,
        "observation_max_age_seconds": MAX_OBSERVATION_AGE,
    }
