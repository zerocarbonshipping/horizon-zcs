<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Next round of .hor decluttering, on top of the short format

Status: proposal (autoresearch track 2). Not implemented.
Basis: `simulations` main @ 4741d35 (`october/run.hor`, 212 lines) and `horizon-zcs` branch
`claude/festive-ride-39tpup` (origin/dev with PR #18/#19: new parser, `docs/configuration.md`, CHANGELOG).
This replaces my first review, which was written against the old 421-line format.

## 1. Summary

The short format removed most of the redundancy (421 -> 212 lines). What is left falls into four groups:
1. Still-repeated lines: 53 `low_val/mid_val/high_val` lines (3 per block), 17 `emissions_low` lines of which 9 are the default,
   an 8-line format-notes header that every file would need, 13 lines of Horizon-block boilerplate.
2. Implicit defaults that are invisible at the point of use: 14 of 16 continuous blocks never state their distribution
   (default LOG-TRIANGULAR), and the comments sometimes disagree with it ("Triangular with mode..." above a log-triangular block).
3. Parser hazards that still reproduce: typo'd keys are silently ignored, discrete without `probabilities` crashes,
   `if` inside a quoted string breaks parsing, override values and conditions are not validated.
   Fixed by the new parser: inline-comment `if`, base `mid_val` leak, `}` in a comment.
4. One new compatibility trap: a legacy file (with `Parameters = [...]` lists) and no `distribution` is now silently
   sampled log-triangular instead of uniform.

Proposed next step: an optional compact distribution call, a few more defaults, legacy-default pinning,
`= (low, mid, high)` / `= base` one-line overrides, strict key and override validation, and `horizon check / fmt / --explain`.
Rewritten october/run.hor: **103 lines vs 212** (code lines 128 -> 37). Sections 2-3 measure the current state, section 5 has the
proposals, section 7 the full file. The rewrite was verified identical to the current file (resolved parameters, emissions samples, drawn samples) for all 10 policies.

## 2. Already done (credit, not re-proposed)

| My earlier proposal | Status in short format (parser on origin/dev) |
|---|---|
| `token` defaults to block id | done; invalid characters give a clear error |
| `name` optional | done (defaults to block name) |
| `active = TRUE` is default | already was; docs/examples no longer write it |
| `default` = `mid_val` (also inside `if`) | done; no mid and no default is an error |
| Drop `Parameters = [...]` list, one place to enable a block | done; lists still read as legacy with a deprecation warning |
| Duplicate block names | now an error |
| Grouped overrides `if policy in (...)` | used in the file; docs show it |
| Automatic `decimals` | done (step <= 1/10,000 of range, 4 significant digits for log) |
| Discrete without `default` | now an error instead of an empty value |
| Distribution keywords, unknown name = error | done (`TRIANGULAR`, `LOG-UNIFORM` ...), highlighted in UDL and VS Code extension |
| Tabs/spaces mix | file is now consistently tab-indented (97 lines, 0 space, 0 mixed) |
| Trailing `#` comments | now stripped everywhere except inside quotes |
| Base `mid_val` taken from an override | fixed (`_base_content`) |

## 3. What is left in the 212-line file

Totals: 212 lines = 128 code, 53 comment-only, 31 blank. 18 blocks (1 scenario, 16 continuous, 1 discrete); 17 parameters.

| Clutter | Count | Where it hurts |
|---|---|---|
| `low_val` / `mid_val` / `high_val` lines | 53 (47 base + 6 in overrides) | 3 lines + header + closer = 5-6 lines per block for one distribution; 18 header and 18 closing-brace lines |
| `emissions_low = ...` | 17 (8 `MAXIMUM`, 9 `MINIMUM`) | `MINIMUM` is the default: 9 lines carry no information. The 8 `MAXIMUM` do |
| Explicit `default` | 5 | 3 are real (two shares where the research base case differs from the mode, plant_lead_time tie); `epc_interpolator` 0.5 is the midpoint of a UNIFORM and the scenario default is the first value, both inferable |
| Explicit `distribution` | 2 of 16 continuous (`TRIANGULAR`, `UNIFORM`) | the other 14 continuous blocks are implicit LOG-TRIANGULAR. Block-local reading cannot tell the distribution without the header |
| Format-notes header | 8 comment lines + 1 blank | 6 rules that are semantics of the tool, copied into the file; will drift from the docs and from the installed horizon version. Every study file would need its own copy |
| Horizon block | 13 lines | `SamplingMethod = LHS` and `RandomSeed = 69` are the defaults, `UncFilePath = ./run.unc` / `OutputPath = ./samples/samples.csv` are boilerplate, 7 comment lines restate docs |
| Empty override block | 1 block, 3 code lines + comment | `if policy in (<6 names, 201 chars>) { }`: a deliberate coverage assertion ("these use the base"), but expressed as an empty block; a misspelled name is silently ignored |
| Over-long lines | 2 lines of 201 chars (`values = [...]` at line 26, the `in (...)` at line 53) | multi-line lists already parse (tested), nothing wraps them |
| Comment vs code drift | 3 cases | biogas share "Triangular with mode..." and gasification "Triangular with mode at 0.08" sit on log-triangular blocks; `run.unc` line 81 still says "beta_multiplier is active = FALSE in run.hor" but the block was deleted; README "switched off in both .hor and .unc" |
| Parked parameters | 2 deleted (beta, fossil) | ranges and rationale now exist only in git history; the format offers `active = FALSE` (needs a default and keeps the token substituted) or commenting out 12 lines |
| Indentation style | 0 mixed; Horizon block is tab-aligned ragged | cosmetic; needs `fmt` rather than rules |
| `run.unc` side (unchanged) | 21 commented `Include`/`Load` of 60 | toggling still needs `.unc` edits that the `.hor` does not know about |
| Encoding | 6 lines contain non-ASCII (em dash, arrows) | `parse_hor_file` uses `open(path, 'r')` without `encoding`; on a Windows locale this decodes as cp1252. Cheap fix: `encoding="utf-8"` |

## 4. Hazards: which still reproduce (new parser, tested with scratch files)

| Hazard from review 1 | Now | Evidence |
|---|---|---|
| Inline comment containing `if` breaks parsing | **fixed** | `mid_val = 0.15 # round if needed` parses |
| `if` inside a quoted string (`name = "Cost if any"`) | **still reproduces** | `ValueError: Malformed 'if' override: missing '{'`. `\bif\b` is searched in the whole block, strings included |
| Base `mid_val` leaks from an override | **fixed** | base without mid, `if` with mid: `no mid_val and no default` error, as it should |
| Unknown / typo'd keys silently ignored | **still reproduces** | `hgh_val`, `emisions_low` (direction lost, defaults to MINIMUM), `hih_val` inside `if`: no message, resolved values unchanged |
| Discrete without `probabilities` (docs: uniform) | **still reproduces** | `AttributeError: 'NoneType' object has no attribute 'group'` |
| `}` in a Horizon-block comment | **fixed** (comments are stripped first) | |
| Omitted `default` becomes `None` silently | **fixed** for continuous and discrete | errors with "Set default explicitly" |
| `default` is a string | still | explicit `default = 5` is stored as `'5'`, no range check |
| No validation of overrides | **still** | `if` with `low_val = 0.5` and base `high_val = 0.2` is accepted; override `mid_val = 0.9` outside [0.1, 0.2] accepted (and becomes the default); base `mid_val = 0.5` above `high_val = 0.2` accepted; `default = 5` outside range accepted |
| Condition typos | **still** | `if p in (x, zz)` (zz not a value) and `if q = x` (q is not a scenario) are silently no-ops. Include/Exclude do warn on this; `if` does not |
| New: legacy files change meaning | **new** | a file with `ContinuousParameter("A")` in the Horizon block, no `distribution`, `low=0.8`, `high=1.5`, `default=1.0` now samples `log-triangular` (it was `uniform`); the only message is the generic list deprecation warning |

## 5. Proposals

Ordered by value per effort. "Compat" always means: old and current-short files keep parsing to the same model.

### N0. No code change: trim what the format already allows (212 -> about 195)
- Delete the 9 `emissions_low = MINIMUM` lines (verified: omitted gives low = minimum, high = maximum; same emissions samples).
- Wrap the two 201-char lines (multi-line `values = [...]` and `in (...)` parse today).
- Horizon block: drop `SamplingMethod = LHS`, the inline restatements and the 3 boilerplate comments.
- Fix the stale `run.unc` line 81 comment and the README sentence; correct the two "Triangular" comments.
- Move the 8-line notes to `docs/configuration.md` / `simulations/CLAUDE.md` (they are already there in part).
Agent value: fewer lines to keep consistent; no change to results.

### N1. Compact distribution call (largest remaining win: about -45 lines)
Before (5-6 lines per block, 47 lines for the bounds alone):
```
ContinuousParameter "vessel_wacc_multiplier" {
	low_val      = 0.6
	mid_val      = 1.0
	high_val     = 1.8
	emissions_low = MINIMUM
}
```
After (1 line; keyword mandatory in this form, so the distribution is visible where it is defined):
```
ContinuousParameter "vessel_wacc_multiplier" = LOG-TRIANGULAR(0.6, 1.0, 1.8)
ContinuousParameter "epc_interpolator" = UNIFORM(0.0, 1.0) { emissions_low = MAXIMUM }
DiscreteParameter "plant_lead_time" = DISCRETE([3, 4, 5, 6], [0.15, 0.35, 0.35, 0.15]) { default = 4 }
```
**Both forms stay valid, per block, in the same file.** The one-line call is an alternative
spelling of `low_val`/`mid_val`/`high_val`/`distribution` (or `values`/`probabilities`); it parses to
the same parameter. Users who prefer the current block form keep writing it; `horizon fmt` can convert
either way. Only mixing the two *inside one block* (a call plus `low_val = ...`) is an error.

`emissions_low`, `default`, `active`, `decimals` and `if` overrides are not distribution data, so they
stay attributes, in an optional `{ ... }` after the call: on the same line when short
(`{ emissions_low = MAXIMUM }`), as a normal block when there are several or there are overrides
(see `operational_ee` in section 8). A parameter with only defaults is just the call. Rejected
alternatives: `emissions_low` as a fourth call argument (mixes sampling and reporting concerns and
breaks the fixed arity check) and a sign shorthand such as `-LOG-TRIANGULAR(...)` (cryptic for
humans and agents). In the October study 8 of 17 parameters carry `emissions_low = MAXIMUM`.

Grammar: `Kind "id" [= DIST(args)] [{ attrs }]`; args are `low, high` (UNIFORM, LOG-UNIFORM), `low, mode, high` (the triangulars)
or `values[, probabilities]` (DISCRETE). Attributes inside `{ }` are separated by newline or comma.
Removes: 53 -> 0 bound lines, the 14 implicit-distribution blocks now state theirs, comment/code drift becomes visible.
Compat: additive; `low_val/mid_val/high_val/distribution` stay valid and keep the LOG-TRIANGULAR default in the long form.
Mixing the call form and the long keys in one block is an error that names the line to remove. `check` can print the
long form (`--explain`), `fmt --compact` / `migrate` converts. Agent value: one place to edit a range, the arity is
checked by the parser (a missing mode for TRIANGULAR is an error instead of an inferred mean).

### N2. More defaults (round 2)
| Field | New default | Lines in october |
|---|---|---|
| `ScenarioParameter` `default` | first value | 1 |
| `default` for `UNIFORM` / `LOG-UNIFORM` | midpoint / geometric mean | 1 (epc) |
| `UncFilePath` | `<hor stem>.unc` (here `run.unc`) | 1 |
| `OutputPath` | `./samples/samples.csv` | 1 |
| `DiscreteParameter` `probabilities` | uniform (docs already say so; currently a crash) | bug fix |
Keep required: `NumberOfSamples` (it multiplies run cost), discrete `default` (ties like [0.35, 0.35] are ambiguous).
Compat: only turns errors or boilerplate into defaults. Log the inferred value in `--explain`.

### N3. Pin the legacy default (no version marker)
The format-notes header is temporary while the format evolves, so no `Format` key is proposed; the
rules live in `docs/configuration.md`.
One compatibility trap still needs a fix: a legacy file (parameter lists in the Horizon block) without
`distribution` is now sampled LOG-TRIANGULAR instead of UNIFORM. The parser already detects legacy
files (the lists trigger the deprecation warning), so: keep UNIFORM as the missing-distribution default
when the legacy lists are present, add a regression test with the legacy sample from section 4, and
name the remedy in the deprecation warning (`distribution = UNIFORM`, or remove the lists to get the
new default). Longer term: `check` warns when `distribution` is omitted and the block has a `default`
but no `mid_val` (the case where the default distribution silently decides the shape).

### N4. One-line overrides, `= base`, validated conditions
Before (12 lines, one 201-char):
```
	if policy in (bau, liberia, liberia_s2) {
		low_val  = 0.00
		mid_val  = 0.05
		high_val = 0.10
	}
	...
	# Real GFS-style compliance pressure: these use the base values above.
	if policy in (nzf_as_is_disallow, nzf_bracketed_disallow, nzf_tuvalu, ..., landing_zone_pessimistic) {
	}
```
After:
```
	if policy in (bau, liberia, liberia_s2) = (0.00, 0.05, 0.10)
	if policy = nzf_tuvalu                  = (0.15, 0.20, 0.25)
	# Real GFS-style compliance pressure: these use the base values.
	if policy in (nzf_as_is_disallow, nzf_bracketed_disallow, japan_s1_disallow,
	              japan_s2_disallow, landing_zone_optimistic, landing_zone_pessimistic) = base
```
- `= (low, mid, high)` replaces the bounds and keeps the base distribution; `= DIST(...)` changes it; `= base` is an explicit "no change"
  (replaces the empty block). The block form stays for overriding `default`, `emissions_*` or one field.
- Validation: every name in `in (...)` must be a value of the scenario parameter and the token must be a defined scenario parameter
  (error, with the list of valid values; both are silent today); coverage: if a parameter has any `if` on `policy`, each policy value
  must be matched by one `if` or `= base`, otherwise warn "policy 'x' has no rule for operational_ee (add it to an `if` or `= base`)".
  This turns the author's empty-block assertion into something a tool can check when a policy is added (today adding a policy value
  silently uses base).
Compat: additive; empty blocks keep working and count as `= base`.

### N5. Strict parser: unknown keys, scoped `if`, range checks (retrofit now, tokenizer later)
Without rewriting the regex scan:
- Collect `key =` tokens in the base view and in every `if` body; any key outside the per-kind whitelist is an error with a
  suggestion: `hgh_val: unknown key in ContinuousParameter 'a' (line 14). Did you mean 'high_val'?` (also `emisions_low`).
- Find `if` only at the start of a (stripped) line and outside quotes: removes the `name = "Cost if any"` crash.
- After resolving each override: `low < high`, `low <= mid <= high` for triangulars, `default` within range (base and every override),
  numeric `default` stored as a number. Error messages name the parameter and the scenario value.
- Discrete: `probabilities` omitted means uniform; lengths must match `values`.
- Read the file as UTF-8.
Agent value: this is where "silently wrong study" comes from: a typo in a 3000-run job is caught at parse time. A tokenizer with line:col
errors (my first review's P6) is still the right end state, but N5 gives most of the value in about a day.
Compat: files that parse today and have only valid keys are unaffected; files with typos start failing (that is the point; run `check` first).

### N6. `horizon check`, `--explain`, `fmt`, `--dump-json`
With implicit defaults and automatic decimals the file no longer shows what will be sampled, so a resolved view is the missing piece.
- `horizon check run.hor [--json]`: parse + N4/N5 validation + cross-file checks (tokens in `.unc` and included `.inc` vs blocks, both
  directions, commented includes ignored; every scenario value has `policy_<value>.inc` with exact case, which is a manual CLAUDE.md rule
  today; `.unc` comments that name a `.hor` state). Exit code 0/1; warnings promoted to errors with `--strict`.
- `horizon check --explain` prints the resolved model; with real values from the current october file:
```
parameter                         scenario                  dist            low   mode   high default dec
operational_ee                    bau                       triangular      0.0   0.05    0.1    0.05   5   (override: policy in (bau, liberia, liberia_s2))
operational_ee                    nzf_tuvalu                triangular     0.15    0.2   0.25     0.2   5   (override: policy = nzf_tuvalu)
operational_ee                    landing_zone_pessimistic  triangular      0.1   0.15    0.2    0.15   5   (= base)
vessel_wacc_multiplier            *                         log-triangular  0.6    1.0    1.8     1.0   4   (default = mid)
feedstock_bioethanol_fermentation_share_shipping *          log-triangular 0.001   0.03   0.12    0.02   6   (default explicit)
epc_interpolator                  *                         uniform         0.0      -    1.0     0.5   4   (default = midpoint)
```
  (columns are the current parser's resolved values; the annotations are the proposed addition).
- `horizon fmt run.hor`: idempotent; one indentation style, aligned `=`, wrapped long lists, one blank line between blocks, drops
  defaults (`emissions_low = MINIMUM`, `active = TRUE`, `token` equal to the name), stable key order. Ends cosmetic diffs and agent churn.
- `horizon migrate --verify old.hor new.hor`: compares resolved parameters and emissions samples per scenario value (the check I ran by hand for this review).
- `horizon --dump-json` / `horizon schema`: the resolved model and its JSON Schema for tooling and agents. A schema describes the model,
  not the raw syntax.
- Proposed message wording, each with the fix in it:
  - `run.hor:41:2 error: unknown key 'hgh_val' in ContinuousParameter "a". Did you mean 'high_val'?`
  - `run.hor:53 error: 'landing_zone_optimistc' is not a value of ScenarioParameter "policy" (values: nzf_as_is_disallow, ...).`
  - `warning: policy 'japan_s3' has no rule for operational_ee; add it to an 'if' or '= base'.`
  - `error: 0_includes_local/policy/policy_bau.inc not found for policy = 'BAU' (case differs from 'bau').`
Agent value: loop of seconds instead of the current "dry-run, then a 1-sample run, then read a log for a few simulated years".

### N7. Smoke-test without editing the file: `--samples N`
README: "Set it to 1 for a smoke test" and CLAUDE.md requires a 1-sample run before committing: both mean editing `NumberOfSamples`
and restoring it. There is no CLI override today (`__main__.py` has none). `horizon run.hor --samples 1 --dry-run` keeps the committed
300 intact and removes the "forgot to restore the value" class of mistakes. Compat: additive flag.

### N8. Parked parameters: `skip = TRUE` (optional)
Today parameters are either in the study, pinned (`active = FALSE`: needs a `default`, token still substituted), or deleted/commented out
(the beta and fossil blocks are gone from the file; their ranges and rationale now live only in git history). A one-line
`skip = TRUE` would keep the block (validated, documented) but leave it out of the study and the samples CSV. `check` then flags a skipped
parameter whose `.inc` is still included. Only worth it if parked parameters recur; otherwise deleting is fine. Do not add it together with
another on/off flag with unclear semantics: the difference must be documented as "pinned" vs "not in the study".

### N9. Comments vs structured text
Free-text `#` comments carry research notes and sources (53 lines); keep them. Only one structured field is worth adding: optional
`unit = "USD/dry ton"` (units live today in comments and in the old `name` strings), shown by `--explain` and plot axes. No `description`:
a second place for prose invites drift (3 drifts found, section 3). Rule for agents: comments for why and sources, attributes for facts a tool can use,
never restate state ("off", "Log-triangular") that the block already carries.

### N10. Later, only if it recurs: scenario groups
If more parameters become policy-dependent, define groups once (`groups = { weak = [bau, liberia, liberia_s2] }` on the ScenarioParameter,
`if policy in weak`). With one policy-dependent parameter (1 of 17) the cost of the concept is higher than the saving, so not now.

## 6. Syntax decision (unchanged): stay with the brace syntax

The short format is the evidence: after removing redundancy the custom syntax gets a 17-parameter study into 103 lines, in the same
`Kind "name" { Attr = value }` shape as `.nav`, `.unc`, `.inc` and `.sen`, with the same highlighters (UDL, VS Code extension) and the same
function-call shape (`TRIANGULAR(...)`, `Forecast("x")`) the agents already write in `.nav`. TOML would not be shorter (overrides need
`[[...override]]` tables with `when = {...}`), YAML adds indentation and implicit-typing risks (`no`, `3` vs `3.0`) and JSON has no comments for the
research notes. Machine-readability comes from `--dump-json` and the schema, not from a second authoring format. Do not accept two formats.

## 7. Suggested order and compatibility summary

1. N0 now (no release). 2. N5 + N3 (legacy default pinning and regression test) + N2: one parser release, golden tests that every current
file (simulations october, examples/, tests/ fixtures) resolves identically. 3. N1 + N4 (grammar additions, long forms remain). 4. N6 (`check`, `--explain`, `fmt`),
N7. 5. N8-N10 on demand. Everything is additive except N5 (typos become errors) and the N3 legacy pinning (restores old behaviour).
Keep long-form keys indefinitely; `fmt --compact` and `migrate` convert on request.

## 8. Rewritten october/run.hor (next-step format)

Line count: **103 lines vs 212** (code 37 vs 128, comments 42 vs 53, blank 24 vs 31; longest line 124 vs 201 characters).
Differences from the current file, all by design: no 8-line notes (temporary; rules live in the docs), no `SamplingMethod`, `UncFilePath`, `OutputPath`
(defaults), no `default` on the scenario (first value) or on `epc_interpolator` (midpoint), no `emissions_low = MINIMUM`, ASCII arrows in comments.
All research comments are kept verbatim, including the two stale "Triangular" remarks, which should be corrected separately (they describe log-triangular blocks).
Requires N1 (call form), N2 (defaults), N4 (`= (...)`, `= base`) and unquoted scenario values. It does not parse with the current horizon-zcs.

Verification: a scratch converter turned this file into the current short syntax (applying the proposed defaults), then the current
`parse_hor_file`, `resolve_parameters_for_scenario` and `ParameterSampler` (`_emissions_sample`, `sample_group` with seed 69, 30 draws) compared the original
and converted files for each of the 10 policy values. Compared: bounds, distribution, auto decimals, default, emissions directives, emissions samples, drawn samples.
Result: 0 differences. (`pyDOE3` is not installed here, so it was stubbed; LHS was not exercised, MC draws were.) The converter encodes the proposed semantics, so this shows the
rewrite preserves the study under those rules, not that they are implemented.

```
Horizon {
    NumberOfSamples = 300        # per scenario combination: 10 policies x 300 = 3000 runs
    RandomSeed = 69
    # SampleOnly = TRUE          # stop after sampling, before file creation
}

# ------------------------- Scenario parameters ------------------------- #
ScenarioParameter "policy" = [   # default = first value
    nzf_as_is_disallow, nzf_bracketed_disallow, nzf_tuvalu, liberia, liberia_s2, bau,
    japan_s1_disallow, japan_s2_disallow, landing_zone_optimistic, landing_zone_pessimistic
]

# ------------------------- Uncertainties impacted by policy axis ------------------------- #

ContinuousParameter "operational_ee" = TRIANGULAR(0.10, 0.15, 0.20) {
    emissions_low = MAXIMUM
    # No or minimal regulatory pressure. Liberia S2: market-defined architecture.
    if policy in (bau, liberia, liberia_s2) = (0.00, 0.05, 0.10)
    if policy = nzf_tuvalu                  = (0.15, 0.20, 0.25)
    # Real GFS-style compliance pressure: these use the base values.
    if policy in (nzf_as_is_disallow, nzf_bracketed_disallow, japan_s1_disallow,
                  japan_s2_disallow, landing_zone_optimistic, landing_zone_pessimistic) = base
}

# ------------------------- Global alignment parameters ------------------------- #

# Vessel WACC multiplier on base WACC (8.89%).
# Low alignment = higher financing costs (risk premium); high alignment = favorable green finance.
# Log-triangular: symmetric treatment of halving vs doubling the base WACC.
ContinuousParameter "vessel_wacc_multiplier" = LOG-TRIANGULAR(0.6, 1.0, 1.8)

# ------------------------- Sampled parameters for biomass availability ------------------------- #

# Biogas feedstock share allocated to shipping.
# Triangular with mode from biofuel assumptions research (25% base case).
ContinuousParameter "feedstock_biogas_share_shipping" = LOG-TRIANGULAR(0.10, 0.25, 0.65) { emissions_low = MAXIMUM }

# Bioethanol fermentation feedstock share allocated to shipping.
# Research base case: 1-4% (mode ~2-3%).
ContinuousParameter "feedstock_bioethanol_fermentation_share_shipping" = LOG-TRIANGULAR(0.001, 0.03, 0.12) {
    default = 0.02
    emissions_low = MAXIMUM
}

# Biomethanol gasification feedstock share allocated to shipping.
# Research base case: 5-10% (mode ~7%). Triangular with mode at 0.08.
ContinuousParameter "feedstock_biomethanol_gasification_share_shipping" = LOG-TRIANGULAR(0.02, 0.08, 0.20) {
    default = 0.07
    emissions_low = MAXIMUM
}

# Used cooking oil share allocated to shipping.
# Research base case: 2-5% (mode ~3%).
ContinuousParameter "used_cooking_oil_shipping_share" = LOG-TRIANGULAR(0.001, 0.03, 0.12) { emissions_low = MAXIMUM }

# Biogenic CO2 share allocated to shipping.
ContinuousParameter "carbon_dioxide_biogenic_shipping_share" = LOG-TRIANGULAR(0.001, 0.10, 0.20) { emissions_low = MAXIMUM }

# ------------------------- Sampled parameters for infrastructure economy development ---------- #

# Plant capacity multiplier on base design capacity.
# Log-triangular range spans full low->high across all former MTC scenarios.
ContinuousParameter "plant_capacity_multiplier" = LOG-TRIANGULAR(0.4, 1.0, 2.5) { emissions_low = MAXIMUM }

# Plant lead time in years. Electro plants get +1 year offset on top of this.
DiscreteParameter "plant_lead_time" = DISCRETE([3, 4, 5, 6], [0.15, 0.35, 0.35, 0.15]) { default = 4 }

# Electro & blue plant WACC multiplier on base WACC (~10% declining to ~6%).
# Log-triangular: right-skewed - financing for unproven tech more likely worse than expected.
ContinuousParameter "h2_wacc_multiplier" = LOG-TRIANGULAR(0.5, 1.0, 1.8)

# ------------------------- Biomass cost parameters (scenario-independent) -------------------- #
# Costs and availability are sampled independently. Cost uncertainty exists regardless of
# availability level. All use log-triangular - right-skewed cost distributions are empirically
# well-established for commodity and process costs.

# Biogas feedstock cost (USD/dry ton): blended pool of waste streams (~$50/dry ton base case).
# 4x range (25-100) appropriate for heterogeneous waste stream.
ContinuousParameter "feedstock_biogas_cost" = LOG-TRIANGULAR(25, 50, 100)

# Fermentation feedstock cost (USD/dry tonne): crop residues (corn stover, wheat straw).
ContinuousParameter "feedstock_bioethanol_fermentation_cost" = LOG-TRIANGULAR(38, 60, 140)

# Gasification feedstock cost (USD/dry tonne): woody residues from forestry/pulp operations.
ContinuousParameter "feedstock_biomethanol_gasification_cost" = LOG-TRIANGULAR(35, 65, 120)

# Used cooking oil cost Europe (USD/ton): volatile waste commodity with documented
# $800-2000/ton swings in 2020-2024.
ContinuousParameter "used_cooking_oil_cost_europe" = LOG-TRIANGULAR(565, 1130, 2260)

# Biogenic CO2 cost (USD/ton).
ContinuousParameter "carbon_dioxide_biogenic_cost" = LOG-TRIANGULAR(30, 70, 180)

# ------------------------- Other parameters -------------------------------------------------- #

# Electricity cost multiplier applied to all regions.
# Log-triangular: cost multiplier - symmetric treatment of cost increases vs decreases.
ContinuousParameter "electricity_cost_interpolator" = LOG-TRIANGULAR(0.01, 0.50, 1.00)

# EPC scaling speed: interpolates between two capacity ramp curves defined in epc_electro_blue.inc.
# interpolator=0.0 -> slow ramp (unlimited by 2050).
# interpolator=1.0 -> fast ramp (unlimited by 2040).
ContinuousParameter "epc_interpolator" = UNIFORM(0.0, 1.0) { emissions_low = MAXIMUM }
```
