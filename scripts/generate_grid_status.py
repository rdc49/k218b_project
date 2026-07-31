#!/usr/bin/env python3
"""Generate a live HTML status report of every currently-running grid-sweep case.

For every `proteus start` process actually alive right now on the cluster
(checked directly over SSH, not inferred from logs), report how long it's
been running and where it sits in the fO2 x H x C x N x S grid. Produces a
self-contained HTML file (sortable/filterable table, no server needed) plus
a short plain-text summary on stdout.

This does NOT publish anything to claude.ai. It only writes a local HTML
file. Turning that file into a shareable Artifact link (or updating a
previously-published one) is a separate step done from within a Claude Code
session via the `Artifact` tool -- a bare script has no access to that.

Two categories of running case
-------------------------------------------------------------------------
- "main" grid cases: launched from grid_sweep_configs/batch_configs/batchNN.toml
  via `proteus grid`. Resolved back to a batch label (batch03, etc.) via
  harvest_completed_cases.discover_batches(), then their actual H/C/N/S/fO2
  values are read from their own launch config
  (<batch output dir>/cfgs/case_NNNNNN.toml) and matched against
  grid_sweep_configs/full_parameter_sweep.toml's axis lists via
  harvest_completed_cases.match_index() to get the 0-3 grid index per axis.
- "gapfill" cases: single-point recovery runs from
  scripts/generate_gapfill_configs.py, living in
  PROTEUS/output/batch_gapfill_H<i>_C<i>_N<i>_S<i>_fO2<i>/. Their grid indices
  are already in the directory name -- no config read or axis-matching
  needed for those.

Only SSH is used for the liveness/runtime check itself (`ps -eo etime` on
each of the 14 known cluster machines) -- the actual TOML config reads are
local filesystem reads, since /data/rdc49-2/PROTEUS/output/ is on a shared
NFS mount visible from this host too, not something that needs a remote
`cat`/`grep` round-trip.

Usage:
    python3 scripts/generate_grid_status.py
    python3 scripts/generate_grid_status.py --output /tmp/status.html --json /tmp/status.json
    python3 scripts/generate_grid_status.py --machines cap001c,cap001d
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import harvest_completed_cases as hc  # noqa: E402  (needs sys.path set up first)

PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SCRIPT_DIR / "grid_status_template.html"
DEFAULT_OUTPUT = PROJECT_ROOT / "grid_status_report.html"

# batch_gapfill_H<i>_C<i>_N<i>_S<i>_fO2<i>, per
# scripts/generate_gapfill_configs.py's naming convention.
GAPFILL_RE = re.compile(r"^batch_gapfill_H(\d)_C(\d)_N(\d)_S(\d)_fO2(\d)$")

# .../output/<name>/cfgs/case_NNNNNN.toml, PROTEUS's own launch-config path
# convention (same one harvest_completed_cases.py's aliveness check matches
# against).
CONFIG_PATH_RE = re.compile(r"/([^/]+)/cfgs/(case_\d+)\.toml")


def query_live_cases(
    machines: tuple[str, ...], ssh_timeout: float
) -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """SSH into every machine once; return [(machine, etimes, etime, config_path), ...]
    for every live `proteus start` process, plus the list of unreachable machines.

    Uses `ps -eo pid,etimes,etime,args` rather than harvest_completed_cases's
    bare `pgrep -af` since runtime (`etimes`, seconds; `etime`, formatted) is
    needed here and wasn't there.
    """
    rows: list[tuple[str, int, str, str]] = []
    unreachable: list[str] = []
    for machine in machines:
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o", f"ConnectTimeout={ssh_timeout}",
                    "-o", "BatchMode=yes",
                    machine,
                    "ps -eo pid,etimes,etime,args --sort=-etimes | grep 'proteus start' | grep -v grep",
                ],
                capture_output=True,
                text=True,
                timeout=ssh_timeout + 5,
            )
        except subprocess.TimeoutExpired:
            unreachable.append(machine)
            continue
        if result.returncode == 255:
            unreachable.append(machine)
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            _pid, etimes, etime, cmd = parts
            m = CONFIG_PATH_RE.search(cmd)
            if not m:
                continue
            rows.append((machine, int(etimes), etime, cmd))
    return rows, unreachable


def build_rows(
    live: list[tuple[str, int, str, str]],
    full_axes: dict[str, list[float]],
    output_to_batch: dict[str, str],
    proteus_output: Path,
) -> list[dict]:
    rows: list[dict] = []
    for machine, etimes, etime, cmd in live:
        m = CONFIG_PATH_RE.search(cmd)
        assert m  # already filtered in query_live_cases
        output_name, case = m.group(1), m.group(2)

        gm = GAPFILL_RE.match(output_name)
        if gm:
            hi, ci, ni, si, foi = (int(g) for g in gm.groups())
            rows.append(dict(
                group="gapfill", batch="gapfill", machine=machine, case=case,
                etimes=etimes, etime=etime,
                hi=hi, ci=ci, ni=ni, si=si, foi=foi,
                H=full_axes["H"][hi], C=full_axes["C"][ci],
                N=full_axes["N"][ni], S=full_axes["S"][si], fO2=full_axes["fO2"][foi],
            ))
            continue

        batch_label = output_to_batch.get(output_name)
        if batch_label is None:
            print(
                f"warning: live process on {machine} has unrecognized output dir "
                f"{output_name!r} (not a known batch, not a gapfill dir) -- skipping: {cmd}",
                file=sys.stderr,
            )
            continue

        case_cfg_path = proteus_output / output_name / "cfgs" / f"{case}.toml"
        if not case_cfg_path.is_file():
            print(
                f"warning: {case_cfg_path} not found (local NFS read) for live process on "
                f"{machine} -- skipping",
                file=sys.stderr,
            )
            continue
        try:
            cfg = hc.load_toml(case_cfg_path)
            params = {
                "H": float(cfg["planet"]["elements"]["H_budget"]),
                "C": float(cfg["planet"]["elements"]["C_budget"]),
                "N": float(cfg["planet"]["elements"]["N_budget"]),
                "S": float(cfg["planet"]["elements"]["S_budget"]),
                "fO2": float(cfg["outgas"]["fO2_shift_IW"]),
            }
        except (KeyError, OSError) as exc:
            print(f"warning: could not read params from {case_cfg_path}: {exc} -- skipping", file=sys.stderr)
            continue

        indices = {axis: hc.match_index(value, full_axes[axis]) for axis, value in params.items()}
        if any(idx is None for idx in indices.values()):
            print(
                f"warning: {case_cfg_path} params {params} don't match any full-grid axis "
                "value within tolerance -- skipping",
                file=sys.stderr,
            )
            continue

        rows.append(dict(
            group="main", batch=batch_label, machine=machine, case=case,
            etimes=etimes, etime=etime,
            hi=indices["H"], ci=indices["C"], ni=indices["N"], si=indices["S"], foi=indices["fO2"],
            H=params["H"], C=params["C"], N=params["N"], S=params["S"], fO2=params["fO2"],
        ))

    rows.sort(key=lambda r: -r["etimes"])
    return rows


def fmt_runtime(etimes: int) -> str:
    d, rem = divmod(etimes, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def print_summary(rows: list[dict], all_batches: list[str], unreachable: list[str]) -> None:
    from collections import Counter

    print("=" * 70)
    print("Live grid status (real-time SSH process query, not the harvest log)")
    print("=" * 70)
    if unreachable:
        print(f"warning: could not reach {len(unreachable)} machine(s) via SSH: {', '.join(unreachable)}",
              file=sys.stderr)

    main_rows = [r for r in rows if r["group"] == "main"]
    gap_rows = [r for r in rows if r["group"] == "gapfill"]
    per_batch = Counter(r["batch"] for r in main_rows)
    per_machine = Counter(r["machine"] for r in rows)

    print(f"TOTAL running: {len(rows)}  ({len(main_rows)} main-grid, {len(gap_rows)} gapfill)")
    print(f"Machines active: {len(per_machine)}")
    for machine, count in sorted(per_machine.items()):
        print(f"  {machine}: {count} running")
    print("Main-grid batches:")
    for batch in sorted(per_batch):
        print(f"  {batch}: {per_batch[batch]} running")
    idle = sorted(set(all_batches) - set(per_batch))
    if idle:
        print(f"Batches with zero running cases right now: {', '.join(idle)}")
    if rows:
        longest = max(rows, key=lambda r: r["etimes"])
        print(f"Longest running: {longest['batch']}/{longest['case']} on {longest['machine']} "
              f"-- {fmt_runtime(longest['etimes'])}")
    print("=" * 70)


def render_html(
    template_path: Path, rows: list[dict], all_batches: list[str], as_of: str
) -> str:
    template = template_path.read_text()
    template = template.replace("__ROWS_JSON__", json.dumps(rows))
    template = template.replace("__ALL_BATCHES_JSON__", json.dumps(all_batches))
    template = template.replace("__AS_OF__", as_of)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--full-sweep", type=Path, default=hc.DEFAULT_FULL_SWEEP)
    parser.add_argument("--batch-configs", type=Path, default=hc.DEFAULT_BATCH_CONFIGS)
    parser.add_argument("--proteus-output", type=Path, default=hc.DEFAULT_PROTEUS_OUTPUT)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write the HTML report (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--json", type=Path, default=None,
                         help="Also dump the raw rows array as JSON to this path")
    parser.add_argument(
        "--machines",
        type=lambda s: tuple(m.strip() for m in s.split(",") if m.strip()),
        default=hc.DEFAULT_MACHINES,
        help=f"Comma-separated cluster machines to check (default: all {len(hc.DEFAULT_MACHINES)} known machines)",
    )
    parser.add_argument("--ssh-timeout", type=float, default=hc.DEFAULT_SSH_TIMEOUT)
    parser.add_argument("--summary-only", action="store_true",
                         help="Print the stdout summary but don't write the HTML report")
    args = parser.parse_args()

    full_axes = hc.load_full_grid_axes(args.full_sweep)
    batches = hc.discover_batches(args.batch_configs, args.proteus_output)
    all_batch_labels = [stem for stem, _output_name, _dir in batches]
    output_to_batch = {output_name: stem for stem, output_name, _dir in batches}

    live, unreachable = query_live_cases(args.machines, args.ssh_timeout)
    rows = build_rows(live, full_axes, output_to_batch, args.proteus_output)

    print_summary(rows, all_batch_labels, unreachable)

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
        print(f"wrote {len(rows)} rows to {args.json}")

    if not args.summary_only:
        as_of = datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
        html = render_html(args.template, rows, all_batch_labels, as_of)
        args.output.write_text(html)
        print(f"wrote HTML report ({len(rows)} rows) to {args.output}")
        print("note: this only wrote a local file -- publishing it as a shareable "
              "claude.ai Artifact link is a separate step done from within a Claude session.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
