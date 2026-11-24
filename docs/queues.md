<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Running through a queue

Without `--command`, `horizon run` runs the study here, one run per CPU at a
time (`-j N` to change that), and waits for it. With `--command`, it hands
each run to a scheduler and returns. Each run is one Navigate command, which
the prefix gets with `{run}`, `{study}` and `{cmd}` filled in:

```bash
horizon run my_study.hor --command "pueue add -p -g horizon --label {run} -- {cmd}"
horizon run my_study.hor --command "sbatch --parsable --job-name {run} --wrap {cmd}"
```

`{cmd}` is the Navigate command as one quoted argument, for schedulers that
take a command string (`pueue add -- {cmd}`, `sbatch --wrap {cmd}`); without
it, the command's words are appended to the prefix. Groups, partitions,
priorities and labels are the scheduler's own options, so they go in the
prefix (`pueue add -g horizon`, `sbatch -p short`).

The command names the `navigate` that `horizon run` found, so the queue's
machines need the same environment, and the study folder on a disk they
share.

## Collecting after the runs

`--after PREFIX` then hands `horizon collect` to the scheduler the same way,
waiting on the job ids the runs' submissions printed, so ask the scheduler
for just the id (`pueue add -p`, `sbatch --parsable`; a `;` and the cluster
after it are dropped). `{ids}` on its own becomes one argument per id, as
`pueue add --after` takes them; inside a word, the ids joined by `:`, as
slurm's `--dependency=afterok:` takes them:

```bash
horizon run my_study.hor --command "pueue add -p -g horizon --label {run} -- {cmd}" \
                         --after "pueue add -p -g horizon --label my_study_collect --after {ids} -- {cmd}"
horizon run my_study.hor --command "sbatch --parsable --job-name {run} --wrap {cmd}" \
                         --after "sbatch --parsable --kill-on-invalid-dep=yes --dependency=afterok:{ids} --wrap {cmd}"
```

pueue needs the `--` before `{cmd}`, as `--after` takes every word up to it.

If a run fails, pueue fails the collect step, and slurm cancels it with
`--kill-on-invalid-dep=yes` (without, it stays pending); start the run again
(`pueue restart <task>`, or its command from `horizon.log`) and, once it is
done, run `horizon collect` yourself.

A slurm dependency is one argument, which Linux caps at 128 kB, some 15 000
job ids; and whether a run that ended and left slurm's memory (`MinJobAge`)
before the collect step was submitted still counts as done depends on the
Slurm version, so try it on your cluster.

`--write-ids` writes what each submission printed to `job_ids.txt` in the
study folder, one line per run, e.g. for a post-processing job of your own;
`horizon.log` pairs each run id with what its submission printed either way.

## The environment a run gets

Every submission passes its scheduler only what a run needs: `PATH`, `HOME`,
the locale, the Python or conda environment, `ASSUMPTIONS_DATA_DIR`, Gurobi's
licensing, thread limits and what the scheduler's client needs to find its
daemon (`PUEUE_CONFIG_PATH`, `SLURM_CONF`, ...). pueue keeps the environment
of `pueue add` with each task and rewrites its whole state on every add, so a
shell's full environment slows every add as the queue grows; slurm hands it
to the job.

`HORIZON_TASK_ENV=NAME1,NAME2` passes more variables (e.g. `SBATCH_ACCOUNT`,
or what `module load` sets), `--full-task-env` the whole environment, and
`--dry-run` lists what the submissions would get.
