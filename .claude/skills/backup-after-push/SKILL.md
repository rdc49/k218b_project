---
name: backup-after-push
description: Use immediately after staging a commit and pushing it to GitHub in this repo — mirrors the whole project directory (including gitignored simulation_data/, raw_grid_output/, logs/) to the attached external hard drive via rsync. This is required after every push, not optional.
---

# Backing up to the attached hard drive after a GitHub push

**Whenever you stage a commit in this repo and push it to GitHub, also mirror
this entire project directory to the external hard drive attached to this
machine, straight after the push succeeds.** This is *in addition to* the
push, not instead of it: `git push` only ever carries the small
code/config/docs footprint that's actually tracked in git. The bulk of this
project — `simulation_data/`, `raw_grid_output/`, `logs/` — is deliberately
gitignored (currently ~336GB and growing as the grid sweep completes) and is
never captured by any push, so the external-drive mirror is the only backup
that data gets.

- **Drive**: `/dev/sda1`, mounted at `/run/media/rdc49/Expansion`, exFAT,
  ~932GB total. Before backing up, confirm it's actually mounted
  (`findmnt /run/media/rdc49/Expansion`) and has enough free space
  (`df -h /run/media/rdc49/Expansion`) — if it isn't plugged in, skip the
  backup rather than failing the push; the push having already succeeded is
  what matters, the backup is best-effort on top of it.
- **exFAT has no Unix permission bits, ownership, or symlink support** —
  don't use `rsync -a` (which implies `-p`/`-o`/`-g`/`-l`) against this
  target: permission/ownership preservation will error, and any symlink in
  the tree (e.g. a `PROTEUS/output/<name>` symlink from an active sweep)
  can't be stored as a symlink at all. Dereference symlinks into real files
  instead (`-L`), and only preserve recursion, timestamps, and a
  `--modify-window` to tolerate exFAT's coarser timestamp resolution.
- **Destination**: a dedicated subdirectory under the drive's existing
  `PhD/` folder (alongside `PhD/Simulation_Output_Data/`),
  `/run/media/rdc49/Expansion/PhD/K218b_project_backup/`, so the mirror
  can't collide with or overwrite anything else already there or elsewhere
  on the drive (it also holds unrelated personal files outside `PhD/`).
- **Command** — a one-way mirror, source of truth is this project directory:
  ```bash
  rsync -rtvL --modify-window=2 --delete \
    /data/rdc49-2/K218b_project/ \
    /run/media/rdc49/Expansion/PhD/K218b_project_backup/
  ```
- **Run it detached and logged**, not in the foreground: a full mirror of a
  multi-hundred-GB directory over NFS-to-USB can take a long time,
  especially the first run, and will often outlive the current turn — that's
  fine, don't wait on it before reporting the push as done, just note that
  the backup was kicked off and where its log is:
  ```bash
  nohup rsync -rtvL --modify-window=2 --delete \
    /data/rdc49-2/K218b_project/ \
    /run/media/rdc49/Expansion/PhD/K218b_project_backup/ \
    > /data/rdc49-2/K218b_project/logs/hardrive_backup_$(date +%Y%m%d_%H%M%S).log 2>&1 &
  disown
  ```
- `--delete` makes the mirror match this directory exactly, including
  deletions — correct for a one-way backup of this project, but confirm the
  source/destination order above is preserved if this command is ever
  adapted, since reversing it would instead delete backup history to match
  a stale local copy.
