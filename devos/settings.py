"""Local startup state, kept separate from portable presets and recordings.

Only an explicitly selected local settings file may remember a project folder.
``presets.validate`` continues to strip that access from imported scene files.
"""

import json
from pathlib import Path
import time

from . import presets


LOCAL_FORMAT = "devos-local-settings"
MAX_SETTINGS_BYTES = 65536


def validate(document, base=None):
    """Validate local preferences without opening the selected project folder."""
    result = presets.validate(document)
    # A scene capsule remains portable even if somebody added a local path to it.
    if document.get("format") not in (None, LOCAL_FORMAT):
        return result
    if "project_root" in document:
        value = document["project_root"]
        if value is not None:
            if (not isinstance(value, str) or not value.strip()
                    or len(value.encode("utf-8")) > 4096 or any(ord(char) < 32 for char in value)):
                raise ValueError("Local project_root must be a directory path or null")
            path = Path(value).expanduser()
            if not path.is_absolute() and base is not None:
                path = Path(base) / path
            value = str(path.resolve())
        result["project_root"] = value
    return result


def load(path):
    path = Path(path).expanduser()
    if path.stat().st_size > MAX_SETTINGS_BYTES:
        raise ValueError("Settings exceed 64 KiB")
    return validate(json.loads(path.read_text(encoding="utf-8")), base=path.parent)


def local_preferences(session):
    """Snapshot durable controls only; no clocks, commands, secrets or live jobs."""
    result = presets.preferences(session)
    root = getattr(session.sources, "project_root", None)
    result["project_root"] = str(root) if root is not None else None
    return result


def save(path, document):
    safe = dict(validate(document), schema_version=1, format=LOCAL_FORMAT)
    return presets._atomic_save(path, safe)


class SettingsStore:
    """Debounce changed preferences and flush the last edit at graceful shutdown.

    The app owns error reporting. Failed writes are retried after a short backoff;
    neither failures nor repeated unchanged frames cause filesystem writes.
    """

    def __init__(self, path, enabled=True, debounce=.75, clock=time.monotonic):
        self.path = Path(path).expanduser()
        self.enabled = enabled
        self.debounce = max(0, float(debounce))
        self.clock = clock
        self._pending = self._saved = None
        self._changed_at = self._retry_at = 0.

    def update(self, session, force=False):
        if not self.enabled:
            return None
        document = local_preferences(session)
        signature = json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False)
        now = self.clock()
        if signature != self._pending:
            self._pending, self._changed_at = signature, now
        if signature == self._saved:
            return None
        if not force and (now - self._changed_at < self.debounce or now < self._retry_at):
            return None
        try:
            path = save(self.path, document)
        except (OSError, ValueError, TypeError):
            self._retry_at = now + 5
            raise
        self._saved, self._retry_at = signature, 0.
        return path

    def close(self, session):
        return self.update(session, force=True)
