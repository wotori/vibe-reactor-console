"""Explicit, bounded public mission summaries. Export never resumes a job."""

import json
from pathlib import Path

from . import __version__


def report_document(snapshot):
    from .playback import arena_document
    public = arena_document(snapshot, include_text=True, include_paths=False)
    return {"schema_version": 1, "format": "devos-mission-report", "devos_version": __version__,
            "origin": "recorded" if snapshot.mode == "replay" else snapshot.mode,
            "arena": public,
            "note": "Public results and recorded check evidence; export runs no checks or model jobs."}


def report_markdown(document):
    arena = document["arena"]
    lines = ["# DEV/OS mission report", "", "Origin: " + document["origin"],
             "State: " + str(arena.get("status", "unknown")),
             "Phase: " + str(arena.get("phase", "unknown")),
             "Verification: " + ("recorded checks passed" if arena.get("verified") else "not verified"),
             "", str(arena.get("reason", "")), "", "## Public results", ""]
    for card in arena.get("cards", ()):
        lines += ["### " + str(card.get("name", card.get("id", "agent"))), "",
                  "Role: " + str(card.get("role", "unknown")), "State: " + str(card.get("status", "unknown")),
                  "", str(card.get("summary", "")), ""]
    lines += ["## Independent check evidence", ""]
    for check in arena.get("validation_reports", ()):
        lines += ["- State: {} / exit: {} / artifact: {}".format(check.get("status", "unknown"),
                    check.get("exit_code", "unknown"), check.get("artifact_hash", "unknown"))]
    if not arena.get("validation_reports"):
        lines += ["No independent check report was recorded."]
    lines += ["", "## Artifacts", ""]
    for artifact in arena.get("artifacts", ()):
        lines += ["- {} / {} / SHA256 {}".format(artifact.get("kind", "unknown"),
                    artifact.get("id", "unknown"), artifact.get("sha256", "unknown"))]
    lines += ["", "## Remaining action", "", "Inspect retained workspace/candidate artifacts and resolve outstanding findings.",
              "The export does not merge, deploy, resume models or certify a later integration.", ""]
    return "\n".join(lines)


def export_mission_report(directory, snapshot):
    """Create a new local report directory; existing exports are never overwritten."""
    document = report_document(snapshot)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Public mission report exceeds 2 MiB")
    directory = Path(directory).expanduser()
    directory.mkdir(mode=0o700, exist_ok=False)
    (directory / "report.json").write_text(encoded, encoding="utf-8")
    (directory / "report.md").write_text(report_markdown(document), encoding="utf-8")
    return directory
