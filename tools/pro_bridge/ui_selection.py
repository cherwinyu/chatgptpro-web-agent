"""Offline validation of a host's control-discovery trace, not browser automation.

Values and key counts never name a model or effort. Only observed text can
satisfy the requested labels. Passing this check does not authorize a send.
"""
from __future__ import annotations

import math

MAX_AGE_SECONDS = 120
MAX_STEPS = 32


def _label(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def check_selection(evidence: object, *, model: str, effort: str, now: float) -> dict:
    reasons: list[str] = []
    if not isinstance(evidence, dict):
        evidence = {}
        reasons.append("selection_evidence_required")
    if not _label(model) or not _label(effort) or "UI_SELECTION_REQUIRED" in (model, effort):
        reasons.append("requested_labels_unresolved")
    if evidence.get("mode") != "CHAT":
        reasons.append("chat_mode_not_confirmed")
    for key in ("logged_in", "permission_confirmed", "selection_unambiguous"):
        if evidence.get(key) is not True:
            reasons.append(key + "_not_confirmed")
    if evidence.get("locked") is not False:
        reasons.append("control_locked_or_unknown")
    if not _label(evidence.get("evidence_ref")):
        reasons.append("evidence_ref_required")
    stamp = evidence.get("observed_at")
    if not _number(stamp) or not 0 <= now - stamp <= MAX_AGE_SECONDS:
        reasons.append("selection_observation_stale_or_invalid")
    kind = evidence.get("control_kind")
    if kind not in ("slider", "radio", "menu", "none"):
        reasons.append("unsupported_or_ambiguous_control")
    if kind == "none" and (effort != "NOT_AVAILABLE" or evidence.get("absence_confirmed") is not True):
        reasons.append("no_control_is_not_requested_effort")
    steps = evidence.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        steps = []
        reasons.append("bounded_selection_trace_required")
    final: dict = {}
    previous: dict | None = None
    reached = False
    for step in steps:
        if not isinstance(step, dict):
            reasons.append("invalid_selection_step")
            continue
        if reached:
            reasons.append("adjustment_after_requested_labels_reached")
        if step.get("model_label") != model:
            reasons.append("wrong_model_in_effort_trace")
        if not _label(step.get("effort_label")):
            reasons.append("observed_effort_label_required")
        if kind == "slider":
            if not _number(step.get("value")):
                reasons.append("slider_value_invalid")
            if previous is not None:
                if step.get("value") == previous.get("value"):
                    reasons.append("slider_did_not_move")
                if step.get("effort_label") == previous.get("effort_label"):
                    reasons.append("slider_label_did_not_update")
        final = step
        previous = step
        reached = step.get("model_label") == model and step.get("effort_label") == effort
    if final.get("model_label") != model or final.get("effort_label") != effort:
        reasons.append("requested_labels_not_selected")
    reasons = list(dict.fromkeys(reasons))
    return {
        "selection_ready_for_prepare": not reasons,
        "check_passed": not reasons,
        "reasons": reasons,
        "observed_model_label": final.get("model_label"),
        "observed_effort_label": final.get("effort_label"),
        "control_kind": kind,
        "steps_observed": len(steps),
        "evidence_origin": "host_control_discovery_trace",
        "browser_independently_inspected": False,
        "backend_model_id": "NOT_OBSERVABLE",
        "send_authorized": False,
        "claim_required": True,
    }
