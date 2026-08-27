<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Performance plan: file generation and queuing

> **Status.** Phases 0–2 have landed on this branch, one commit per step,
> each through the full verification protocol below. Measured on the
> benchmark container (2 000 realizations): generation 5.3 s → 1.7 s,
> queue submission via real pueue 4.0.4 129 s → 12.8 s (direct daemon
> connection, minimal task env) and overlapped with generation;
> `state.json` 17.2 MB → 2.0 MB. Still open: 1.5 (pool tuning — measure on
> a Windows workstation), 2.3's TCP+TLS variant for Windows (Windows uses
> the CLI fallback until then), 2.4 (docs/groups), and Phase 3.

Goal: make `horizon my_study.hor` — parse, sample, generate `.nav` files,
queue via pueue — as fast as the machine allows, in steps that are each
independently verifiable and revertable. Earlier work (thread pools for
generation and submission, the include-file read cache, lazy work-item
generation) helped; this plan is built on fresh measurements of where the
remaining time actually goes.

Every number below was measured on a 4-CPU Linux container with the
benchmark harness in [`tools/benchmark/`](../tools/benchmark/README.md)
(2 000 realizations: 8 scenario combinations x 250 samples, 800-line
template, 12 includes of which 4 tokenized, pueue 4.0.4). Absolute numbers
will differ on team hardware — especially Windows, where process spawns and
small-file I/O cost several times more — so each step's gate is the
*relative* improvement measured by the same harness on the same machine.

## Where the time goes today

| Phase | Baseline (2 000 realizations) | Dominant cost |
|-------|------------------------------|---------------|
| parse + sample + CSV | 0.2 s (grows quadratically with samples, see LHS below) | `lhs(criterion="maximin")` recomputed per scenario combination |
| generate `.nav` + includes | 5.5 s (~360 files/s) | per-line regex over the whole template and all 12 includes, per realization; redundant path syscalls |
| queue via pueue CLI | **129 s (15 tasks/s)** | one process + client handshake per task; daemon rewrites its entire state file on every add |

Profiling detail for the generation phase (cProfile, same workload): 1.6 M
calls to `_replace_tokens_in_text` / regex `sub` + 1.5 M `finditer` calls —
the entire template and every include file are re-scanned line by line for
every realization; 14 k `makedirs`, 24 k `stat`, 34 k `abspath`, 16 k
`relpath` calls — several filesystem round trips per realization that don't
depend on the sample.

Queuing detail: with a real pueue 4.0.4 daemon, `pueue add` measured
~65 ms/task effective (4 parallel workers). Two structural causes, verified
against pueue's source and by direct measurement:

1. **Per-add client cost.** Every task spawns a `pueue` process that
   connects and handshakes with the daemon.
2. **Per-add daemon state save.** `add_task` serializes and writes the
   *entire* task list to `state.json` synchronously on every add
   (`pueue/src/daemon/network/message_handler/add.rs`), so submission cost
   grows linearly with queue size — total time grows quadratically. The
   dominant driver of state size is that each task stores the client's
   **full environment** (~8.7 kB/task here; `state.json` reached 17 MB at
   2 000 tasks). A big queue also slows `pueue status` and therefore
   `horizon --status`.

Measured on the same daemon, same 2 000 adds: CLI 15 tasks/s; direct
protocol with full env 12 tasks/s (state save dominates); **direct protocol
with a minimal per-task environment 55 tasks/s and a 10x smaller
state.json**; Horizon-side overhead with a no-op pueue stub 1 480 tasks/s
(Horizon itself is not the bottleneck).

LHS sampling: per-scenario sampling re-runs `lhs(criterion="maximin")` —
O(n²) in samples — once per scenario combination with the same seed,
recomputing an identical matrix each time. Measured cost of one call at 17
dims: 250 samples 0.005 s, 2 000 samples 0.19 s, 5 000 samples 1.16 s —
times the number of combinations (9.3 s wasted at 5 000 x 8, and it grows
with both knobs).

## Phase 0 — correctness fixes the verification depends on

Found while building the harness; both are prerequisites, not
optimizations. Land these first, each with a regression test.

**0.1 `RandomSeed` does not reproduce LHS studies (pyDOE3 >= 1.5).**
`ParameterSampler` seeds `numpy.random.seed(...)`, but modern pyDOE3 draws
from its own `default_rng` — verified: two runs with `RandomSeed = 42`
produce different samples 4+. Fix: pass the seed to `lhs(..., seed=...)`
(and pin `pyDOE3 >= 1.5` in `pyproject.toml` so the parameter exists).
This also silently broke the documented guarantee that every scenario
combination re-uses the same draw matrix so `sample_i` aligns across
scenarios — today each combination gets a *different* random matrix.
Note for the team: after the fix, newly generated studies will not
reproduce pre-fix sampled values (those were irreproducible anyway).
Without this fix the golden-output protocol below cannot work for LHS
studies at all.

**0.2 `horizon --status` misreads pueue 4.x results.** pueue 4 reports
`status: {"Done": {"result": "Success"}}`; `_task_succeeded` checks for a
`"Success"` *key* and so classifies every successful run as failed —
verified against a live daemon (four successful Navigate runs reported as
four failures, with warning-only log tails). Fix `_task_succeeded` /
`_task_failed` to read `Done.result` (keeping the 3.x shapes), and make the
e2e smoke test assert "no failures" via `horizon --status`.

## Phase 1 — file generation

Ranked by measured impact over risk. One step per PR, gates in
[Verification protocol](#verification-protocol).

**1.1 Compile the template once; render per realization.**
Split the `.unc` once with the token regex into an alternating list of
literal segments and token slots (`re.split` on the existing `_TOKEN_RE`);
per realization, rendering is then a list copy, ~1 dict lookup per token
slot, and one `"".join`. Apply the same to each cached include file
(compile on first read; the cache and `_include_lock` already exist), which
also eliminates the per-realization `finditer` scans that decide which
tokens were replaced. Semantics that must be preserved exactly — the golden
manifest catches all of them: unknown tokens stay as `%TOKEN%`; float
formatting via `_format_value_for_template`; the rewritten include filename
is derived from the replaced tokens *in first-appearance order*
(`CONT5_CONT4_..._sample_N.inc`); include-path interpolation and the
`Include` keyword normalization are unchanged.
Measured on this workload's template: 0.23 ms → 0.007 ms per render, **33x
on the replacement CPU**, byte-identical output (verified). Expected effect
on the generate phase: ~3–5x (the remaining time is real file I/O), more on
Windows.

**1.2 Syscall diet per realization.**
One `os.makedirs` per realization (currently up to 7: `simulation_includes`
unconditionally plus one inside every `_write_to_file`); skip creating
`simulation_includes/` when the template has no tokenized includes (today
every realization gets one, often empty — note: visible layout change,
flag it in the changelog); precompute the include-file relative paths once
per run instead of `os.path.relpath` per include per realization (16 k
calls); drop `os.path.abspath` in `generate_commands_list` (paths are
already absolute — 34 k calls); write the `.nav` with a single
`fh.write("".join(...))`. Expected: removes most of the remaining
non-write syscall time; on network filesystems and Windows this is the
larger half of the win.

**1.3 Reuse the LHS draw matrix across scenario combinations.**
Compute the `(remaining, dim)` matrix once per `create_files` run and reuse
it for every combination (after 0.1 this is also what the documented
alignment semantics require — one matrix, mapped through each scenario's
resolved parameter bounds). Implementation: hoist the draw out of
`sample_latin_hypercube` or cache keyed on `(dim, n, seed)`; same for the
MC uniform matrix. Measured saving: (combinations − 1) x the per-call cost
above — seconds to tens of seconds for big studies, and it turns the
sampling phase from O(combos x n²) into O(n²).

**1.4 Cache the include/exclude decision per scenario combination.**
In pre-resolved mode `should_skip_combination` and the label lookup run per
*sample* (N x combos times); the inputs only vary per combination. Cache by
the combination's value tuple. Small but free.

**1.5 Re-tune generation parallelism (measure, then decide).**
After 1.1–1.2 the per-file work is almost pure I/O, so the current
`max_workers = min(8, cpu_count)` cap and the GIL stop mattering; sweep
8/16/32 workers with the harness on Linux and on a real Windows workstation
before changing anything. A `ProcessPoolExecutor` is the fallback if CPU
still dominates somewhere — measure first; expected unnecessary after 1.1.

## Phase 2 — queuing

**2.1 Overlap queuing with generation (streaming submission).**
Today every task is submitted only after all files are generated
(`create_files` runs the phases strictly in sequence). Feed each command to
the submitter as its `.nav` completes (bounded queue between the generation
pool and the existing submission pool; same labels, same priority).
Effects: total wall time becomes max(generate, queue) instead of the sum,
and the first Navigate runs start seconds after `horizon` is invoked
instead of after the full queue is built — on the baseline study that moves
first-solver-start from ~135 s to ~1 s. Failure semantics shift slightly
(a mid-generation crash leaves earlier tasks already queued); today's
partial-queue failures behave the same way, but call it out in review.

**2.2 Send a minimal per-task environment.**
The daemon's per-add state save is driven by env payload (measured above:
10x smaller `state.json`, 15 → 55 tasks/s worst-case path). Two layers:
- CLI path: run `pueue add` with a trimmed `subprocess.run(..., env=...)`
  whitelist (PATH, HOME, LANG/LC_ALL, LD_LIBRARY_PATH, VIRTUAL_ENV,
  CONDA_* , TMPDIR, solver licensing such as GRB_LICENSE_FILE, plus the
  OMP/MKL/NUMEXPR vars Horizon sets) — the client forwards *its* env, so
  trimming the client env trims the task env.
- Direct-protocol path (2.3): set the `envs` field explicitly.
Ship with an escape hatch (`--full-task-env`) and an additive
`HORIZON_TASK_ENV=VAR1,VAR2` passthrough for cluster setups (module
systems, proxies). The e2e smoke test must pass under the minimal env —
that is the gate proving Navigate still finds its solver and assumptions.

**2.3 Talk to the pueue daemon directly (one connection, N adds).**
Validated by spike against pueue 4.0.4: unix socket, 8-byte big-endian
length framing, shared-secret handshake, CBOR-encoded `Request`/`Response`
(`{"Add": {command, path, envs, ...}}`), reading the daemon version from
the handshake reply. Eliminates the per-task process spawn and client
handshake entirely and gives exact control of `envs` and the task `path`.
Measured: 2 000 adds in 8.7 s (230/s) with small envs vs 129 s via CLI.
Guardrails that make this safe to ship: feature-detect from the handshake's
version string and **fall back to the CLI path on any error** (unsupported
version, protocol mismatch, mid-stream failure — resubmit the remainder via
CLI; adds are idempotent-by-position since each realization label appears
once). Windows uses TCP + TLS against the daemon cert — implement after the
unix-socket path, keeping CLI fallback in the meantime. Adds a small
dependency for CBOR (`cbor2`) — or vendor a ~100-line encoder for the one
message shape. This is the one step with real integration risk; it is also
the only way to remove the per-task client cost, which no amount of
Horizon-side threading can hide on Windows (process spawn ~30–80 ms there).

**2.4 Queue hygiene and study groups (UX, enables the above to stay fast).**
Since every add rewrites the whole state, thousands of finished tasks from
last week's study slow this week's submission and every `pueue status`.
Document `pueue clean` in the workflow docs, and consider an optional
dedicated pueue group per study (`--group <study>`): isolates parallelism
control per study, makes `horizon --status` filter precisely, and `pueue
clean -g <study>` becomes safe housekeeping. No throughput effect by
itself.

Expected combined queue effect on the baseline machine: 129 s → under 10 s
for 2 000 tasks (2.2 + 2.3), and hidden behind generation entirely once 2.1
lands. On Windows the relative gain from 2.3 is larger.

## Phase 3 — only if the numbers still demand it

- **Shared include store**: rewritten `.inc` files that are identical
  across realizations (e.g. scenario-only tokens: same content for all N
  samples of a combination) currently get written N times into N folders.
  Deduplicating into a shared per-combination folder changes the on-disk
  layout users see and copy around — needs a team decision, not just a
  benchmark.
- **ProcessPoolExecutor for generation** — only if a real workload still
  shows CPU-bound generation after 1.1 (see 1.5).
- **Batching several Navigate runs per pueue task** — rejected for now: it
  destroys per-realization labels, `pueue status` readability, and
  `horizon --status` failure attribution.

## Verification protocol

One optimization per PR, in the phase order above. Each PR must pass four
gates; record gate C's numbers in the PR description.

**Gate A — unit tests.** `pytest` green (393 tests today). Every Phase-0
fix and every behavior-adjacent step (1.1's compiled rendering, 2.2's env
whitelist, 2.3's protocol client with a mocked socket) adds its own tests.

**Gate B — golden output equivalence.** Byte-identical generated files
against the baseline commit, on three study shapes: `--preset small`
(legacy sampling path: no overrides), `--preset medium` (per-scenario
sampling with overrides), and a variant with a tokenized include *path*.
Queue submissions compared as an unordered set (stub log). Mechanics in
[`tools/benchmark/README.md`](../tools/benchmark/README.md). Steps that
intentionally change bytes or layout (0.1 changes sampled values; 1.2 may
drop empty `simulation_includes/`; 2.2/2.3 change task envs) must say so in
the PR and re-baseline the golden snapshot — an unexplained diff is a
failed gate.

**Gate C — benchmark delta.** `bench_generation.py` (medium preset) and
`bench_queue.py 2000` (stub *and* real paused daemon), before/after on the
same machine. A perf PR that doesn't move its target number doesn't merge;
no step may regress the other phase's number.

**Gate D — real-Navigate smoke test.** `make_e2e_study.py` study (four real
runs, ~10 s each with HiGHS): all pueue tasks `Success`, `horizon --status`
reports no failures, four `report/*_output.xlsx` produced, `horizon -c`
collects them. Mandatory for every Phase-2 step and for 1.1/1.2 (they touch
what Navigate consumes); recommended for the rest. This lane is what caught
0.2 — it exercises the seams unit tests can't.

Suggested tracking table per PR:

| Step | generate (s, medium) | queue 2 000 stub (s) | queue 2 000 real (s) | Gates |
|------|---------------------|----------------------|----------------------|-------|
| baseline `<sha>` | 5.5 | 1.4 | 129 | — |
| 1.1 compiled templates | | | | A B C D |
| ... | | | | |

## Landing order

```
0.1 LHS seed fix ──> 0.2 --status fix ──> [golden baselines recorded]
        │
        ▼
1.1 compiled templates ──> 1.2 syscall diet ──> 1.3 LHS reuse ──> 1.4 rule cache ──> 1.5 pool tuning (measure)
        │
        ▼
2.1 streaming submission ──> 2.2 minimal env ──> 2.3 direct protocol (unix socket, CLI fallback) ──> 2.4 hygiene/groups
```

Phase 0 first (0.1 unlocks golden checks for LHS; 0.2 unlocks the smoke
test's pass criterion). Phase 1 before Phase 2 only because its gates are
cheapest; 2.1 is independent of 1.x and can land in parallel if two people
work on this. 2.3 lands last of the queue steps because 2.1 + 2.2 may
already make queuing invisible next to generation on Linux — decide with
gate C numbers from a Windows workstation before spending the protocol
work.
