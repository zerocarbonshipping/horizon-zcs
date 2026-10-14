<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Configuration reference

Horizon reads two configuration formats: `.hor` files define an uncertainty
study, and `.sen` files configure PRCC sensitivity analysis on completed
results. Both use the same block-style syntax. Lines starting with `#` are
comments. Relative paths are resolved from the directory you run `horizon`
in, so run it from the directory containing the `.hor` file.

## The `.hor` file

A `.hor` file contains parameter blocks and one `Horizon` block that wires
them together:

```
ContinuousParameter "FUEL_PRICE" {
    low_val = 0.8
    mid_val = 1.0
    high_val = 1.5
    distribution = TRIANGULAR
}

Horizon {
    UncFilePath = "template.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 100
    SamplingMethod = LHS
    RandomSeed = 42
}
```

Every parameter block in the file takes part in the study. Use
`active = FALSE` to fix a parameter at its default; delete or comment out a
block to leave it out completely. Two blocks with the same name are an error,
also across parameter types.

Older files list parameters in the `Horizon` block
(`Parameters = [ContinuousParameter("FUEL_PRICE")]`, `ScenarioParameters = [...]`
or bare `ContinuousParameter("X")` lines). These lists are no longer
supported and raise an error: delete the list, set `active = FALSE` to pin a
parameter at its default, or remove its block. The old default distribution
was `UNIFORM`; where a block relied on it, set `distribution = UNIFORM`
explicitly (the default is now `LOG-TRIANGULAR`).

### Strict keys and checks

The parser rejects what it does not understand instead of ignoring it. Every
error names the file and line (`run.hor:41: error: ...`); the command line
prints that one line and exits 1.

- **Unknown keys** in a parameter block, in an `if` body and in the `Horizon`
  block are errors, with a suggestion when one is close:
  `unknown key 'hgh_val' in ContinuousParameter "a". Did you mean 'high_val'?`
  Keys by block: all parameters take `token`, `name`, `active`, `default`;
  `ScenarioParameter` adds `values`; `ContinuousParameter` adds `low_val`,
  `mid_val`, `high_val`, `distribution`, `decimals`, `emissions_low`,
  `emissions_high`; `DiscreteParameter` adds `values`, `probabilities`,
  `emissions_low`, `emissions_high`. An `if` body takes the same keys except
  `token`; the `Horizon` block takes the keys in the table below.
- **`if` blocks** are recognised only at the start of a line and never inside
  quoted text, so `name = "Cost if any"` is a plain name.
- **Ranges** are checked for the base values and for every `if` override
  after it is applied: `low_val <= high_val` (an override may set both equal
  to fix a value; the base needs `low_val < high_val`), `low_val <= mid_val
  <= high_val` for triangular distributions, and `default` within
  `[low_val, high_val]`. The message names the parameter and the scenario
  condition. If the base `default` falls outside an override's bounds, set
  `default` inside that `if` block.
- **Conditions**: the token in an `if` must be a defined `ScenarioParameter`
  (or `SCENARIO`, the label from Include rules) and every listed value one
  of its values; the error lists the valid values.

### Shared parameter fields

| Field | Required | Description |
|-------|----------|-------------|
| `token` | no | Placeholder name in the `.unc` template (`%TOKEN%`). Defaults to the block name, which must then use only letters, digits and `_`. |
| `name` | no | Display name. Defaults to the block name. |
| `active` | no | `FALSE` fixes the parameter at `default`. Default `TRUE`. |
| `default` | see below | Value for `sample_1` and for inactive parameters. For `ContinuousParameter` it defaults to `mid_val`; if there is no `mid_val`, `default` is required. Required for `DiscreteParameter`. |

### Horizon block keys

| Key | Required | Description |
|-----|----------|-------------|
| `UncFilePath` | yes | Path to the `.unc` template (a Navigate simulation file with `%TOKEN%` placeholders). |
| `OutputPath` | yes | Path of the sampled-parameters CSV to write, e.g. `output/samples.csv`. Must include a directory component; the directory is created if missing. |
| `NumberOfSamples` | yes | Number of samples to generate. |
| `SamplingMethod` | no | `LHS` (default), `MC`, `SENSITIVITY`, or `CALIBRATION`. |
| `RandomSeed` | no | Seed for reproducible sampling. |
| `SampleOnly` | no | `TRUE` stops after sampling and diagnostics — no `.nav` files are generated and nothing is queued. Default `FALSE`. |
| `Include(...)` / `Exclude(...)` | no | Whitelist / blacklist scenario combinations, see below. |
| `MaxParallelWorkers` | deprecated | Ignored; pueue handles parallelism natively. |
| `Plot` | deprecated | Ignored; use Navigate's on-demand plotting instead. |

### Parameter types

**`ScenarioParameter`** — categorical parameters that define named scenario
branches. Every combination of active scenario parameter values becomes its
own branch of the study.

```
ScenarioParameter "POLICY" {
    name = "Policy scenario"
    default = "baseline"
    values = ["baseline", "carbon_levy"]
}
```

**`ContinuousParameter`** — numeric parameters sampled from a distribution.
`distribution` is a keyword: `LOG-TRIANGULAR` (default when omitted),
`TRIANGULAR`, `LOG-UNIFORM` or `UNIFORM`. Quoted lowercase names
(`"triangular"`) still work. `low_val` and `high_val` bound the range;
triangular distributions also take `mid_val` (the mode). If `mid_val` is
omitted it is inferred: the arithmetic mean `(low + high) / 2` for
`TRIANGULAR`, the geometric mean `sqrt(low * high)` for `LOG-TRIANGULAR`.
Log distributions require positive bounds.

Sampled values are rounded. `decimals` is optional; without it Horizon picks
the number of decimals from the bounds:

- the rounding step is at most 1/10,000 of the range (`high_val - low_val`), and
- for log distributions, values keep 4 significant digits at `low_val`, where
  log sampling puts most of its values.

Examples: 100–200 gives 2 decimals, 0.0001–0.001 gives 8, a log-triangular
0.001–0.12 gives 6. If an `if` block changes the bounds or distribution, the
decimals are recomputed for that scenario. Set `decimals` only to force a
fixed number.

**`DiscreteParameter`** — parameters drawn from a fixed set of values, with
optional probabilities (uniform if omitted). `probabilities` must have one
entry per value; they are scaled to sum to 1:

```
DiscreteParameter "RENEWAL" {
    name = "Fleet renewal rate"
    default = 0.05
    values = [0.03, 0.05, 0.08]
    probabilities = [0.25, 0.5, 0.25]
}
```

### Active vs. inactive parameters

Every parameter has an `active` flag. Inactive parameters (`active = FALSE`)
always use their `default` value. An inactive scenario parameter does not
expand the combinatorial space but its default is still available as a token.

### Reserved samples

The first three samples are always deterministic:

- `sample_1`: default values
- `sample_2`: emissions-low scenario
- `sample_3`: emissions-high scenario
- `sample_4+`: random (MC) or stratified (LHS) draws

The emissions scenarios use each parameter's optional `emissions_low` /
`emissions_high` attributes, which can be numeric values or the keywords
`MINIMUM` / `MAXIMUM` (`MIN` / `MAX`). If only one is given as a keyword, the
other is set to the opposite.

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
default.

Conditions support equality (`token = value`), membership
(`token in (a, b)`), and conjunction (`and`). When overrides are present and
active scenario parameters exist, sampling runs separately per scenario
combination; the same underlying draw matrix is reused so `sample_i` indices
align across scenarios.

### Checking a study before running it

```bash
horizon run.hor --check             # validation + cross-file checks, exit 0 or 1
horizon run.hor --check --explain   # also print the resolved parameters
horizon run.hor --samples 1         # run with one sample, file unchanged
```

`--check` runs all the validation above and then reads the `.unc` template
and the `.inc` files it includes (commented-out `Include` lines are skipped):

- a `%TOKEN%` without a parameter is an error (`SCENARIO` is built in); a
  parameter that no template or include uses is a warning;
- an include path with a scenario token, such as `policy_%policy%.inc`, is
  resolved for every value of that scenario parameter; a missing file or one
  whose name differs in case is an error;
- the summary line ends with the number of errors and warnings, and the exit
  code is 1 if there are errors.

Nothing is sampled, written or queued. `--explain` prints one row per
parameter and scenario value (scenario parameters that appear in an `if` of
that parameter) with `dist`, `low`, `mode`, `high`, `default`, `decimals`
and `from`: `base`, or the `if` condition with the keys it changes, plus
notes for inferred values (`mode inferred`, `default from mid_val`,
`decimals auto`). `--samples N` replaces `NumberOfSamples` for one run and
logs that it did.

### Include / Exclude filters

`Include(...)` and `Exclude(...)` directives in the `Horizon` block filter
which scenario combinations run:

- No Include, no Exclude — run all combinations
- Include only — run **only** matching combinations (whitelist)
- Exclude only — run all **except** matching (blacklist)
- Include + Exclude — Include narrows first, then Exclude removes from that set

Both use partial matching: `Include(BIO = "high")` matches every combination
where `BIO` is `"high"`, regardless of other tokens.

Include rules support an optional `_name` key that labels each combination:

```
Include(BIO = "high", ELEC = "low", _name = "s1")
Include(BIO = "low", ELEC = "high", _name = "s2")
```

- `_name` is reserved — it labels, it does not filter
- If any Include rule has `_name`, all must have it (parse-time error if mixed)
- The label becomes available as the `%SCENARIO%` token in `.unc` templates
- `SCENARIO` is therefore a reserved token name — do not use it for a
  `ScenarioParameter`

## The `.unc` template and token replacement

A `.unc` file is an ordinary Navigate simulation file in which uncertain
values are replaced with `%TOKEN%` placeholders. Horizon writes one `.nav`
file per sample/scenario combination, substituting each token with the
sampled value. `Include` directives referencing `.inc` files are processed
the same way, so tokens work across included files too. Refer to the
[Navigate documentation](https://zerocarbonshipping.github.io/navigate-zcs/)
for the simulation file format itself.

## The `.sen` file (sensitivity analysis)

The `.sen` format configures PRCC (partial rank correlation coefficient)
analysis of completed runs:

```
SensitivityAnalysis {
    SamplesCSV = "output/samples.csv"       # required
    SourceDir = "."                         # optional (defaults to .sen file dir)
    ReportCSV = "report.csv"                # optional (collected report from 'horizon -c')
    OutputDir = "prcc_analysis"             # optional

    Metric "Emissions reduction" {
        key = "TotalEquivalentWtw"
        aggregation = difference            # last year minus first year
    }

    Metric "Lifetime emissions" {
        key = "TotalEquivalentWtw"
        aggregation = cumulative            # sum over all years
    }

    Metric "Final year emissions" {
        key = "TotalEquivalentWtw"
        year = 2050
        aggregation = point                 # value at a specific year (default)
    }
}
```

### Metric aggregation modes

| Mode | Meaning |
|------|---------|
| `point` (default) | Value at a specific year (`year = YYYY`), or the final year if omitted. |
| `difference` | `metric(last year) - metric(first year)`. More stable than single-year values. |
| `cumulative` | Sum over all simulation years; captures total lifetime impact. |

### CLI equivalent

Sensitivity analysis can also run without a `.sen` file:

```bash
horizon --sensitivity-analysis --samples-csv samples.csv \
        --metric "TotalEquivalentWtw:difference" \
        --metric "Expenses:cumulative" \
        --report-csv report.csv source_dir output_dir
```

Default metrics when none are given: `TotalEquivalentWtw:difference`,
`Expenses:difference`, `TotalEquivalentWtw:cumulative`.
