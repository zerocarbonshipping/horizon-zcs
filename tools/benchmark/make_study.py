# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Generate a synthetic Horizon study at a configurable scale for benchmarking.

Produces a self-contained study directory:

    study.hor        scenario parameters (first continuous parameter carries an
                     override so the per-scenario sampling path is exercised),
                     continuous and discrete parameters
    template.unc     deck with token lines, static lines and Include directives
    includes/*.inc   include files; the first ``--tokenized-includes`` of them
                     contain %TOKEN% placeholders (rewritten per realization),
                     the rest are static (referenced in place)

Every token defined in the .hor is guaranteed to appear in the template, so a
benchmark run produces no unused/missing-token warnings.

Presets (``--preset``): small = 4 combos x 100 samples = 400 realizations,
medium = 8 x 250 = 2000, large = 8 x 1250 = 10000.

Usage:
    python tools/benchmark/make_study.py /tmp/study --preset medium --fresh
"""

import argparse
import os
import random
import shutil

PRESETS = {
    "small": dict(n_scen=2, n_samples=100, template_lines=400, n_includes=8, tokenized_includes=3),
    "medium": dict(n_scen=3, n_samples=250, template_lines=800, n_includes=12, tokenized_includes=4),
    "large": dict(n_scen=3, n_samples=1250, template_lines=1500, n_includes=16, tokenized_includes=6),
}


def make_study(root, n_scen=3, scen_values=2, n_cont=12, n_disc=4, n_samples=250,
               template_lines=800, n_includes=12, tokenized_includes=4, include_lines=60,
               seed=42, sample_only=False):
    """Write the study files and return a dict describing the scale."""
    random.seed(seed)
    os.makedirs(root, exist_ok=True)
    inc_dir = os.path.join(root, "includes")
    os.makedirs(inc_dir, exist_ok=True)

    scen_tokens = [f"SCEN{i}" for i in range(n_scen)]
    cont_tokens = [f"CONT{i}" for i in range(n_cont)]
    disc_tokens = [f"DISC{i}" for i in range(n_disc)]
    all_sample_tokens = cont_tokens + disc_tokens

    # ---------------- .hor ----------------
    hor = []
    for i, tok in enumerate(scen_tokens):
        values = ", ".join(f'"v{j}"' for j in range(scen_values))
        hor.append(f'''ScenarioParameter "Scenario {i}" {{
    name = "Scenario {i}"
    token = "{tok}"
    active = TRUE
    default = "v0"
    values = [{values}]
}}
''')
    for i, tok in enumerate(cont_tokens):
        # The first continuous parameter carries an override so that
        # per-scenario sampling (the production code path for real studies)
        # is exercised by the benchmark.
        override = ""
        if i == 0:
            override = f'''
    if {scen_tokens[0]} = v1 {{
        low_val = 1.0
        high_val = 2.0
    }}
'''
        hor.append(f'''ContinuousParameter "Continuous {i}" {{
    name = "Continuous {i}"
    token = "{tok}"
    active = TRUE
    default = 1.0
    low_val = 0.5
    high_val = 1.5
    decimals = 4
    distribution = "uniform"
{override}}}
''')
    for i, tok in enumerate(disc_tokens):
        hor.append(f'''DiscreteParameter "Discrete {i}" {{
    name = "Discrete {i}"
    token = "{tok}"
    active = TRUE
    default = 0.05
    values = [0.03, 0.05, 0.08]
    probabilities = [0.25, 0.5, 0.25]
}}
''')

    refs = "\n".join(
        [f'    ScenarioParameter("{t}")' for t in scen_tokens]
        + [f'    ContinuousParameter("{t}")' for t in cont_tokens]
        + [f'    DiscreteParameter("{t}")' for t in disc_tokens]
    )
    hor.append(f'''Horizon {{
    UncFilePath = "template.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = {n_samples}
    SamplingMethod = LHS
    RandomSeed = {seed}
    SampleOnly = {"TRUE" if sample_only else "FALSE"}

{refs}
}}
''')
    with open(os.path.join(root, "study.hor"), "w") as fh:
        fh.write("\n".join(hor))

    # ---------------- includes ----------------
    include_names = []
    for i in range(n_includes):
        name = f"inc_{i:02d}.inc"
        include_names.append(name)
        lines = [f"# include file {i}\n", "DEFINE {\n"]
        for ln in range(include_lines):
            if i < tokenized_includes and ln % 15 == 7:
                tok = all_sample_tokens[(i * 7 + ln) % len(all_sample_tokens)]
                lines.append(f"    set_value_{ln}(%{tok}%)\n")
            else:
                lines.append(f"    set_constant_{ln}({random.random():.6f})\n")
        lines.append("}\n")
        with open(os.path.join(inc_dir, name), "w") as fh:
            fh.writelines(lines)

    # ---------------- template.unc ----------------
    lines = ["# synthetic benchmark deck\n", "DEFINE {\n"]
    # Guarantee every defined token appears at least once.
    for tok in scen_tokens:
        lines.append(f"    # scenario branch marker: %{tok}%\n")
    for tok in all_sample_tokens:
        lines.append(f"    set_{tok.lower()}(%{tok}%)\n")
    body_lines = max(0, template_lines - len(lines) - len(include_names) - 4)
    token_cursor = 0
    for ln in range(body_lines):
        if ln % 10 == 3:
            tok = all_sample_tokens[token_cursor % len(all_sample_tokens)]
            token_cursor += 1
            lines.append(f"    set_param_{ln}(%{tok}%)\n")
        else:
            lines.append(f"    set_static_{ln}({random.random():.6f})\n")
    for name in include_names:
        lines.append(f'    Include "includes/{name}"\n')
    lines.append("}\n")
    lines.append("EVENTS {\n    Load DefaultTimeStepYearly\n}\n")
    with open(os.path.join(root, "template.unc"), "w") as fh:
        fh.writelines(lines)

    n_combos = scen_values ** n_scen
    return {"combos": n_combos, "samples": n_samples,
            "realizations": n_combos * n_samples,
            "template_lines": len(lines), "includes": n_includes}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory to create the study in")
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None)
    ap.add_argument("--scen", type=int, default=3, help="number of scenario parameters (2 values each)")
    ap.add_argument("--scen-values", type=int, default=2)
    ap.add_argument("--cont", type=int, default=12)
    ap.add_argument("--disc", type=int, default=4)
    ap.add_argument("--samples", type=int, default=250)
    ap.add_argument("--template-lines", type=int, default=800)
    ap.add_argument("--includes", type=int, default=12)
    ap.add_argument("--tokenized-includes", type=int, default=4)
    ap.add_argument("--include-lines", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fresh", action="store_true", help="delete the study directory first")
    args = ap.parse_args()

    kwargs = dict(n_scen=args.scen, scen_values=args.scen_values, n_cont=args.cont,
                  n_disc=args.disc, n_samples=args.samples, template_lines=args.template_lines,
                  n_includes=args.includes, tokenized_includes=args.tokenized_includes,
                  include_lines=args.include_lines, seed=args.seed)
    if args.preset:
        kwargs.update(PRESETS[args.preset])

    if args.fresh and os.path.isdir(args.root):
        shutil.rmtree(args.root)
    info = make_study(args.root, **kwargs)
    print(f"study at {args.root}: {info['combos']} combos x {info['samples']} samples "
          f"= {info['realizations']} realizations "
          f"({info['template_lines']}-line template, {info['includes']} includes)")


if __name__ == "__main__":
    main()
