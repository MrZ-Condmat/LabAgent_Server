# Repairing daily reports

Use `DeepSeek-V4.1-Flash` for both entries in `lab_agent/config/models.json`.
Keep model names in the committed release source. Production credentials belong
in `/data/zmr/projects/labagent_runtime/labagent.env`, outside the repository.

## Runtime and data boundaries

Run the tools as the application user in the existing server environment:

```bash
cd /data/zmr/projects/labAgent_Server
source /home/zmr/miniforge3/etc/profile.d/conda.sh
conda activate labagent_server
export LABAGENT_PROJECT_ROOT=/data/zmr/projects/labAgent_Server
export LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env
```

Reports and logs may be symlinks into `labagent_shared`. Back up actual report
contents before a repair; archiving the symlink alone does not back up reports:

```bash
reports_dir=$(readlink -f reports)
backup="$HOME/labagent_reports_before_repair_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "$backup" -C "$reports_dir" .
```

Do not run overlapping repair jobs on the same dates or edit those reports
while a repair is running. Stopping or restarting Web does not stop a separately
launched background repair. Source snapshots, checkpoints, logs, backups and
credentials must not be committed to Git.

## Existing JSON: rescore failed articles

`scripts/rescore_saved_reports.py` uses saved `all_papers` records. It recognizes
`Error in scoring...` and `Unable to parse response`, preserving genuine valid
Priority 1 articles and other successful assessments. It does not fetch current
articles and label them with a historical date.

The default mode inventories files without calling the scoring API. For example:

```bash
python scripts/rescore_saved_reports.py --from 2026-10-15 --through 2026-11-06
nohup python -u scripts/rescore_saved_reports.py --from 2026-10-15 --through 2026-11-06 --apply > logs/rescore_20261015_20261106.log 2>&1 &
```

The apply mode overwrites JSON/HTML and rebuilds journal summary and Highlights
reports. Saved source assessments are reused when rebuilding summaries after a
restart. Per-report writes are atomic per file; this simpler repair tool keeps
its per-paper cache in memory, so an interrupted, unfinished report may repeat
some scoring on the next run.

Empty or unusable responses and transient network/API errors are retried up to
three requests per paper. If they still fail, the original failure marker is
retained and the remaining papers continue. Authentication, permission and other
non-transient request errors stop the run. `INCOMPLETE` means unresolved scores
remain; rerun the same range to retry those papers. `MISSING` means no source
report is saved and cannot be repaired by this tool. Weekend ArXiv reports are
normally absent.

## Missing JSON: recover dated source records

`scripts/backfill_daily_reports.py` also creates missing reports. It uses:

- Saved historical articles and a snapshot of the configured journal RSS feeds,
  filtered to the report date and previous date.
- Nature Communications publisher archive pages as a fallback for its short RSS
  feed. Archive pagination must reach dates older than the requested window;
  it includes the previous day of the first requested report. Cross-year
  publisher archive recovery is explicitly rejected.
- An arXiv listing whose visible announcement date exactly matches the missing
  report date, with its parsed paper count checked against the listing total.
  A mismatch is rejected. This tool uses the current `cond-mat/new` listing;
  it cannot reconstruct an arbitrary past arXiv date whose listing is no longer
  current. Saturday/Sunday ArXiv reports follow the scheduled skip behavior.

RSS snapshots cannot prove that an expired historical entry never existed.
Publisher recovery currently covers Nature Communications, not every configured
journal. Keep the snapshots and their provenance when assessing completeness.

Prepare and validate source snapshots before applying changes:

```bash
work="$HOME/labagent_backfill_20261107_20261109"
python scripts/backfill_daily_reports.py --from 2026-11-07 --through 2026-11-09 --work-dir "$work" --prepare
nohup python -u scripts/backfill_daily_reports.py --from 2026-11-07 --through 2026-11-09 --work-dir "$work" --apply > logs/backfill_20261107_20261109.log 2>&1 &
```

These dates illustrate the completed November recovery. A new recovery must
pass the source-date checks for its own dates; do not assume this example can
fetch those missing historical arXiv articles later.

The work directory contains:

| Path | Purpose |
|---|---|
| `sources/`, `prepared.json` | Dated source snapshots and preparation record |
| `backup/` | Original report files, copied before apply and preserved on resume |
| `scoring_checkpoint.json` | Valid per-paper results, saved after each response |
| `status.json` | Current date/source, completion state and unresolved articles |
| `run.lock` | Prevents concurrent runs using the same work directory |
| `summary_staging/`, `highlights_staging/` | Generated aggregates before replacing live report files |

Resume with the same work directory and date range. A changed scoring model
cannot reuse an existing checkpoint. Individual report files are replaced
atomically; the complete date range is not a single transaction. Valid original
scores remain unchanged. The program does not write chat history or migrate the
database.

When keeping tools outside an immutable release, install
`backfill_daily_reports.py` and `rescore_saved_reports.py` together, for example
in `/home/zmr/labagent_tools/`. The dated tool adds `--project-root` to its Python
import path itself. The standalone rescore tool installed outside the project
also needs `PYTHONPATH=/data/zmr/projects/labAgent_Server`.

Inspect the log and status after a run. A zero-unresolved `COMPLETE` message
means available source records were repaired; missing source records require
separate recovery. Check journal summary counts against source reports and
Highlights counts against Priority 3 articles. Refresh the website to reload the
updated reports.
