"""
Shared infrastructure for the hardware-validation phases.

Each ``phaseN_*.py`` script performs one check against real (or
simulated) EV-charging hardware and emits three things:

1. structured events into ``runs/<ts>/session.jsonl`` via :class:`EventLog`
2. raw Ethernet frames via :class:`PacketCapture` (one pcap per phase)
3. a human-readable summary block into ``REPORT.md`` via :class:`MarkdownReport`

The orchestrator (:mod:`run_all`) constructs a single :class:`RunContext`
that each phase receives; running a phase standalone is also supported —
just call ``RunContext.create_standalone()`` and it will carve out its
own ``runs/<ts>/`` directory.

Design notes:

* We deliberately avoid pypcap for recording. ``tcpdump`` / ``dumpcap``
  are much easier to invoke as a subprocess, survive the Python process
  dying, and always produce a valid pcap. Fall back cleanly when the
  tool isn't installed — the phase still runs, just without a pcap.
* JSONL events are flushed after every write so a crashed phase still
  leaves a readable trail.
* Phases never call ``sys.exit``; they return a :class:`PhaseResult`
  that the orchestrator aggregates. An exception becomes a FAIL with
  the traceback captured in the event log.

Everything here is stdlib — no third-party dependencies — because the
hardware host is typically a Raspberry Pi where we don't want to pip
install anything more than strictly necessary.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import traceback
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterable, Optional


# --- Result enum -------------------------------------------------------


class Status(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclasses.dataclass
class PhaseResult:
    """One phase's outcome. ``details`` is free-form prose; ``metrics``
    is a dict of small numeric/scalar key-values appended to the
    report."""
    name: str
    status: Status
    summary: str = ""
    details: str = ""
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)
    artifacts: list[Path] = dataclasses.field(default_factory=list)
    duration_s: float = 0.0

    @property
    def symbol(self) -> str:
        return {
            Status.PASS: "[PASS]",
            Status.FAIL: "[FAIL]",
            Status.SKIP: "[SKIP]",
            Status.ERROR: "[ERROR]",
        }[self.status]


# --- Structured event log ---------------------------------------------


class EventLog:
    """Thread-safe JSONL writer.

    Events are single-line JSON objects with at minimum a timestamp
    and phase tag. Writers call ``log.event(kind="...", **fields)``
    and everything serialisable ends up on disk.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8", buffering=1)

    def event(self, kind: str, **fields: Any) -> None:
        rec = {
            "ts": _iso_now(),
            "kind": kind,
            **fields,
        }
        line = json.dumps(rec, default=_json_default, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, Status):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return str(obj)


def _iso_now() -> str:
    # Timezone-aware ISO-8601 with microsecond precision.
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


# --- Packet capture ----------------------------------------------------


class PacketCapture:
    """Wrap ``tcpdump`` / ``dumpcap`` so a phase can capture its own
    frames to ``runs/<ts>/phaseN_capture.pcap``.

    Usage::

        with PacketCapture(ctx, phase="phase2") as cap:
            ...do work that produces traffic...
        # on exit: tcpdump is SIGINT'd and the file closed.

    If no capture tool is found on PATH the context manager still
    returns a working object — its ``pcap_path`` attribute is None
    and ``available`` is False. The phase can decide whether that's
    fatal or a soft warning.
    """

    # Commands we try in order. First one found wins.
    _CANDIDATES = [
        # (binary_name, args_factory). args_factory returns argv given
        # the output path, interface, and BPF filter.
        ("dumpcap", lambda out, iface, flt: [
            "dumpcap", "-i", iface, "-q", "-w", str(out),
            *( ["-f", flt] if flt else [] ),
        ]),
        ("tcpdump", lambda out, iface, flt: [
            "tcpdump", "-i", iface, "-w", str(out), "-U",
            *( [flt] if flt else [] ),
        ]),
    ]

    def __init__(
        self,
        ctx: "RunContext",
        phase: str,
        interface: Optional[str] = None,
        bpf: Optional[str] = None,
    ) -> None:
        self.ctx = ctx
        self.phase = phase
        self.interface = interface or ctx.interface
        self.bpf = bpf
        self.pcap_path: Optional[Path] = None
        self.available = False
        self._proc: Optional[subprocess.Popen] = None
        self._tool: Optional[str] = None

    # ---- context manager --------------------------------------------

    def __enter__(self) -> "PacketCapture":
        if not self.interface:
            self.ctx.log.event(
                kind="pcap.skip", phase=self.phase,
                reason="no interface configured",
            )
            return self
        tool = self._find_tool()
        if tool is None:
            self.ctx.log.event(
                kind="pcap.skip", phase=self.phase,
                reason="no tcpdump/dumpcap on PATH",
            )
            return self
        self._tool = tool[0]
        out = self.ctx.run_dir / f"{self.phase}_capture.pcap"
        argv = tool[1](out, self.interface, self.bpf)
        self.ctx.log.event(
            kind="pcap.start", phase=self.phase,
            tool=self._tool, argv=argv, output=str(out),
        )
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.ctx.log.event(
                kind="pcap.skip", phase=self.phase,
                reason=f"{self._tool} not executable",
            )
            return self
        self.pcap_path = out
        self.available = True
        # Give the sniffer a moment to actually open the interface before
        # the phase starts producing traffic.
        time.sleep(0.3)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._proc is None:
            return
        try:
            self._proc.send_signal(signal.SIGINT)
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        stderr = (self._proc.stderr.read() if self._proc.stderr else b"")
        self.ctx.log.event(
            kind="pcap.stop", phase=self.phase,
            returncode=self._proc.returncode,
            stderr_tail=stderr.decode(errors="replace")[-400:],
            pcap_path=str(self.pcap_path) if self.pcap_path else None,
            pcap_size=(self.pcap_path.stat().st_size
                       if self.pcap_path and self.pcap_path.exists() else 0),
        )

    # ---- helpers ----------------------------------------------------

    @staticmethod
    def _find_tool():
        for name, factory in PacketCapture._CANDIDATES:
            if shutil.which(name):
                return name, factory
        return None


# --- Report writer -----------------------------------------------------


class MarkdownReport:
    """Accumulate phase results and render to ``REPORT.md`` on close.

    We render incrementally (after each phase) so an interrupted run
    still leaves a partial report behind.
    """

    def __init__(self, ctx: "RunContext") -> None:
        self.ctx = ctx
        self._phases: list[PhaseResult] = []
        self.path = ctx.run_dir / "REPORT.md"

    def add(self, result: PhaseResult) -> None:
        self._phases.append(result)
        self._render()

    def _render(self) -> None:
        hdr = self.ctx.header_dict()
        lines: list[str] = []
        lines.append(f"# HotWire hardware check — {hdr['started_at']}")
        lines.append("")
        lines.append("## Environment")
        lines.append("")
        for k, v in hdr.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")
        lines.append("## Phase summary")
        lines.append("")
        lines.append("| Phase | Status | Duration | Summary |")
        lines.append("|---|---|---|---|")
        for ph in self._phases:
            dur = f"{ph.duration_s:.2f}s"
            # Escape pipe chars in summary
            s = (ph.summary or "").replace("|", "\\|")
            lines.append(f"| {ph.name} | {ph.symbol} | {dur} | {s} |")
        lines.append("")
        for ph in self._phases:
            lines.append(f"## {ph.name} — {ph.symbol}")
            lines.append("")
            if ph.summary:
                lines.append(ph.summary)
                lines.append("")
            if ph.metrics:
                lines.append("**Metrics:**")
                lines.append("")
                for k, v in ph.metrics.items():
                    lines.append(f"- `{k}` = {v}")
                lines.append("")
            if ph.details:
                lines.append("**Details:**")
                lines.append("")
                lines.append("```")
                lines.append(ph.details.rstrip())
                lines.append("```")
                lines.append("")
            if ph.artifacts:
                lines.append("**Artifacts:**")
                lines.append("")
                for a in ph.artifacts:
                    rel = os.path.relpath(a, self.ctx.run_dir.parent)
                    lines.append(f"- [`{rel}`]({rel.replace(os.sep, '/')})")
                lines.append("")
        self.path.write_text("\n".join(lines), encoding="utf-8")


# --- RunContext --------------------------------------------------------


@dataclasses.dataclass
class RunContext:
    """Everything a phase needs to log, capture, and decide how to run."""
    run_dir: Path
    log: EventLog
    report: MarkdownReport
    interface: Optional[str]
    config: dict[str, Any]
    started_at: str = dataclasses.field(default_factory=_iso_now)

    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        run_root: Path,
        interface: Optional[str] = None,
        extra_config: Optional[dict[str, Any]] = None,
    ) -> "RunContext":
        run_root.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = run_root / ts
        # If two runs collide on the same second, suffix with an index.
        n = 1
        while run_dir.exists():
            run_dir = run_root / f"{ts}-{n}"
            n += 1
        run_dir.mkdir(parents=True, exist_ok=False)
        log = EventLog(run_dir / "session.jsonl")
        cfg = {
            "interface": interface,
            "host_os": os.name,
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
            **(extra_config or {}),
        }
        (run_dir / "config.json").write_text(
            json.dumps(cfg, indent=2, default=_json_default),
            encoding="utf-8",
        )
        ctx = cls(
            run_dir=run_dir,
            log=log,
            report=None,  # type: ignore[arg-type]
            interface=interface,
            config=cfg,
        )
        ctx.report = MarkdownReport(ctx)
        log.event(kind="run.start", run_dir=str(run_dir), **cfg)
        return ctx

    @classmethod
    def create_standalone(
        cls, interface: Optional[str] = None,
    ) -> "RunContext":
        """Used when a phase is run as ``__main__`` on its own."""
        root = _default_run_root()
        return cls.create(root, interface=interface)

    def header_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, **self.config}

    def close(self) -> None:
        self.log.event(kind="run.end")
        self.log.close()


def _default_run_root() -> Path:
    # Anchor next to the repo root so artefacts don't scatter into $HOME.
    # scripts/hw_check/_runner.py → repo root is three levels up.
    return Path(__file__).resolve().parent.parent.parent / "runs"


# --- Phase execution helper --------------------------------------------


def run_phase(
    ctx: RunContext,
    name: str,
    func,
    *args,
    **kwargs,
) -> PhaseResult:
    """Wrap a phase callable so exceptions become FAIL with traceback.

    ``func`` must accept ``ctx`` as its first positional and return a
    :class:`PhaseResult`.
    """
    t0 = time.monotonic()
    ctx.log.event(kind="phase.start", phase=name)
    try:
        result: PhaseResult = func(ctx, *args, **kwargs)
    except Exception as e:                                       # noqa: BLE001
        tb = traceback.format_exc()
        ctx.log.event(
            kind="phase.error", phase=name,
            exc_type=type(e).__name__, message=str(e), traceback=tb,
        )
        result = PhaseResult(
            name=name,
            status=Status.ERROR,
            summary=f"unhandled exception: {e}",
            details=tb,
        )
    if result.name != name:
        result = dataclasses.replace(result, name=name)
    result.duration_s = time.monotonic() - t0
    ctx.log.event(
        kind="phase.end", phase=name, status=result.status.value,
        summary=result.summary, metrics=result.metrics,
        duration_s=result.duration_s,
    )
    ctx.report.add(result)
    return result


# --- CLI helpers -------------------------------------------------------


def print_banner(title: str) -> None:
    bar = "=" * min(72, max(20, len(title) + 4))
    print(bar)
    print(f"  {title}")
    print(bar)


def print_result(result: PhaseResult) -> None:
    print(f"{result.symbol} {result.name}: {result.summary}")
    if result.metrics:
        for k, v in result.metrics.items():
            print(f"      {k} = {v}")
    if result.details:
        # Indent details two spaces.
        for line in result.details.rstrip().splitlines():
            print("      " + line)
