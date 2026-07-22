# How to launch a PROTEUS grid sweep on the IoA cluster

Not tracked by git (matches the `nogit*` pattern in `.gitignore`). Written for
future agents/sessions so the steps below don't need to be re-discovered.

## Facts that hold for every machine in this cluster

`/home/rdc49` and `/data/rdc49-2` are NFS-shared across all machines below
(verified: identical `~/.bashrc` md5sum on cap001a and cap004a; same
`/data/rdc49-2/PROTEUS` checkout visible from every node). That means:

- The real PROTEUS checkout is at **`/data/rdc49-2/PROTEUS`**, not
  `~/PROTEUS`, on every machine — there is no per-machine checkout and no
  symlink from the SSH-reported `$HOME` (`/home/rdc49`) to it.
- Conda env `proteus` lives at `/data/rdc49-2/anaconda3/envs/proteus`
  (separate from the module-provided miniforge envs) on every machine.
- There is no Slurm anywhere on this cluster (`squeue`/`sbatch` do not
  exist). Grid sweep TOMLs for this project set `use_slurm = false`, so
  `proteus grid` runs cases as local multiprocessing workers bounded by
  `max_jobs` in the sweep config. "How busy is the machine" must be judged
  per-machine from `uptime`, `free -h`, and `ps aux` — there is no cluster
  job queue to check instead, and load on one node tells you nothing about
  any other node.
- The `~/.bashrc` shell-context gotcha below applies identically everywhere,
  since it's the same file.
- **These machines are shared with other people, not dedicated to this
  project.** Always launch `proteus grid` under `nice -n 19` (see "Steps to
  launch a sweep" below) so a sweep's CPU-bound worker processes yield to
  anyone else's work on the same node, rather than competing with it on
  equal footing. This is a courtesy floor, not a substitute for the
  "Choosing a machine" load check below — still avoid a machine that
  already looks busy with someone else's work.

## Choosing a machine

Most machines below are the same spec (48 cores, 251 GiB RAM), but **not
all** — two confirmed exceptions: `cap001a` has only **24 cores**, and
`cap005a` has **112 cores / 629 GiB RAM**. Don't assume 48 cores for a
candidate; check `nproc` on it directly (the per-machine table below has
the confirmed counts as of the last full check). All reachable directly via
`ssh <name>` from this environment (no bastion/jump host, no VPN needed) —
`<name>.ast.cam.ac.uk` resolves fine. Confirmed reachable as of 2026-07-20:

```
cap001a  cap001b  cap001c  cap001d
cap002b  cap002c  cap002d
cap003a  cap003b  cap003c  cap003d
cap004a  cap004d
cap005a
```

(`cap002a` timed out rather than refusing — it may be down; `cap005b/c/d`
don't resolve at all — that group appears to only have an `a` node. There
may be more machines beyond this set that were never probed; the above is
just what's been verified reachable, not necessarily exhaustive.)

### Per-machine spec and core usage (last full check: 2026-07-22)

Estimated cores in use = `(100 - %idle) * nproc / 100` from a `top -bn1`
snapshot on each machine, cross-checked against `ps aux` for real
`proteus start` worker processes (not just background daemons — every
machine runs the Tanium endpoint-security agent, firewalld, fail2ban and
tuned as root, which shows up as a small, non-zero, ignorable CPU/load
baseline even when nothing else is running).

| Machine | Cores (`nproc`) | Cores in use (≈) | Notes |
|---|---|---|---|
| cap001a | 24 | ~1.7 | Background daemons only |
| cap001b | 48 | ~0.3 | Idle |
| cap001c | 48 | ~0.1 | Idle |
| cap001d | 48 | ~0.2 | Idle |
| cap002b | 48 | ~0.3 | Idle |
| cap002c | 48 | ~1.4 | Background daemons only |
| cap002d | 48 | ~0.4 | Idle |
| cap003a | 48 | ~2.4 | Transient `firewall-cmd` blip at check time; no simulation work |
| cap003b | 48 | ~0.4 | Idle |
| cap003c | 48 | ~0.5 | Idle |
| cap003d | 48 | ~0.3 | tmux session `proteus_grid_extremety_sweep` still exists but has **no live worker process** — that sweep has stopped/finished; treat as free (see bookkeeping section) |
| cap004a | 48 | ~0.3 | Idle |
| cap004d | 48 | ~0.4 | Idle |
| cap005a | 112 | ~6.4 | **6 live `proteus start` workers at ~99.5% CPU each** (the `extremity_sweep_calliope` grid) |

This snapshot goes stale immediately — re-check load before picking a
machine rather than trusting this table, but it's a useful starting point
for which nodes are worth probing first (everything except `cap005a` was
essentially idle at check time).

**If the user names a specific machine, use it — do not second-guess their
choice.** If the user does not specify a machine:

1. Pick 2-3 candidates from the list above (or all of them, if launching
   many sweeps at once and comparing).
2. Check each candidate's load before committing:
   ```
   for h in cap001a cap002c cap003b; do
     echo "== $h =="
     ssh -o ConnectTimeout=6 $h "uptime; free -h | awk '/Mem:/{print \$3, \"used /\", \$2}'; ps aux --sort=-%cpu | grep -Ei 'python|julia|proteus|agni|spider' | grep -v grep"
   done
   ```
3. Choose the one with the lowest 5-min load average and no other user's
   simulation processes running. Load average well below that machine's own
   core count (`nproc` — don't assume 48, see the spec exceptions above) and
   no non-root python/julia/proteus/agni/spider processes = safe to use. A
   previously-launched sweep of your own on another node is not a reason to
   avoid that node for a second sweep unless it's already using most of that
   machine's cores.

## Before launching: avoid clobbering an existing output folder

`/data/rdc49-2/PROTEUS/output/` sits on the same cluster-wide shared
filesystem as everything else in this doc, so this check can be done
directly from wherever the agent is running — no `ssh` needed — as long as
that environment also mounts `/data/rdc49-2` (local Claude Code sessions in
this project do; confirmed by writing this very file locally and seeing it
appear instantly on cap003d). If working from an environment that does
*not* mount `/data/rdc49-2`, do the same check over `ssh <machine>` instead.

**Batch configs under `grid_sweep_configs/batch_configs/` set a `symlink`
field** pointing at an absolute path under
`/data/rdc49-2/K218b_project/raw_grid_output/`. When `symlink` is set,
`PROTEUS/output/<name>` ends up being *just a symlink* to that path — the
real risk of clobbering existing data is at the `symlink` target, not at
`PROTEUS/output/<name>` itself. Worse, `Grid.__init__` (`PROTEUS/src/proteus/grid/manage.py`
~lines 146-156) `rmtree`s the symlink target if it already has content,
with **no confirmation prompt**, only refusing if it finds a `.git` folder
inside. So for these configs, check the `symlink` target, not just
`output`. Configs without a `symlink` field (or with it blank) behave as
before — only `output` matters.

1. Read both the `output = "..."` and `symlink = "..."` lines from the
   sweep config (strip trailing slashes for comparisons; `symlink` may be
   blank/absent for older configs):
   ```
   grep -E '^\s*(output|symlink)\s*=' <config>.toml
   ```
2. Check whether the *real* target already exists and has content — this
   is the `symlink` path if set, otherwise `/data/rdc49-2/PROTEUS/output/<name>`:
   ```
   ls -d <symlink-path-or-/data/rdc49-2/PROTEUS/output/<name>> 2>/dev/null && echo EXISTS
   ```
3. If it already exists, do **not** launch into it — it may hold results
   from a previous run, or belong to a sweep that's still running elsewhere
   on the cluster, and launching would silently `rmtree` it. Find the
   first unused suffix by appending `_1`, `_2`, ... and checking the same
   real target (not `PROTEUS/output/<name>`, which will always look
   available since it's just a symlink recreated fresh every launch):
   ```
   base=<name>; n=1; new="${base}_${n}"
   while [ -d "<real-target-dir>/${new}" ]; do
     n=$((n+1)); new="${base}_${n}"
   done
   ```
4. Never edit the user's original config file to do this. Instead, write a
   scratch copy under `input/nogit_grid_launch_configs/` (create it if it
   doesn't exist yet — the `nogit_` prefix keeps it out of git no matter
   where the original config lives) with the `output` line changed to
   `${new}` — **and, if the config sets `symlink`, that line changed to
   match the same `${new}` suffix too**, so the two stay paired. A
   mismatched pair (fresh `output` name but a stale/colliding `symlink`
   target) defeats the whole point of renaming:
   ```
   mkdir -p input/nogit_grid_launch_configs
   sed -E \
     -e "s#^([[:space:]]*output[[:space:]]*=[[:space:]]*\")[^\"]*#\1${new}/#" \
     -e "s#^([[:space:]]*symlink[[:space:]]*=[[:space:]]*\")[^\"]*(/[^/\"]*)\"#\1\2_renamed_${new}\"#" \
     <config>.toml > "input/nogit_grid_launch_configs/$(basename <config>.toml .toml)_${new}.toml"
   ```
   (That `symlink` substitution is illustrative, not copy-paste-safe for
   every naming scheme — check the resulting file by eye before using it;
   getting `output` and `symlink` out of sync is worse than not renaming
   at all.)
5. Use that scratch copy — not the original — as the `-c` argument in every
   command in the "Steps to launch a sweep" section below (dry-run and real
   launch alike), and record which config/output/symlink you actually used
   in the "Currently running sweeps" bookkeeping section at the bottom of
   this file, so the next agent doesn't collide with it either.

If the real target does not already exist, skip straight to launching with
the original, unmodified config.

## Shell-context gotcha (the thing that will trip you up)

`~/.bashrc` starts with `[ -z "$PS1" ] && return` — it only loads
environment modules and sets up the personal conda install for
**interactive** shells.

- A plain non-interactive `ssh <machine> "some command"` lands in
  `/home/rdc49` and has neither `module` nor the `proteus` conda env
  available.
- An interactive shell (a `tmux` pane, or `ssh -t <machine> bash -i -c
  '...'`) does have `module`/`conda` available, *and* in that context `cd
  PROTEUS` resolves to `/data/rdc49-2/PROTEUS` (the interactive shell's
  effective home differs from the SSH-reported `$HOME`).
- For any one-off non-interactive check, sidestep this entirely: wrap in
  `bash -i -c '...'` to get `module`/`conda` loaded, and use the
  **absolute** path `/data/rdc49-2/PROTEUS/...` rather than relying on a
  relative `cd`.

## Steps to launch a sweep

Substitute `<machine>` with whichever host was chosen above, and
`<config-path>` with the original config's path unless the pre-flight check
above produced a renamed scratch copy under
`input/nogit_grid_launch_configs/` — if it did, use that copy's path for
every command below instead.

1. Check the machine isn't already busy with someone else's runs:
   ```
   ssh <machine> "uptime; free -h; ps aux --sort=-%cpu | head -20"
   ```
   Look for non-root `python`/`julia`/`proteus`/`agni`/`spider` processes,
   not just system daemons.

2. (Optional but recommended) validate the sweep config first without
   spending compute:
   ```
   ssh -t <machine> "bash -i -c 'conda activate proteus && nice -n 19 proteus grid -c /data/rdc49-2/PROTEUS/<config-path> --dry-run'"
   ```
   (The dry-run itself barely touches the CPU, but `nice` every `proteus
   grid` invocation on principle — see the shared-machines note above.)
   A real dry-run writes per-case TOMLs under
   `output/<name>/cfgs/case_NNNNNN.toml` and each "case" flips from queued
   to exited within about a second — because no simulation actually runs.
   If a real launch behaves like this (all cases exiting near-instantly),
   something is wrong; a genuine run keeps cases in `running` for much
   longer. Delete the dry-run's `output/<name>/` directory before the real
   launch so it doesn't collide — this is safe to do even after the
   pre-flight rename check above, since the dry-run only ever touches the
   (possibly renamed) `output` folder that check confirmed was free.

3. Launch for real in a detached tmux session:
   ```
   ssh <machine> "tmux new-session -d -s <session_name> -c /data/rdc49-2/PROTEUS \
     && tmux send-keys -t <session_name> 'module load netcdf/4-2025.01' C-m \
     && sleep 2 \
     && tmux send-keys -t <session_name> 'cd PROTEUS' C-m \
     && sleep 1 \
     && tmux send-keys -t <session_name> 'conda activate proteus' C-m \
     && sleep 3 \
     && tmux send-keys -t <session_name> 'nice -n 19 proteus grid -c <config-path>' C-m"
   ```
   `nice -n 19` (the lowest possible scheduling priority) is not optional —
   these machines are shared with other people, and `proteus grid`'s worker
   subprocesses (forked from the niced parent, so they inherit its
   priority automatically — no need to nice each one individually) are
   CPU-bound for the whole run. Creating the session with `-d` means it is
   already detached — there is no need to attach and then detach again.

4. Confirm it's actually running the real sweep (not stuck, not another
   accidental dry-run):
   ```
   ssh <machine> "tmux capture-pane -t <session_name> -p | tail -20"
   ```
   Expect a `queued / running / exited` progress line where `running` stays
   nonzero for well more than a few seconds per case, and (for
   atmodeller-backed configs) a benign `RuntimeWarning: os.fork() was
   called... incompatible with multithreaded code` from JAX — this is
   expected noise, not a failure.

5. Nothing further is needed to "log out" — each `ssh <machine> "..."` call
   above opens and closes its own connection; no persistent attached session
   is held open. The tmux session keeps running server-side after the SSH
   connection closes. To check on it later:
   ```
   ssh <machine> "tmux capture-pane -t <session_name> -p | tail -20"
   ```
   or `ssh <machine>` then `tmux attach -t <session_name>` interactively
   (and `Ctrl-b d` to detach again).

## Currently running sweeps (update as sweeps finish / new ones launch)

Nothing currently running as of 2026-07-22 (both entries below killed
deliberately). Both `cap003d` and `cap005a` are free.

- `cap003d`, tmux session `proteus_grid_extremety_sweep`:
  `proteus grid -c input/nogit_ensembles/K218b_project/extremety_sweep_atmodeller.toml`
  (32-case grid), launched 2026-07-20. Was already found stopped (no live
  worker process, just the empty tmux shell) when re-checked 2026-07-22 —
  the sweep had finished or died on its own before that check. The
  leftover tmux session itself was killed 2026-07-22 for cleanup; nothing
  live was lost by doing so. (The `k218b_project_extremity_sweep` output
  folder — the non-suffixed, non-`_2` one — was already moved into
  `simulation_data/` earlier in this project while cases were still
  mid-run; whether this tmux session belongs to that output or a later
  relaunch wasn't re-verified.)
- `cap005a`, tmux session `proteus_grid_extremety_sweep_calliope`:
  `proteus grid -c input/nogit_ensembles/K218b_project/extremety_sweep_calliope.toml`
  (32-case grid, output `k218b_project_extremity_sweep_calliope/`), launched
  2026-07-20. cap005a was chosen for lowest 5-min load average (0.04) among
  cap001a/cap002c/cap004a/cap004d/cap005a; note it reports 629 GiB RAM vs the
  251 GiB on the other probed nodes, so it isn't identical spec after all
  (112 cores too — see the spec-exceptions note above). Confirmed running
  (6 live `proteus start` workers at ~99.5% CPU each) as of 2026-07-22, then
  **deliberately killed** the same day via `tmux kill-session` — this
  stopped all 6 in-progress cases mid-run (each had ~48h of accumulated
  compute). To resume rather than restart from scratch, use `proteus start
  -r`/`--resume` per-case against
  `output/k218b_project_extremity_sweep_calliope/`, provided each case's
  helpfile/interior-snapshot checkpoint state is intact.

## Unrelated finding worth flagging

`~/.bashrc` (shared across the whole cluster) has a GitHub personal access
token hardcoded in plaintext (`export GITHUB_TOKEN=...`). Don't propagate it
anywhere; flag it to the user for rotation if you notice it again.
