<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# autoresearch round 1: results

Six agents ran the loop in [`program.md`](program.md) in parallel, one per
area, each on its own branch in its own worktree and editing only its own
files. The branches were merged one by one, and every gate was re-run after
each merge. Base: `origin/dev` at d57c2cc (short `.hor` format). Per-experiment logs are in
[`results/`](results/).

## Outcome

All gates pass on the merged branch: 506 tests, flake8/isort, `reuse lint`,
byte-identical `.nav`/include/`samples.csv` output and the same queued commands
for the synthetic 2 000-realization study and the production IMO October study.

| | dev base | after round 1 |
|---|---|---|
| `horizon/` LOC (non-blank, non-comment) | 6 790 | 5 444 (-20 %) |
| `horizon/` raw lines | 9 023 | 7 194 |
| `import horizon.__main__` / `horizon --help` | 1.38 s / 1.41 s | 0.17 s / 0.16 s |
| `horizon study.hor`, synthetic, 2 000 realizations | 5.26 s | 4.35 s (-17 %) |
| `horizon run.hor`, IMO October, 10 x 300 realizations (54 000 files) | 12.96 s | 11.61 s (-10 %) |

End-to-end CLI wall time, best of 3, 4-CPU container, nothing else running,
output on tmpfs, queue through the recording pueue stub.

**Regression caught after merging.** The merged tracks first made the IMO study
about 10 % *slower* (pure generation 10.1 s vs 11.1 s) while the synthetic
eval study showed no change. Profiling found two simplifications that cost
per-file time: `pathlib.Path(...).write_text` (about 30 us per file) and
dropping the include-relpath cache (two `getcwd` calls per include per
realization). Commit 24a1a71 restores plain `open()` writes and a 4-line
cached relpath. The eval now also times the full IMO study (`imo_s`) so the
next round cannot miss this kind of regression.

| Track | Kept commits | Discarded | Main changes |
|---|---|---|---|
| startup | 2 | 1 | lazy imports of pandas, matplotlib and pyDOE3/scipy (8x faster CLI start); dead validators removed |
| generation | 16 | 1 | one work-item generator for both modes, no include cache/lock, `executor.map`, direct command building (-270 LOC in file_handler/create_files) |
| parser | 13 | 0 | single brace matcher, flattened `create_parameter`, shared Include/Exclude rule matching, merged MC/LHS sampling loops |
| run | 8 | 1 | one pueue status helper, table-driven CBOR, shared submit logging, simpler report collection |
| analysis-sens | 8 | 1 | one PRCC result path, one aggregation helper for both loaders, one CDF/PDF implementation in plot.py (-467 LOC) |
| calibration | 10 | 0 | shared line-figure builder, flattened index loading, simpler scoring and HTML rendering (-275 raw lines) |

## Behaviour changes worth knowing

None of these changes generated files or queued commands.

- sensitivity, xlsx input: a cumulative metric was multiplied by the number of
  `difference`/`cumulative` specs sharing its key (bug). PRCC results are
  unchanged (rank-based); only the y-axis of those scatter plots changes.
- generation: a failed include write now drops that realization with an error
  instead of silently keeping the untokenised include line; the per-file
  DEBUG lines "Generated NAV file"/"Command" are gone.
- parser: override parse errors are reported before bound/decimals errors.
- A degenerate log-uniform PDF in the diagnostics returns 0 instead of raising.

## Decisions for the maintainers (not done: they change features or output)

1. Direct pueue daemon client (`pueue_client.py`, `_cbor.py`, `_DirectSubmitter`,
   about 600 lines) vs. CLI-only submission: biggest remaining cut, costs queue
   speed on large studies (55 vs 15 tasks/s measured in the performance plan).
2. `--no-shared-includes` and the network-filesystem worker probe in generation
   (about 75 LOC): keep only if someone uses them.
3. Legacy `Parameters = [...]` lists in the Horizon block and all-caps `INCLUDE`
   templates: set a removal date (about 40 LOC).
4. Dead or test-only code: `plot_sampled_parameters_histograms`,
   `is_combination_excluded`, `generate_commands_list`, the calibration
   legacy no-regulation path and study-specific `_ROUND2_GRIDS` (about 150 LOC).
5. Bugs seen but deliberately preserved: the literal `# noqa: E122` text shown
   in every calibration recommendation card; a `.sen` point metric with a
   custom label and explicit year is skipped in the collected-report loader;
   `DiscreteParameter` without `probabilities` crashes (docs say uniform).
6. `collect_reports` (`horizon -c`) has no tests; add an xlsx round-trip test
   before the next round touches it.

## Next round

Rebuild the golden snapshot (`eval.sh --golden`) from the merged head, then
run the loop again on the items above that the maintainers approve, plus
`.hor` format work from [`docs/proposals/hor-format-next.md`](../../docs/proposals/hor-format-next.md).

## Round 2 (maintainer-approved removals, strict parser, tooling)

Removed `--no-shared-includes`, the network-filesystem worker probe, legacy
`Parameters = [...]` lists (now a parse error) and any include spelling other
than `Include` (now an error), plus about 150 lines of dead code; fixed the
calibration `# noqa` text and the labelled `.sen` metric lookup. Added the
strict parser (unknown keys, ranges, `if` conditions, uniform discrete default),
one-line CLI errors, `--check`, `--explain` and `--samples N`.

Final eval: `ok 5663 0.14 4.45 12.11` (loc, import, synthetic, IMO full).
Further timing rounds are not planned: about 70 % of IMO generation time is the
kernel creating 54 000 files (one .nav plus 17 sample-specific includes per
realization). The only large lever left is writing fewer files, e.g. inlining
sample-specific include content into the .nav, which changes the output layout
and needs a maintainer decision.
