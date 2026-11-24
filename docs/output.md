# The study folder

`OutputPath` in the `.hor` file names the study folder, e.g. `output`,
which holds:

| Path | What |
|------|------|
| `output/study.json` | the deck, the navigate command and its arguments, where the study came from, and every run, one per line: its run id, scenario combination, sample number and the value of every token |
| `output/logs/` | Navigate's log of each run and Horizon's own `horizon.log`, which has the command of each run; cleared when the study is run again |
| `output/job_ids.txt` | with `--write-ids`: what the scheduler printed for each run, its job id when asked for just that; never deleted, only overwritten |
| `output/parquets/<run_id>.parquet` | the results of one run; the folder is deleted once the study is compacted |
| `output/<study>_archive.parquet` | the archive: every row of every run, with the columns `run` and `sample` |
| `output/<study>.parquet` | for Lantern: per scenario combination the runs `<label>` (the representative sample), `<label>_median`, `<label>_p05` and `<label>_p95` |

Runs are named `<label>_sample<NNN>`. The label is the Include rule's
`_name`, else the active scenario values joined by `_`, else the study name;
the study name is the `.hor` file name. See
[Run labels](configuration.md#run-labels).

## Compacting the results

Once every run is ok, `horizon collect` compacts the study into two files.
The archive, `<study>_archive.parquet`, holds every row of every run. The
Lantern file, `<study>.parquet`, holds the summary Lantern once computed
itself from an ensemble, as four runs per scenario combination that Lantern
opens and compares:

- `<label>`: the representative sample, the one that stays closest to the
  median of WTW emissions, installed power, fuel consumed by fuel type and
  fuel-related expenses (weighted 0.4, 0.3, 0.2 and 0.1). It is one real run,
  so its stacked plots add up.
- `<label>_median`, `<label>_p05`, `<label>_p95`: for each result row and
  date, the median and the 5th and 95th percentiles over the samples. The
  file's metadata names them per label under `summaries`, as `median`, `low`
  and `high`, the way Lantern finds them.

Percentiles do not add up, so where a plot sums rows (all fleets of a fuel,
say), the band of the sum is wider than the true band.

`collect` deletes `parquets/` only once both files are written and read
back. With `horizon run --after`, the scheduler starts the collect step once
the runs have succeeded (see [Running through a queue](queues.md));
otherwise run `horizon collect` once every run is done. It refuses while a
run has no results, and `horizon collect --partial` compacts what there is
and keeps the per-run results.

## Failed runs

Horizon keeps no state about the runs. A run that fails shows where it ran:
in the scheduler's status and log, or, without `--command`, in the summary
`horizon run` prints, grouped by the last line of each failed run's output.
Navigate's log of the run is in `logs/`. To run one again, restart it in the
scheduler (e.g. `pueue restart <task>`), or take its command from
`horizon.log`, which has the command of every run.
