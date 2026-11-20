"""Read-only checklist projection from an independently verified saved snapshot."""

import csv
import io
import json

VERSION = "financial-month-end-checklist-csv-v1"
HEADERS = (
    "export_schema",
    "snapshot_id",
    "snapshot_payload_hash",
    "source_hash",
    "filename",
    "target_month",
    "policy_id",
    "policy_version",
    "language",
    "preparation_readiness",
    "accounting_close_attested",
    "item_id",
    "category",
    "title",
    "status",
    "reason_code",
    "evidence_json",
)


def text_cell(value):
    """Quote spreadsheet formula prefixes, including hidden leading whitespace."""
    value = str(value)
    probe = value
    while probe and (
        probe[0].isspace() or probe[0] in "\ufeff\u200b\u200c\u200d\u2060"
    ):
        probe = probe[1:]
    if value.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def checklist_csv(snapshot):
    """Call only after MonthlySnapshots.read verifies authorization and replay.

    Canonical headers/status/reason codes and original localized titles are retained.
    The JSON snapshot remains the complete evidence artifact, including calculations.
    """
    result = snapshot["result"]
    policy = result["policy"]
    common = (
        VERSION,
        snapshot["snapshot_id"],
        snapshot["payload_hash"],
        snapshot["source_hash"],
        snapshot["filename"],
        snapshot["close_choices"]["target_month"],
        policy["policy_id"],
        policy["version"],
        result["language"],
        result["preparation_readiness"],
        "false",
    )
    output = io.StringIO(newline="")
    writer = csv.writer(
        output, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n"
    )
    writer.writerow(HEADERS)
    for item in result["checklist"]:
        writer.writerow(
            text_cell(v)
            for v in (
                *common,
                item["item_id"],
                item["category"],
                item["title"],
                item["status"],
                item["reason_code"],
                json.dumps(
                    item["evidence"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return output.getvalue().encode("utf-8-sig")
