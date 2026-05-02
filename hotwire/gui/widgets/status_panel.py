"""Live status readout: FSM state, EVCCID, SoC, voltages, currents.

The panel surfaces two kinds of state:

  1. **FSM/connection state** — pushed by the worker via the
     ``status_changed(key, value)`` signal. Examples: ``evseState``,
     ``pevState``, ``mode``, ``EVCCID``.

  2. **Per-message wire telemetry** — extracted directly from the
     decoded DIN 70121 messages flowing through the
     ``msg_decoded(direction, name, params)`` signal. Examples:
     ``DC_EVStatus.EVRESSSOC`` (SoC %), ``EVTargetVoltage``,
     ``EVTargetCurrent``, ``EVSEPresentCurrent``,
     ``ChargingComplete``, ``BulkChargingComplete``.

Pulling telemetry directly from msg_decoded means the StatusPanel
sees everything the FSM saw, without needing the FSM to push each
field individually. As soon as the wire carries a CurrentDemandReq,
the SoC% updates here.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


_STATE_COLORS = {
    "idle": "#607D8B",
    "running": "#4CAF50",
    "paused": "#FFC107",
    "stopped": "#F44336",
    "error": "#D32F2F",
}


# Telemetry fields extracted from msg_decoded payloads. Each entry is
# ``(label, [decoded-key, fallback-key, ...], formatter)``. The first
# matching key found in the params dict wins; the formatter takes the
# raw value (often a string from the OpenV2G JSON) plus the full params
# dict so it can fold in a unit / multiplier sibling, and returns the
# display string.
def _fmt_with_unit(unit_key: str, default_unit: str = ""):
    def _f(value: Any, params: dict[str, Any]) -> str:
        unit = params.get(unit_key, default_unit)
        return f"{value} {unit}".strip() if unit else f"{value}"
    return _f


def _fmt_with_voltage_unit(value: Any, params: dict[str, Any]) -> str:
    unit = params.get("EVSEPresentVoltage.Unit", "V")
    return f"{value} {unit}"


def _fmt_target_voltage(value: Any, params: dict[str, Any]) -> str:
    unit = params.get("EVTargetVoltage.Unit", "V")
    return f"{value} {unit}"


def _fmt_target_current(value: Any, params: dict[str, Any]) -> str:
    unit = params.get("EVTargetCurrent.Unit", "A")
    return f"{value} {unit}"


def _fmt_present_current(value: Any, params: dict[str, Any]) -> str:
    unit = params.get("EVSEPresentCurrent.Unit", "A")
    return f"{value} {unit}"


def _fmt_pct(value: Any, _params: dict[str, Any]) -> str:
    return f"{value} %"


def _fmt_bool(value: Any, _params: dict[str, Any]) -> str:
    return "Yes" if str(value) in ("1", "true", "True") else "No"


def _fmt_passthrough(value: Any, _params: dict[str, Any]) -> str:
    return str(value)


_TELEMETRY_FIELDS: tuple[tuple[str, tuple[str, ...], Any], ...] = (
    # SoC% comes from PEV side via DC_EVStatus.EVRESSSOC in every
    # PreChargeReq / CurrentDemandReq.
    ("SoC (%)", ("DC_EVStatus.EVRESSSOC", "EVRESSSOC"), _fmt_pct),
    # EVSE-side present voltage (real charger reports actual DC pin V).
    ("Present Voltage (V)",
     ("EVSEPresentVoltage.Value",), _fmt_with_voltage_unit),
    # EVSE-side present current.
    ("Present Current (A)",
     ("EVSEPresentCurrent.Value",), _fmt_present_current),
    # PEV-side requested target voltage (where the EV thinks it
    # wants to be).
    ("Target Voltage (V)",
     ("EVTargetVoltage.Value",), _fmt_target_voltage),
    # PEV-side requested target current.
    ("Target Current (A)",
     ("EVTargetCurrent.Value",), _fmt_target_current),
    # ReadyToChargeState from PowerDeliveryReq tells us whether EV
    # is actively charging or paused.
    ("EV Ready", ("DC_EVStatus.EVReady",), _fmt_bool),
    # EV side error code (NO_ERROR most of the time — non-zero is the
    # interesting evidence in attack scenarios).
    ("EV Error", ("DC_EVErrorCodeText", "DC_EVStatus.EVErrorCode"),
     _fmt_passthrough),
    # Charging complete flag from CurrentDemandReq — tells operator
    # the EV intends to terminate the session.
    ("EV Charging Complete",
     ("ChargingComplete",), _fmt_bool),
    ("EV Bulk Complete",
     ("BulkChargingComplete",), _fmt_bool),
    # EVSE-side advertised limits from ChargeParameterDiscoveryRes.
    ("EVSE Max Voltage (V)",
     ("EVSEMaximumVoltageLimit.Value",), _fmt_with_unit("EVSEMaximumVoltageLimit.Unit", "V")),
    ("EVSE Max Current (A)",
     ("EVSEMaximumCurrentLimit.Value",), _fmt_with_unit("EVSEMaximumCurrentLimit.Unit", "A")),
    ("EVSE Max Power (kW)",
     ("EVSEMaximumPowerLimit.Value",),
     _fmt_with_unit("EVSEMaximumPowerLimit.Unit", "W")),
)


class StatusPanel(QWidget):
    """Grid of key/value labels updated from two signals:

    * ``status_changed(key, value)`` — FSM-pushed scalar updates
      (``evseState`` / ``pevState`` / ``EVCCID`` / ``mode``).
    * ``msg_decoded(direction, name, params)`` — wire-level telemetry
      extracted per :data:`_TELEMETRY_FIELDS`.
    """

    # Pushed-from-FSM scalar fields (status_changed slot).
    _FSM_FIELDS: tuple[tuple[str, str], ...] = (
        ("evseState", "EVSE State"),
        ("pevState", "PEV State"),
        ("EVCCID", "EVCCID"),
        ("mode", "Mode"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fsm_labels: dict[str, QLabel] = {}
        self._telemetry_labels: dict[str, QLabel] = {}
        # Cumulative-energy tracker (kWh); integrated by sampling
        # P = V × I from CurrentDemandRes msgs. Reset on Clear trees.
        self._energy_wh: float = 0.0
        self._last_sample_t: float | None = None
        self._last_v: float | None = None
        self._last_i: float | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)

        # Primary state (colored, bigger font).
        primary_box = QGroupBox("Session State")
        primary_layout = QFormLayout(primary_box)
        self._primary_label = QLabel("Idle")
        primary_font = QFont()
        primary_font.setPointSize(11)
        primary_font.setBold(True)
        self._primary_label.setFont(primary_font)
        self._primary_label.setStyleSheet(f"color: {_STATE_COLORS['idle']};")
        primary_layout.addRow("Status:", self._primary_label)
        root.addWidget(primary_box)

        # FSM/connection-level fields (pushed by the worker).
        fsm_box = QGroupBox("Connection")
        fsm_layout = QFormLayout(fsm_box)
        for key, label in self._FSM_FIELDS:
            lbl = QLabel("N/A")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._fsm_labels[key] = lbl
            fsm_layout.addRow(label + ":", lbl)
        root.addWidget(fsm_box)

        # Live wire telemetry (pulled from msg_decoded).
        tele_box = QGroupBox("Live Parameters")
        tele_layout = QFormLayout(tele_box)
        for label, _keys, _fmt in _TELEMETRY_FIELDS:
            lbl = QLabel("N/A")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._telemetry_labels[label] = lbl
            tele_layout.addRow(label + ":", lbl)
        # Cumulative energy — integrates V × I from CurrentDemandRes.
        self._energy_label = QLabel("0.000 kWh")
        self._energy_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tele_layout.addRow("Energy (cumulative):", self._energy_label)
        root.addWidget(tele_box)
        root.addStretch(1)

        self.setFrameStyle = QFrame.NoFrame

    # ---- slot: status_changed ---------------------------------------

    def on_status(self, key: str, value: str) -> None:
        # Primary state tracks evseState or pevState directly.
        if key in ("evseState", "pevState"):
            self._primary_label.setText(value)
            color = self._color_for_state(value)
            self._primary_label.setStyleSheet(f"color: {color};")
        lbl = self._fsm_labels.get(key)
        if lbl is not None:
            lbl.setText(value)

    # ---- slot: msg_decoded ------------------------------------------

    def on_message(self, _direction: str, _msg_name: str, params: dict) -> None:
        """Update telemetry labels from the just-decoded message's params."""
        for label, keys, fmt in _TELEMETRY_FIELDS:
            value = self._first_present(params, keys)
            if value is None:
                continue
            lbl = self._telemetry_labels.get(label)
            if lbl is not None:
                lbl.setText(fmt(value, params))

        # Cumulative energy: integrate V_present × I_present whenever a
        # CurrentDemandRes / PreChargeRes carries both. Sampling the
        # protocol report (rather than reading a real ammeter) means the
        # number reflects what the EVSE *claims* to deliver — which is
        # exactly what the operator wants when they're using HotWire to
        # spoof voltage values: the Live Parameters panel will say
        # "Energy = X kWh" derived from our lying VxI claims, while the
        # bench multimeter at the resistive load tells the physical
        # truth. Useful side-by-side for paper figures.
        v_str = self._first_present(params, ("EVSEPresentVoltage.Value",))
        i_str = self._first_present(params, ("EVSEPresentCurrent.Value",))
        if v_str is not None and i_str is not None:
            try:
                v = float(v_str)
                i = float(i_str)
            except (TypeError, ValueError):
                return
            now = time.monotonic()
            if (self._last_sample_t is not None
                    and self._last_v is not None
                    and self._last_i is not None):
                dt_h = (now - self._last_sample_t) / 3600.0
                # Trapezoidal: average power across the interval.
                avg_power_w = ((v * i) + (self._last_v * self._last_i)) / 2.0
                self._energy_wh += avg_power_w * dt_h
                self._energy_label.setText(
                    f"{self._energy_wh / 1000.0:.3f} kWh"
                )
            self._last_sample_t = now
            self._last_v = v
            self._last_i = i

    def reset_energy(self) -> None:
        """Reset the cumulative-energy integrator. Hooked to the
        ``Clear trees`` button so the operator can start a fresh
        per-session count without restarting the worker."""
        self._energy_wh = 0.0
        self._last_sample_t = None
        self._last_v = None
        self._last_i = None
        self._energy_label.setText("0.000 kWh")

    # ---- helpers ----------------------------------------------------

    @staticmethod
    def _first_present(params: dict[str, Any], keys: tuple[str, ...]):
        for k in keys:
            if k in params and params[k] not in (None, ""):
                return params[k]
        return None

    def _color_for_state(self, value: str) -> str:
        v = value.lower()
        if "error" in v or "timeout" in v or "fail" in v:
            return _STATE_COLORS["error"]
        if "stopped" in v:
            return _STATE_COLORS["stopped"]
        if "pause" in v:
            return _STATE_COLORS["paused"]
        if "listen" in v or "idle" in v or v == "n/a":
            return _STATE_COLORS["idle"]
        return _STATE_COLORS["running"]
