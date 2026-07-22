#!/usr/bin/env python3
"""Analyse and summarise a `proteus grid` sweep output directory.

Usage:
    python output/analyze_grid_sweep.py <sweep_name> [--no-collect]

Implements the full playbook in output/GRID_SWEEP_ANALYSIS.md end to end for
a sweep directory at output/<sweep_name>/:

  1. Parses manager.log's "Flattened grid points" block to map each
     case_NNNNNN to its grid parameters (Section 1).
  2. Determines each case's REAL outcome from case_NNNNNN/proteus_00.log --
     never trusting the on-disk `status` file alone (Sections 2-4): looks for
     Traceback/CRITICAL, distinguishes a main-loop crash from a post-loop
     observe/spectrum crash, extracts the loop number at/near the failure
     from the log's own "Loop counters" blocks, checks real process liveness
     via `pgrep` plus log-mtime staleness for dead-with-no-traceback cases,
     and matches known recurring failure signatures (Section 4).
  3. Checks the grid manager's own health (manager.log crash trace vs a
     clean "All cases have exited" / "All processes finished" finish vs a
     stale manager.log with no live "proteus grid" process, i.e. externally
     killed).
  4. Flags any case where the on-disk `status` file disagrees with the
     log-derived outcome.
  5. Collects every case's plots (`.png`, `.pdf`, `.jpg`, `.jpeg`) and each
     case's proteus_00.log into output/<sweep_name>/collected_plots/, both
     flat (<plot_type>/<case>.<ext>) and sorted by outcome
     (by_status/<plot_type>/<category>/<case>.<ext>) -- absorbs what used to
     be the separate collect_grid_sweep_plots.py script, reusing the exact
     same per-case classification computed in step 2 so the table and the
     folder layout can never disagree with each other.
  6. Prints, and saves to output/<sweep_name>/summary_<YYYYMMDD_HHMMSS>.txt,
     a human-readable summary: headline success count, manager/job status,
     dominant failure mode(s), status-file disagreements, a per-case table
     (case, grid parameters, real outcome), a per-parameter outcome
     breakdown, and the collected-plots location.

Safe to re-run against an in-progress sweep: it just re-derives every case's
category from scratch and overwrites the previous plot copies; a fresh,
independently timestamped summary file is written on every run.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

IMAGE_EXTENSIONS = {'.png', '.pdf', '.jpg', '.jpeg'}
SKIP_DIR_NAMES = {'data', 'cfgs'}
COLLECTED_DIRNAME = 'collected_plots'
LOG_PLOT_TYPE = 'proteus_00_log'
CASE_LOG_NAME = 'proteus_00.log'
STATUS_NAME = 'status'
MANAGER_LOG_NAME = 'manager.log'

# manager.log / a case's proteus_00.log not touched in this long, with no
# matching live process found via pgrep -> treat it as no longer running.
# `pgrep` alone is not trustworthy here: when this script runs inside a
# sandboxed shell that does not share the host's PID namespace, pgrep sees
# none of the actual PROTEUS processes even though they are genuinely alive
# and the sweep's log files are still being actively written -- mtime
# freshness is the fallback signal that still works in that situation.
STALE_MANAGER_SECS = 15 * 60
STALE_LOG_SECS = 15 * 60

CATEGORY_LABELS = {
    1: '1_success',
    2: '2_fatal_crash_main_loop',
    3: '3_crash_observe_step',
    4: '4_running_mid_loop',
    5: '5_running_past_solidified_stale_status',
    6: '6_unclassified',
}

# (signature name, required substring in exception type or None, required
# substring in exception type+message). Order matters: first match wins.
KNOWN_FAILURE_SIGNATURES = [
    ('atmodeller_overflow', 'OverflowError', 'cannot convert float infinity to integer'),
    ('atmodeller_zero_converged_multistart', None, 'zero converged multistart'),
    (
        'agni_coupling_deadlock',
        'RuntimeError',
        'consecutive AGNI failures with frozen interior state',
    ),
    ('observe_wavelength_out_of_range', 'ValueError', 'out of opacities table wavelength grid'),
]

LOOP_COUNTERS_RE = re.compile(
    r'\[\s*INFO\s*\]\s*current\s+init\s+maximum\s*\n\[\s*INFO\s*\]\s*(\d+)\s+(\d+)\s+(\d+)'
)
GRID_POINT_LINE_RE = re.compile(r'(\d+):\s*(\{.*\})\s*$')
EXCEPTION_LINE_RE = re.compile(r'^(\w*(?:Error|Exception))(?::\s*(.*))?$', re.MULTILINE)
TOTAL_RUNTIME_RE = re.compile(r'Total runtime:\s*([\d.]+)\s*(hour|minute|second)s?')
RUNTIME_UNIT_ABBREV = {'hour': 'hr', 'minute': 'min', 'second': 'sec'}


@dataclass
class CaseResult:
    name: str
    index: int | None
    params: dict
    category: int
    outcome: str
    loop: str | None
    exc_signature: str | None
    status_disagreement: str | None
    runtime: str | None = None


# --------------------------------------------------------------------------
# manager.log parsing
# --------------------------------------------------------------------------


def parse_grid_points(manager_log_text: str) -> dict[int, dict]:
    """Parse the "Flattened grid points" block (Section 1)."""
    mapping: dict[int, dict] = {}
    in_block = False
    for line in manager_log_text.splitlines():
        if 'Flattened grid points' in line:
            in_block = True
            continue
        if not in_block:
            continue
        m = GRID_POINT_LINE_RE.search(line)
        if m:
            try:
                mapping[int(m.group(1))] = ast.literal_eval(m.group(2))
            except (ValueError, SyntaxError):
                pass
        elif mapping:
            break  # block ended
    return mapping


def manager_crashed(manager_log_text: str) -> tuple[bool, str]:
    tail = manager_log_text[-6000:]
    if 'Traceback' not in tail:
        return False, ''
    matches = list(EXCEPTION_LINE_RE.finditer(tail))
    if matches:
        exc_type, exc_msg = matches[-1].groups()
        return True, f'{exc_type}: {(exc_msg or "").strip()}'
    return True, 'uncaught exception (type not recognized in tail)'


def manager_finished_cleanly(manager_log_text: str) -> bool:
    tail = manager_log_text[-4000:]
    return 'All cases have exited' in tail or 'All processes finished' in tail


def manager_is_alive(sweep_dir: Path) -> tuple[bool, str]:
    lines = get_pgrep_lines('proteus grid')
    if lines:
        return True, 'live "proteus grid" process found via pgrep'
    manager_log = sweep_dir / MANAGER_LOG_NAME
    if manager_log.is_file():
        age = time.time() - manager_log.stat().st_mtime
        if age < STALE_MANAGER_SECS:
            return (
                True,
                f'no "proteus grid" pgrep match, but manager.log modified {age:.0f}s ago '
                f'(< {STALE_MANAGER_SECS}s threshold)',
            )
    return False, 'no live "proteus grid" process and manager.log has not been touched recently'


# --------------------------------------------------------------------------
# process liveness
# --------------------------------------------------------------------------


def get_pgrep_lines(pattern: str) -> list[str]:
    try:
        result = subprocess.run(
            ['pgrep', '-af', pattern], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def get_alive_case_names() -> set[str]:
    lines = get_pgrep_lines('proteus start')
    return set(re.findall(r'case_\d{6}', ' '.join(lines)))


def case_is_alive(case_dir: Path, alive_case_names: set[str], mtime: float, now: float) -> bool:
    if case_dir.name in alive_case_names:
        return True
    return (now - mtime) < STALE_LOG_SECS


# --------------------------------------------------------------------------
# per-case log analysis (Sections 2-4)
# --------------------------------------------------------------------------


def read_status(case_dir: Path) -> tuple[str, str] | None:
    p = case_dir / STATUS_NAME
    if not p.is_file():
        return None
    lines = p.read_text(errors='replace').splitlines()
    if len(lines) >= 2:
        return lines[0].strip(), lines[1].strip()
    if len(lines) == 1:
        return lines[0].strip(), ''
    return None


def extract_last_loop(text: str, before_pos: int | None = None) -> int | None:
    matches = list(LOOP_COUNTERS_RE.finditer(text))
    if before_pos is not None:
        matches = [m for m in matches if m.start() < before_pos]
    if not matches:
        return None
    return int(matches[-1].group(1))


def extract_exception(text: str, tb_pos: int) -> tuple[str | None, str | None]:
    window_end = text.find('\n[ ', tb_pos + 20)
    if window_end == -1:
        window_end = min(len(text), tb_pos + 8000)
    window = text[tb_pos:window_end]
    matches = list(EXCEPTION_LINE_RE.finditer(window))
    if not matches:
        return None, None
    exc_type, exc_msg = matches[-1].groups()
    return exc_type, (exc_msg or '').strip()


def extract_runtime(text: str) -> str | None:
    """Wall-clock duration from the log's own "Total runtime: ..." line.

    Only ever printed by print_stoptime(), which runs after run_observe() has
    already returned successfully -- so this is only present for a genuine
    end-to-end success, never for a crashed or still-running case.
    """
    m = TOTAL_RUNTIME_RE.search(text)
    if not m:
        return None
    value, unit = m.groups()
    return f'{float(value):.2f} {RUNTIME_UNIT_ABBREV[unit]}'


def match_known_signature(exc_type: str | None, exc_msg: str | None) -> str | None:
    combined = f'{exc_type or ""}: {exc_msg or ""}'
    for name, type_needle, msg_needle in KNOWN_FAILURE_SIGNATURES:
        if type_needle and type_needle not in combined:
            continue
        if msg_needle not in combined:
            continue
        return name
    return None


def format_age(seconds: float) -> str:
    if seconds < 3600:
        return f'{seconds / 60:.0f} min'
    if seconds < 86400:
        return f'{seconds / 3600:.1f} hr'
    return f'{seconds / 86400:.1f} d'


def status_disagreement(status: tuple[str, str] | None, category: int) -> str | None:
    if status is None:
        return None
    _, status_str = status
    if 'Running' in status_str and category in (1, 2, 3, 6):
        return (
            f'on-disk status says "Running" but real outcome is '
            f'{CATEGORY_LABELS[category]} -- stale/misleading status file'
        )
    if 'Completed' in status_str and category not in (1, 5):
        return (
            f'on-disk status says "{status_str}" but real outcome is '
            f'{CATEGORY_LABELS[category]} -- stale/misleading status file'
        )
    return None


def analyze_case(
    case_dir: Path, params: dict, index: int | None, alive_case_names: set[str], now: float
) -> CaseResult:
    """Determine a case's real outcome per Sections 2-4 of the playbook."""
    log_path = case_dir / CASE_LOG_NAME
    status = read_status(case_dir)

    if not log_path.is_file():
        outcome = 'no simulation log found (crashed before logging started, or never launched)'
        return CaseResult(
            case_dir.name, index, params, 6, outcome, None, None, status_disagreement(status, 6)
        )

    text = log_path.read_text(errors='replace')
    mtime = log_path.stat().st_mtime
    alive = case_is_alive(case_dir, alive_case_names, mtime, now)

    tb_positions = [m.start() for m in re.finditer('Traceback', text)]
    if tb_positions:
        tb_pos = tb_positions[-1]
        exc_type, exc_msg = extract_exception(text, tb_pos)
        loop_at_crash = extract_last_loop(text, before_pos=tb_pos)
        loop_str = f'loop {loop_at_crash}' if loop_at_crash is not None else 'before loop 1'
        observe_pos = text.find('Observing the planet')
        category = 3 if (observe_pos != -1 and observe_pos < tb_pos) else 2
        sig = match_known_signature(exc_type, exc_msg)
        exc_label = sig or exc_type or 'unrecognized exception'
        stage = 'observe/spectrum step' if category == 3 else 'main loop'
        outcome = f'crashed in {stage} ({exc_label}) @ {loop_str}'
        return CaseResult(
            case_dir.name,
            index,
            params,
            category,
            outcome,
            loop_str,
            sig,
            status_disagreement(status, category),
        )

    if 'Simulation stopped' in text:
        loop_final = extract_last_loop(text)
        reason = ''
        if status and 'Completed' in status[1]:
            m = re.search(r'Completed\s*\((.*)\)', status[1])
            reason = m.group(1) if m else ''
        runtime = extract_runtime(text)
        outcome = 'success (main loop + observe completed'
        outcome += f', {reason}' if reason else ''
        outcome += f', last loop {loop_final})' if loop_final is not None else ')'
        loop_str = str(loop_final) if loop_final is not None else None
        return CaseResult(
            case_dir.name,
            index,
            params,
            1,
            outcome,
            loop_str,
            None,
            status_disagreement(status, 1),
            runtime,
        )

    if alive:
        loop_now = extract_last_loop(text)
        loop_str = f'loop {loop_now}' if loop_now is not None else 'unknown loop'
        if status and 'Completed' in status[1]:
            category = 5
            outcome = (
                f'still RUNNING at {loop_str} (post-solidification), but on-disk status '
                f'already reads "{status[1]}" -- stale, do not trust'
            )
        else:
            category = 4
            outcome = f'still RUNNING at {loop_str}'
        return CaseResult(
            case_dir.name,
            index,
            params,
            category,
            outcome,
            loop_str,
            None,
            status_disagreement(status, category),
        )

    age_str = format_age(now - mtime)
    status_str = status[1] if status else 'none'
    outcome = (
        f'dead, no traceback, no clean finish -- log stale for {age_str} '
        f'(on-disk status: {status_str}); likely killed externally'
    )
    return CaseResult(
        case_dir.name, index, params, 6, outcome, None, None, status_disagreement(status, 6)
    )


# --------------------------------------------------------------------------
# plot/log collection (absorbed from collect_grid_sweep_plots.py)
# --------------------------------------------------------------------------


def find_case_plots(case_dir: Path) -> list[Path]:
    found = []
    stack = [case_dir]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_dir():
                if entry.name not in SKIP_DIR_NAMES:
                    stack.append(entry)
            elif entry.suffix.lower() in IMAGE_EXTENSIONS:
                found.append(entry)
    return found


def copy_one(src: Path, dest_dir: Path, case_name: str, written: set[Path]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f'{case_name}{src.suffix.lower()}'
    if dest_path in written:
        # Same case produced a second same-named plot in this run (e.g. two
        # differently-located files that share a stem) -- disambiguate by
        # parent dir rather than silently dropping one.
        dest_path = dest_dir / f'{case_name}__{src.parent.name}{src.suffix.lower()}'
    shutil.copy2(src, dest_path)
    written.add(dest_path)


def copy_into(
    src: Path,
    dest_root: Path,
    plot_type: str,
    category_label: str,
    case_name: str,
    written: set[Path],
) -> None:
    copy_one(src, dest_root / plot_type, case_name, written)
    copy_one(src, dest_root / 'by_status' / plot_type / category_label, case_name, written)


def collect_plots(
    sweep_dir: Path, case_dirs: list[Path], results: dict[str, CaseResult]
) -> tuple[int, set[str]]:
    dest_root = sweep_dir / COLLECTED_DIRNAME
    # Wipe and rebuild fresh every run: a case's category can change between
    # runs (e.g. running -> success), and stale copies left in the wrong
    # by_status/<old_category>/ folder would otherwise never be cleaned up.
    if dest_root.is_dir():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True)

    n_files = 0
    plot_types: set[str] = set()
    written: set[Path] = set()
    for case_dir in case_dirs:
        category_label = CATEGORY_LABELS[results[case_dir.name].category]

        for plot_path in find_case_plots(case_dir):
            copy_into(
                plot_path, dest_root, plot_path.stem, category_label, case_dir.name, written
            )
            plot_types.add(plot_path.stem)
            n_files += 1

        log_path = case_dir / CASE_LOG_NAME
        if log_path.is_file():
            copy_into(
                log_path, dest_root, LOG_PLOT_TYPE, category_label, case_dir.name, written
            )
            n_files += 1

    return n_files, plot_types


# --------------------------------------------------------------------------
# aggregate reporting (Section 7)
# --------------------------------------------------------------------------


def format_param_value(v) -> str:
    if isinstance(v, float):
        return f'{v:g}'
    return str(v)


def build_per_case_table(
    case_dirs: list[Path], results: dict[str, CaseResult], param_keys: list[str]
) -> str:
    headers = ['case', *[k.split('.')[-1] for k in param_keys], 'runtime', 'outcome']
    rows = [headers]
    for case_dir in case_dirs:
        r = results[case_dir.name]
        param_cells = [format_param_value(r.params.get(k, '?')) for k in param_keys]
        rows.append([case_dir.name, *param_cells, r.runtime or '-', r.outcome])

    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]

    def fmt_row(cells: list[str]) -> str:
        return ' | '.join(cell.ljust(w) for cell, w in zip(cells, widths))

    lines = [fmt_row(headers), '-+-'.join('-' * w for w in widths)]
    lines.extend(fmt_row(row) for row in rows[1:])
    return '\n'.join(lines)


def build_param_breakdown(
    case_dirs: list[Path], results: dict[str, CaseResult], param_keys: list[str]
) -> str:
    lines = []
    for key in param_keys:
        values: dict = {}
        for case_dir in case_dirs:
            r = results[case_dir.name]
            v = r.params.get(key)
            values.setdefault(v, []).append(r.category)
        if len(values) < 2:
            continue
        lines.append(f'  {key}:')
        for v, cats in sorted(values.items(), key=lambda kv: str(kv[0])):
            n_success = sum(1 for c in cats if c == 1)
            lines.append(f'    {format_param_value(v)}: {n_success}/{len(cats)} succeeded')
    return '\n'.join(lines) if lines else '  (no varying parameters found)'


def build_failure_mode_summary(results: dict[str, CaseResult]) -> str:
    tally: dict[str, list[str]] = {}
    for name, r in results.items():
        if r.category in (2, 3):
            key = r.exc_signature or 'unrecognized exception'
            tally.setdefault(key, []).append(name)
    if not tally:
        return '  (no crashes)'
    lines = []
    for key, names in sorted(tally.items(), key=lambda kv: -len(kv[1])):
        flag = (
            ''
            if key != 'unrecognized exception'
            else (' [NOT in GRID_SWEEP_ANALYSIS.md Section 4 -- consider adding it]')
        )
        lines.append(f'  {key}: {len(names)} case(s){flag} -- {", ".join(sorted(names))}')
    return '\n'.join(lines)


def build_disagreement_summary(results: dict[str, CaseResult]) -> str:
    flagged = [
        (name, r.status_disagreement) for name, r in results.items() if r.status_disagreement
    ]
    if not flagged:
        return (
            '  (none -- on-disk status files agree with the log-derived outcome for every case)'
        )
    return '\n'.join(f'  {name}: {msg}' for name, msg in sorted(flagged))


def generate_summary(
    sweep_name: str,
    sweep_dir: Path,
    case_dirs: list[Path],
    results: dict[str, CaseResult],
    param_keys: list[str],
    manager_alive: bool,
    manager_alive_note: str,
    manager_crash: tuple[bool, str],
    manager_clean_finish: bool,
    n_files: int,
    plot_types: set[str],
    now: datetime,
) -> str:
    n_total = len(case_dirs)
    counts = {c: 0 for c in CATEGORY_LABELS}
    for r in results.values():
        counts[r.category] += 1
    n_success = counts[1]
    n_still_running = counts[4] + counts[5]

    if manager_crash[0]:
        status_line = f'MANAGER CRASHED mid-sweep: {manager_crash[1]}'
    elif n_still_running > 0 and manager_alive:
        status_line = f'sweep is IN PROGRESS ({manager_alive_note})'
    elif n_still_running > 0 and not manager_alive:
        status_line = (
            f'sweep appears ABANDONED/KILLED EXTERNALLY: {n_still_running} case(s) still show '
            f'as running in-process but no live manager was found ({manager_alive_note})'
        )
    elif manager_clean_finish:
        status_line = 'sweep has FINISHED (manager reported a clean exit)'
    else:
        status_line = (
            'sweep appears FINISHED or externally stopped (no cases still running, but '
            f'manager.log does not show a clean finish message; {manager_alive_note})'
        )

    lines = []
    lines.append(f'{sweep_name} -- grid sweep summary ({now.strftime("%Y-%m-%d %H:%M")})')
    lines.append('')
    lines.append(f'STATUS: {status_line}.')
    lines.append('')
    lines.append(
        f'HEADLINE: {n_success}/{n_total} cases fully succeeded (main loop + observe both clean).'
    )
    for cat, label in CATEGORY_LABELS.items():
        lines.append(f'  {label}: {counts[cat]}')
    lines.append('')
    lines.append('DOMINANT FAILURE MODE(S) (Section 4 of GRID_SWEEP_ANALYSIS.md):')
    lines.append(build_failure_mode_summary(results))
    lines.append('')
    lines.append('STATUS-FILE DISAGREEMENTS (on-disk `status` vs. log-derived outcome):')
    lines.append(build_disagreement_summary(results))
    lines.append('')
    if param_keys:
        lines.append('PER-PARAMETER OUTCOME BREAKDOWN (successes / total cases at that value):')
        lines.append(build_param_breakdown(case_dirs, results, param_keys))
        lines.append('')
    lines.append('Per-case table (grid point -> outcome):')
    lines.append('')
    lines.append(build_per_case_table(case_dirs, results, param_keys))
    lines.append('')
    lines.append(
        f'Collected plots/logs: {sweep_dir / COLLECTED_DIRNAME}/, {n_files} file(s) across '
        f'{len(plot_types)} plot type(s) (+ {LOG_PLOT_TYPE}), grouped both flat '
        '(<plot_type>/<case>.<ext>) and by outcome (by_status/<plot_type>/<category>/<case>.<ext>).'
    )
    if manager_crash[0] or (
        n_still_running == 0 and not manager_clean_finish and not manager_alive
    ):
        lines.append('')
        lines.append(
            'RE-RUNNING NOTE: grid-sweep cases do not auto-resume. If any crashed/killed cases '
            'need re-running, that requires a fresh `proteus grid -c copy.grid.toml` invocation '
            'for those grid points, not a resume of this sweep.'
        )
    return '\n'.join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def run(sweep_dir: Path, sweep_name: str, do_collect: bool) -> None:
    manager_log_path = sweep_dir / MANAGER_LOG_NAME
    manager_log_text = (
        manager_log_path.read_text(errors='replace') if manager_log_path.is_file() else ''
    )

    grid_points = parse_grid_points(manager_log_text)
    param_keys = sorted({k for params in grid_points.values() for k in params})

    case_dirs = sorted(p for p in sweep_dir.glob('case_*') if p.is_dir())
    if not case_dirs:
        sys.exit(f'No case_* directories found under {sweep_dir}')

    alive_case_names = get_alive_case_names()
    now = time.time()

    results: dict[str, CaseResult] = {}
    for case_dir in case_dirs:
        m = re.search(r'case_(\d+)', case_dir.name)
        index = int(m.group(1)) if m else None
        params = grid_points.get(index, {})
        results[case_dir.name] = analyze_case(case_dir, params, index, alive_case_names, now)

    manager_alive, manager_alive_note = manager_is_alive(sweep_dir)
    crashed = manager_crashed(manager_log_text)
    clean_finish = manager_finished_cleanly(manager_log_text)

    n_files, plot_types = (0, set())
    if do_collect:
        n_files, plot_types = collect_plots(sweep_dir, case_dirs, results)

    summary_time = datetime.now()
    summary_text = generate_summary(
        sweep_name,
        sweep_dir,
        case_dirs,
        results,
        param_keys,
        manager_alive,
        manager_alive_note,
        crashed,
        clean_finish,
        n_files,
        plot_types,
        summary_time,
    )

    print(summary_text)

    summary_path = sweep_dir / f'summary_{summary_time.strftime("%Y%m%d_%H%M%S")}.txt'
    summary_path.write_text(summary_text + '\n')
    print(f'\n(summary saved to {summary_path})')


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('sweep_name', help='Name of the grid sweep directory under output/')
    parser.add_argument(
        '--no-collect',
        action='store_true',
        help='Skip collecting plots/logs into collected_plots/',
    )
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parent
    sweep_dir = output_dir / args.sweep_name
    if not sweep_dir.is_dir():
        sys.exit(f'Sweep directory not found: {sweep_dir}')

    run(sweep_dir, args.sweep_name, do_collect=not args.no_collect)


if __name__ == '__main__':
    main()
