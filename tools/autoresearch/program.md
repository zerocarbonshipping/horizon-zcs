<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# autoresearch for Horizon

Adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch):
an agent edits the code, runs one fixed evaluation, keeps the change if the
score improved and reverts it otherwise, and logs every attempt. Here the
"model" is the Horizon codebase and the score is *simpler and faster with
identical output*.

## The evaluation (`eval.sh`)

One command, one TSV line: `status  loc  import_s  gen_s  imo_s`.

Gates (any failure means discard):

1. `pytest` passes.
2. `flake8` and `isort --check-only` pass.
3. Two fixture studies run through the real CLI produce **byte-identical**
   files and the same set of queued commands as the golden snapshot:
   a synthetic 2 000-realization study (`make_study.py --preset medium`)
   and the production IMO study (`simulations/production/2026_imo/october`)
   at 10 samples per scenario.

Metrics (lower is better):

- `loc`: non-blank, non-comment lines in `horizon/`.
- `import_s`: best-of-3 time to `import horizon.__main__` (CLI startup).
- `gen_s`: best-of-3 wall time of `horizon study.hor` on the synthetic study.
- `imo_s`: best-of-3 wall time of the full production IMO study (10 policies x
  300 samples, about 54 000 files). Round 1 showed why it is needed: the
  synthetic study has few includes per realization and hid a 10 % per-file
  regression that the real study exposed.

Fixture setup (once per machine, from a checkout of the baseline commit):

```bash
python tools/benchmark/make_study.py $AR_FIXTURES/synth --preset medium --fresh
cp -r <simulations>/production/2026_imo $AR_FIXTURES/imo   # set NumberOfSamples = 10
cp -r <simulations>/production/2026_imo $AR_FIXTURES/imo_full   # unchanged, timing only
mkdir $AR_FIXTURES/stubbin && cp tools/benchmark/pueue-stub $AR_FIXTURES/stubbin/pueue
tools/autoresearch/eval.sh --golden
```

## The loop

1. Read the files you own. Form one hypothesis: "X can be deleted /
   merged / written more directly", or "Y is slow because Z".
2. Make that one change. Small and self-contained.
3. Run `tools/autoresearch/eval.sh`.
4. **Keep** (commit, one commit per experiment, message says what and why)
   if every gate passes and either:
   - `loc` went down and no timing metric regressed beyond noise
     (about 5 %), or
   - a timing metric improved clearly and `loc` grew only a little.
   Otherwise **discard** (`git checkout -- . && git clean -fd horizon tests`).
5. Append a line to `tools/autoresearch/results/<track>.tsv`:
   `commit  status  loc  import_s  gen_s  imo_s  kept|discarded  description`
   (commit is `-` for discarded attempts). Commit the log with each kept change.
6. Repeat. Stop when ideas run out or several attempts in a row fail.

## Simplicity criterion

All else equal, simpler wins. A tiny speed-up that adds hacky complexity is
not worth it. Deleting code and getting identical output is always a win.
Prefer the obvious, direct way of writing something over the clever one.
Good targets: dead code, duplicated helpers, needless classes and
indirection, defensive checks the parser already guarantees, hand-rolled
versions of stdlib functions, heavy imports that only some modes need.

## Rules

- Never change what Horizon writes or queues; the golden gate enforces it
  for generation, and for analysis modules (`-c`, sensitivity, calibration,
  plots) the tests are the gate - do not weaken them.
- Keep the CLI flags and the `.hor` format backwards compatible. If you think
  a flag or feature should go, list it in your report instead.
- Tests may change only where they target an internal helper you removed or
  merged; keep the behaviour they check covered.
- Edit only the files your track owns.
- No new dependencies.
