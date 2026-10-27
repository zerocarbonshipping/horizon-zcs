<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Configuration reference

A `.hor` file defines an uncertainty study. Lines starting with `#` are
comments; a `#` inside a quoted string is not. Newlines do not matter, and
commas between entries are optional, so a short block fits on one line.
Values may be quoted or bare: `distribution = UNIFORM` and
`distribution = "uniform"` are the same. Paths are taken from the folder of
the `.hor` file.

## The `.hor` file

A `.hor` file contains parameter blocks and one `Horizon` block:

```
ContinuousParameter "FUEL_PRICE" {
    low_val = 0.8
    mid_val = 1.0
    high_val = 1.5
    distribution = TRIANGULAR
}

Horizon {
    UncFilePath = "template.unc"
    OutputPath = "output"
    NumberOfSamples = 100
    SamplingMethod = LHS
    RandomSeed = 42
}
```

Every parameter block in the file takes part in the study. Use
`active = FALSE` to fix a parameter at its default; delete or comment out a
block to leave it out completely. The block name is the parameter's
`%NAME%` in the simulation files, so it uses letters, digits and `_`; two
blocks with the same name are an error, also across parameter types, and
`SCENARIO` is reserved.

### Strict keys and checks

Horizon rejects what it does not understand instead of ignoring it. Every
error names the file and line (`run.hor:41: error: ...`); the command line
prints that one line and exits 1.

- **Unknown keys** in a parameter block, in an `if` body and in the `Horizon`
  block are errors that list the valid keys:
  `unknown key 'hgh_val' in ContinuousParameter "a". Valid keys: active, default, low_val, ...`
  A key set twice in one block is an error too.
- **Ranges** are checked for the base values and for every `if` override
  after it is applied: `low_val < high_val` in the base (an override may set
  both equal to fix a value), `low_val <= mid_val <= high_val` for
  triangular distributions, `default` within `[low_val, high_val]`, and
  bounds above 0 for log distributions. The message names the parameter and
  the scenario condition. If the base `default` falls outside an override's
  bounds, set `default` inside that `if` block.
- **Conditions**: the name in an `if` must be a defined `ScenarioParameter`
  (or `SCENARIO`, the label from Include rules) and every listed value one
  of its values; the error lists the valid values.

### Parameter fields

| Field | Required | Description |
|-------|----------|-------------|
| `active` | no | `FALSE` fixes the parameter at `default`. Default `TRUE`. |
| `default` | see below | Value of `sample_1` and of an inactive parameter. For `ContinuousParameter` it defaults to `mid_val`; without a `mid_val` (as for `UNIFORM`), `default` is required. Required for `DiscreteParameter`. For `ScenarioParameter` it defaults to the first value and must be one of the values. |

### Horizon block keys

| Key | Required | Description |
|-----|----------|-------------|
| `UncFilePath` | yes | The Navigate deck with `%NAME%` placeholders. |
| `OutputPath` | yes | The study folder, e.g. `output`: `study.json` with every run and its values, and the results and logs of the runs. It cannot be the folder of the `.hor` file. |
| `NumberOfSamples` | yes | Samples per scenario combination. |
| `SamplingMethod` | no | `LHS` (default) or `MC`. |
| `RandomSeed` | no | Seed for reproducible sampling (default 69). |
| `Include(...)` / `Exclude(...)` | no | Keep or drop scenario combinations, see below. |

### Parameter types

**`ScenarioParameter`**: categorical parameters that define named scenario
branches. Every combination of active scenario parameter values becomes its
own branch of the study.

```
ScenarioParameter "POLICY" {
    default = "baseline"
    values = ["baseline", "carbon_levy"]
}
```

**`ContinuousParameter`**: numeric parameters sampled from a distribution.
`distribution` is `LOG-TRIANGULAR` (default when omitted), `TRIANGULAR`,
`LOG-UNIFORM` or `UNIFORM`, in any case. `low_val` and `high_val` bound the
range; triangular distributions also take `mid_val` (the mode). If `mid_val`
is omitted it is inferred: the arithmetic mean `(low + high) / 2` for
`TRIANGULAR`, the geometric mean `sqrt(low * high)` for `LOG-TRIANGULAR`.
Log distributions require positive bounds.

Sampled values are rounded. `decimals` is optional; without it Horizon picks
the number of decimals from the bounds:

- the rounding step is at most 1/10,000 of the range (`high_val - low_val`), and
- for log distributions, values keep 4 significant digits at `low_val`, where
  log sampling puts most of its values.

Examples: 100–200 gives 2 decimals, 0.0001–0.001 gives 8, a log-triangular
0.001–0.12 gives 6. With 0 decimals the values are whole numbers. If an `if`
block changes the bounds or distribution, the decimals are recomputed for
that scenario. Set `decimals` only to force a fixed number.

**`DiscreteParameter`**: parameters drawn from a fixed set of values, with
optional probabilities (uniform if omitted). `probabilities` must have one
entry per value; they are scaled to sum to 1:

```
DiscreteParameter "RENEWAL" {
    default = 0.05
    values = [0.03, 0.05, 0.08]
    probabilities = [0.25, 0.5, 0.25]
}
```

### Active vs. inactive parameters

Every parameter has an `active` flag. Inactive parameters (`active = FALSE`)
always use their `default` value. An inactive scenario parameter does not
expand the combinatorial space but its default still fills its `%NAME%`.

### Reserved samples

The first three samples are always deterministic:

- `sample_1`: default values
- `sample_2`: emissions-low scenario
- `sample_3`: emissions-high scenario
- `sample_4+`: random (MC) or stratified (LHS) draws

The emissions scenarios use each parameter's optional `emissions_low` /
`emissions_high` attributes, which can be numeric values or the keywords
`MINIMUM` / `MAXIMUM`. If only one is given as a keyword, the other is set to
the opposite; without either, `sample_2` takes the minimum and `sample_3`
the maximum. The minimum and maximum of discrete values compare as numbers
when every value is one.

### Conditional overrides

Parameter definitions can change per scenario using `if` blocks inside the
parameter block:

```
ContinuousParameter "FUEL_PRICE" {
    ...
    low_val = 0.8
    mid_val = 1.0
    high_val = 1.5

    if POLICY in (carbon_levy, fuel_standard) {
        low_val = 1.0
        mid_val = 1.4
        high_val = 2.0
    }
}
```

Scenarios without a matching `if` block use the base values. If the base has
no explicit `default`, an override that changes `mid_val` also moves the
default. Of several matching `if` blocks, the one with the most conditions
applies, ties going to the last.

Conditions support equality (`token = value`), membership
(`token in (a, b)`), and conjunction (`and`). Sampling runs per scenario
combination on one draw matrix, so `sample_i` of every combination comes
from the same draw.

### One-line distribution call

A parameter's distribution can also be written as a call on the header line.
This is an alternative spelling: the block form above stays valid, and both
forms can be mixed in one file, block by block.

```
ContinuousParameter "WACC" = LOG-TRIANGULAR(0.6, 1.0, 1.8)
ContinuousParameter "EPC" = UNIFORM(0.0, 1.0) { default = 0.5, emissions_low = MAXIMUM }
ContinuousParameter "FUEL_PRICE" = TRIANGULAR(0.8, 1.0, 1.5) {
    emissions_low = MAXIMUM
    if POLICY = carbon_levy {
        low_val = 1.0
        mid_val = 1.4
        high_val = 2.0
    }
}
DiscreteParameter "LEAD_TIME" = DISCRETE([3, 4, 5, 6], [0.15, 0.35, 0.35, 0.15]) { default = 4 }
DiscreteParameter "LANES" = DISCRETE([3, 4, 5]) { default = 4 }
```

- `UNIFORM(low, high)` and `LOG-UNIFORM(low, high)` take two numbers,
  `TRIANGULAR(low, mode, high)` and `LOG-TRIANGULAR(low, mode, high)` take
  three, `DISCRETE([values], [probabilities])` takes a list of values and an
  optional list of probabilities (uniform when omitted). The wrong number of
  arguments is an error.
- The call sets `low_val`, `mid_val`, `high_val` and `distribution` (or
  `values` and `probabilities`); writing the call and any of those keys in the
  same block is an error naming the line.
- Everything else (`default`, `decimals`, `active`, `emissions_low`,
  `emissions_high`, `if` overrides) goes in an optional `{ }` after the call,
  exactly as in the block form.

### Include / Exclude filters

`Include(...)` and `Exclude(...)` rules in the `Horizon` block filter which
scenario combinations run:

- No Include, no Exclude: run all combinations
- Include only: run **only** matching combinations
- Exclude only: run all **except** matching ones
- Include + Exclude: Include narrows first, then Exclude removes from that set

Both match partially: `Include(BIO = high)` matches every combination where
`BIO` is `high`, whatever the other scenario parameters are. Rules that
leave no combination are an error.

Include rules take an optional `_name` that labels the combinations they
match:

```
Include(BIO = high, ELEC = low, _name = s1)
Include(BIO = low, ELEC = high, _name = s2)
```

- `_name` labels, it does not filter; Exclude rules take none
- If any Include rule has `_name`, all must have it
- The label fills `%SCENARIO%` in the simulation files

### Run labels

Each scenario combination gets a label, which names its runs
(`<label>_sample001`, ...) and is the run Lantern shows: the Include rule's
`_name`, else the active scenario values joined by `_`, else the name of the
`.hor` file. Labels are part of file names, so they use letters, digits,
`_`, `.` and `-`, and two combinations cannot share one.

## Checking a study before running it

```bash
horizon check run.hor                     # validation + cross-file checks, exit 0 or 1
horizon run run.hor --dry-run             # the study.json it would write, nothing written or run
horizon run run.hor --samples 1           # run with one sample, file unchanged
```

`check` runs all the validation above and then reads the deck and the files
it includes (commented-out `Include` lines are skipped):

- a `%NAME%` without a parameter is an error (`SCENARIO` is built in); a
  parameter that no file uses is a warning;
- an include path with a scenario token, such as `policy_%policy%.inc`, is
  resolved for every scenario combination the study runs; a missing file is
  an error;
- the summary line ends with the number of runs, errors and warnings, and
  the exit code is 1 if there are errors.

Nothing is sampled, written or run.

## The deck and its placeholders

The deck that `UncFilePath` names is an ordinary Navigate simulation file in
which uncertain values are replaced with `%NAME%` placeholders. Each run
starts `navigate <deck> --set NAME=VALUE ...` with the run's values, and
Navigate fills in the placeholders in the deck and in every file it reads,
include paths too, before it parses them. A placeholder without a value is
an error naming its file and line. Refer to the
[Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/)
for the simulation file format itself.

## Changes from Horizon 1

Horizon 2 reads the files of Horizon 1 except for these changes:

- `token` and `name` are gone: the block name is the token. Rename a block
  whose name is not its token, and delete `name` lines.
- `MaxParallelWorkers`, `Plot` and `SampleOnly` are gone; delete them.
  Instead of `SampleOnly = TRUE`, `horizon run --dry-run` prints every run
  with its values without running it.
- `MIN` / `MAX` are spelled `MINIMUM` / `MAXIMUM`.
- `Include` and `Exclude` are written exactly so (not `include`).
- `SamplingMethod` is `LHS` or `MC`; `SENSITIVITY` and `CALIBRATION` are
  gone, as are the `.sen` files of the PRCC sensitivity analysis.
- `UncFilePath` and `OutputPath` are taken from the folder of the `.hor`
  file, not from the folder `horizon` runs in.
- `OutputPath` names the study folder (`output`), not a run list
  (`output/samples.csv`); the runs and their values are in `study.json`.
- A key set twice in one block, a scenario `default` that is not one of its
  values, and run labels that clash or cannot be file names are errors.
- Parameter lists in the `Horizon` block (`Parameters = [...]`,
  `ContinuousParameter("X")`) were already refused; they stay errors.
