"""
Orchestrates running a slate of :class:`Check` functions and turning
them into a list of :class:`CheckResult` objects.

The runner is deliberately simple — it iterates the registry,
filters by platform, calls each check with the shared kwargs, and
yields results one by one. A caller that wants progressive UI (the
PyQt6 wizard) can consume the generator; a caller that wants the
full list (the CLI) can ``list(...)`` it.

No Qt, no subprocess orchestration, no threading. That's the caller's
job. Keeps the runner testable in under 50 lines.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Iterator, Optional

from .checks import CHECKS, Check, CheckResult, CheckStatus


def _current_platform() -> str:
    """Return 'linux' / 'windows' / 'other'."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return "other"


@dataclass
class PreflightRunner:
    """Iterate the preflight registry, passing shared kwargs to each check.

    Parameters
    ----------
    interface
        Optional network interface name for interface-dependent checks.
    stop_on_fail
        If True, stop yielding after the first FAIL (useful for CLI
        fail-fast mode). Default False so the operator always sees
        the full sweep.
    include_categories
        Whitelist of categories — None means all.
    """
    interface: Optional[str] = None
    stop_on_fail: bool = False
    include_categories: Optional[frozenset[str]] = None

    def iter_results(self) -> Iterator[CheckResult]:
        platform = _current_platform()
        for check in CHECKS:
            if (self.include_categories is not None
                    and check.category not in self.include_categories):
                continue
            if platform not in check.platforms:
                yield self._skip_for_platform(check, platform)
                continue
            result = check.fn(interface=self.interface)
            yield result
            if self.stop_on_fail and result.status == CheckStatus.FAIL:
                return

    def run_all(self) -> list[CheckResult]:
        return list(self.iter_results())

    # ---- helpers ----------------------------------------------------

    @staticmethod
    def _skip_for_platform(check: Check, platform: str) -> CheckResult:
        return CheckResult(
            name=check.name,
            status=CheckStatus.SKIP,
            observed=f"not applicable on {platform}",
            expected=f"runs on {sorted(check.platforms)}",
            platforms=check.platforms,
        )


# --- rendering helpers (shared by CLI + GUI summary) ------------------


def format_markdown(results: list[CheckResult]) -> str:
    """Render a markdown summary table + per-check details."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1

    lines: list[str] = []
    lines.append("# HotWire preflight report")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for st in ("PASS", "FAIL", "WARN", "SKIP"):
        lines.append(f"| {st} | {counts.get(st, 0)} |")
    lines.append("")
    lines.append("| Check | Status | Observed | Expected |")
    lines.append("|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.name} | {r.status.symbol} | "
            f"{r.observed or '—'} | {r.expected or '—'} |"
        )
    lines.append("")
    fails = [r for r in results
             if r.status in (CheckStatus.FAIL, CheckStatus.WARN)]
    if fails:
        lines.append("## Remediation")
        lines.append("")
        for r in fails:
            if not r.remediation:
                continue
            lines.append(f"### {r.name} — {r.status.symbol}")
            lines.append("")
            lines.append("```")
            lines.append(r.remediation)
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def format_text(results: list[CheckResult]) -> str:
    """Plain-text table for terminal output."""
    lines: list[str] = []
    name_w = max((len(r.name) for r in results), default=20)
    for r in results:
        lines.append(
            f"{r.status.symbol:7s} {r.name:<{name_w}s}  "
            f"{r.observed}"
        )
    passes = sum(1 for r in results if r.status == CheckStatus.PASS)
    fails = sum(1 for r in results if r.status == CheckStatus.FAIL)
    warns = sum(1 for r in results if r.status == CheckStatus.WARN)
    skips = sum(1 for r in results if r.status == CheckStatus.SKIP)
    lines.append("")
    lines.append(
        f"Summary: {passes} pass, {fails} fail, {warns} warn, {skips} skip "
        f"({len(results)} total)"
    )
    return "\n".join(lines)
