"""Read saved receipts without inventing identity or adopting diagnostic values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("task_id", "request_sha256", "source_repository", "baseline_sha", "branch", "ledger_id")
ARGUMENTS = ("task", "expect_request_sha256", "expect_repository", "expect_baseline", "expect_branch", "expect_ledger_id")
MAX_BYTES = 1024 * 1024


class ReceiptInputError(ValueError):
    def __init__(self, message: str, mismatches: list[dict] | None = None) -> None:
        super().__init__(message)
        self.mismatches = mismatches or []


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptInputError("duplicate JSON field: " + key)
        result[key] = value
    return result


def load(path: str | Path) -> dict[str, str]:
    """Accept the exact six-field object or its saved successful prepare envelope.

    utf-8-sig also accepts a BOM written by Windows PowerShell. No path in the
    envelope selects a ledger; that remains an explicit launcher argument.
    """
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ReceiptInputError("receipt exceeds 1 MiB")
        envelope = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReceiptInputError("cannot read receipt: " + str(exc)) from exc
    if not isinstance(envelope, dict):
        raise ReceiptInputError("receipt must be a JSON object")
    if envelope.get("error") or envelope.get("command_succeeded") is False:
        raise ReceiptInputError("an error response is not a replacement receipt")
    receipt = envelope.get("receipt", envelope)
    if not isinstance(receipt, dict) or set(receipt) != set(FIELDS):
        raise ReceiptInputError("receipt requires exactly six fields: " + ", ".join(FIELDS))
    for key, value in receipt.items():
        if (not isinstance(value, str) or not value.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ReceiptInputError("invalid receipt field: " + key)
    differences = [
        {"field": key, "provided": envelope[key], "actual": receipt[key], "actual_source": "nested_receipt"}
        for key in FIELDS if key in envelope and envelope[key] != receipt[key]
    ]
    if differences:
        raise ReceiptInputError("receipt envelope conflicts with its nested receipt", differences)
    return dict(receipt)


def apply_file(args: argparse.Namespace) -> None:
    """Combine matching inputs; refuse *all* conflicts before opening a ledger."""
    path = getattr(args, "receipt", None)
    if path is None:
        return
    saved = load(path)
    conflicts = []
    for field, argument in zip(FIELDS, ARGUMENTS):
        explicit = getattr(args, argument, None)
        if explicit is not None and explicit != saved[field]:
            conflicts.append({"field": field, "provided": explicit, "actual": saved[field],
                              "actual_source": "receipt_file"})
    if conflicts:
        raise ReceiptInputError("receipt input conflict", conflicts)
    for field, argument in zip(FIELDS, ARGUMENTS):
        setattr(args, argument, saved[field])
    # A receipt file never allows implicit latest/sole-row selection.
    args.cross_workspace = True


def mismatches(record: dict, args: argparse.Namespace, ledger_id: str) -> list[dict]:
    actual = {**record, "ledger_id": ledger_id}
    differences = []
    for field, argument in zip(FIELDS, ARGUMENTS):
        if field == "task_id" and getattr(args, "receipt", None) is None:
            continue  # Legacy callers retain their existing identity-error path.
        provided = getattr(args, argument, None)
        if provided and provided != actual.get(field):
            difference = {"field": field, "provided": provided, "actual": actual.get(field),
                          "actual_source": "selected_ledger"}
            if field == "ledger_id":
                difference["identity_input"] = "absolute_resolved_ledger_json_file_path_not_directory"
            differences.append(difference)
    return differences
