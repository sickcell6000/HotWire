"""
Per-field preset storage for the GUI's attack launcher and override editor.

The operator runs a fixed set of attack scenarios (replaying captured
EVCCIDs from specific test vehicles, dialling in voltage targets per
battery model, etc.) over and over again. Re-typing values from a
notebook every time is error-prone — the difference between
``d83add22f182`` and ``d83add22f169`` is one Autocharge session billed
to the wrong account. Presets fix this: pick the named entry, get the
exact value plus the note that says where it came from.

Storage
-------
Presets live in ``<repo>/config/attack_presets.json`` as a flat list
of entries. The schema is intentionally simple so reviewers can hand-
edit the file without booting the GUI:

.. code-block:: json

    {
      "version": 1,
      "entries": [
        {
          "scope": "AutochargeImpersonation.evccid",
          "value": "d83add22f182",
          "label": "Pi PEV self-MAC",
          "note": "Sim/dev only — never replay against a live station.",
          "created": "2026-04-30T00:30:00"
        },
        {
          "scope": "ForcedDischarge.voltage",
          "value": 400,
          "label": "Luxgen N7 (60 kWh, ~400 V)",
          "note": "Paper Table 4 - vulnerable BMS",
          "created": "..."
        }
      ]
    }

The ``scope`` key follows the convention ``<context>.<field-name>``,
where context is either an attack class name
(``AutochargeImpersonation``, ``ForcedDischarge``) or a stage name
(``SessionSetupReq``, ``PreChargeRes``). Field names match the
dataclass field / schema entry exactly.

The file is *user-managed*: the GUI reads/writes it but never
auto-fetches new presets from elsewhere. Default seed entries are
shipped in the repo so a fresh clone has paper-relevant examples to
start from.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PRESETS_PATH = _REPO_ROOT / "config" / "attack_presets.json"

# Override via env var so tests can point at a temp file without polluting
# the operator's real preset library.
_PRESETS_PATH_ENV = "HOTWIRE_ATTACK_PRESETS"


@dataclass(frozen=True)
class Preset:
    """One preset entry — immutable on purpose, replace via the store."""

    scope: str          # e.g. "AutochargeImpersonation.evccid"
    value: Any          # actual value (str/int/float/bool depending on field)
    label: str          # short human-readable name
    note: str = ""      # longer free-form note (where captured, target vehicle, …)
    created: str = ""   # ISO-8601 timestamp; auto-filled on save

    def display(self) -> str:
        """How this preset shows up in a QComboBox dropdown."""
        head = self.label or str(self.value)
        if self.note:
            return f"{head} — {self.value!s}"
        return f"{head} — {self.value!s}"


def _resolve_path() -> Path:
    override = os.environ.get(_PRESETS_PATH_ENV)
    return Path(override) if override else _PRESETS_PATH


class PresetStore:
    """Thread-safe loader/saver for the preset JSON file.

    Held as a module-level singleton (see :func:`get_store`) so the
    attack launcher and the per-stage override editor share one
    in-memory copy + persist to the same file.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _resolve_path()
        self._lock = Lock()
        self._entries: list[Preset] = []
        self._loaded = False

    # ---- IO --------------------------------------------------------

    def load(self) -> None:
        """Read presets from disk. Safe to call repeatedly."""
        with self._lock:
            if not self._path.is_file():
                self._entries = []
                self._loaded = True
                return
            try:
                blob = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._entries = []
                self._loaded = True
                return
            raw_entries = blob.get("entries", []) if isinstance(blob, dict) else []
            parsed: list[Preset] = []
            for raw in raw_entries:
                if not isinstance(raw, dict):
                    continue
                scope = raw.get("scope")
                if not scope or "value" not in raw:
                    continue
                parsed.append(
                    Preset(
                        scope=str(scope),
                        value=raw["value"],
                        label=str(raw.get("label", "")),
                        note=str(raw.get("note", "")),
                        created=str(raw.get("created", "")),
                    )
                )
            self._entries = parsed
            self._loaded = True

    def save(self) -> None:
        """Persist current presets back to disk."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "entries": [asdict(e) for e in self._entries],
            }
            self._path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    # ---- Query -----------------------------------------------------

    def for_scope(self, scope: str) -> list[Preset]:
        if not self._loaded:
            self.load()
        with self._lock:
            return [e for e in self._entries if e.scope == scope]

    def all_entries(self) -> list[Preset]:
        if not self._loaded:
            self.load()
        with self._lock:
            return list(self._entries)

    # ---- Mutation --------------------------------------------------

    def add(self, scope: str, value: Any, label: str, note: str = "") -> Preset:
        """Add a new preset and persist immediately. Returns the entry."""
        if not self._loaded:
            self.load()
        entry = Preset(
            scope=scope,
            value=value,
            label=label or str(value),
            note=note,
            created=_dt.datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._entries.append(entry)
        self.save()
        return entry

    def remove(self, scope: str, value: Any, label: str) -> bool:
        """Remove the matching preset. Returns True if found."""
        if not self._loaded:
            self.load()
        with self._lock:
            for i, e in enumerate(self._entries):
                if (e.scope == scope and e.value == value
                        and e.label == label):
                    del self._entries[i]
                    self.save()
                    return True
        return False


_singleton: Optional[PresetStore] = None
_singleton_lock = Lock()


def get_store() -> PresetStore:
    """Process-wide :class:`PresetStore` singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PresetStore()
            _singleton.load()
        return _singleton


def reset_store_for_tests() -> None:
    """Force the next ``get_store()`` call to rebuild from the env-var
    path. Used by pytest fixtures pointing at a temp file."""
    global _singleton
    with _singleton_lock:
        _singleton = None
