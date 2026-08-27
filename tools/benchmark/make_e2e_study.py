# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Build a small real-Navigate smoke study from a navigate-zcs checkout.

Copies Navigate's ``simulations/examples/example_2`` (a self-contained Pacific
bulk scenario that solves in ~10 s with HiGHS), tokenizes two values in its
include files, adds a scenario token and a per-realization Report, and writes
a matching ``study.hor``. The result is a 2-scenario x 2-sample study — four
real Navigate runs — that exercises every seam between Horizon and Navigate:

  deck-level token replacement, tokenized include rewriting into
  simulation_includes/, relative include paths from the realization folder
  back to the shared includes, pueue submission, real solves, per-realization
  Excel reports, and `horizon -c` collection.

Usage:
    python tools/benchmark/make_e2e_study.py <navigate-zcs checkout> <study dir>

Then (pueue daemon running):
    cd <study dir>
    horizon study.hor --solver highs \
        --navigate-flags "-d <navigate-zcs checkout>/assumptions -s" \
        --output-dir <study dir>/out
    pueue status                       # wait for the four runs
    horizon --status <study dir>/out   # must report no failures
    horizon -c <study dir>/out collected.xlsx
"""

import argparse
import os
import shutil
import sys

EXAMPLE_REL = os.path.join("simulations", "examples", "example_2")

REPORT_INC = '''# Report exported per realization; collected by `horizon -c`.
Report "output" {
\tDirectory = "./report"

\tadd_port_property("*", BunkerIntensityPrice)
}
'''

STUDY_HOR = '''# End-to-end smoke study: tokenized copy of Navigate's example_2.
# 2 scenarios x 2 samples = 4 real Navigate runs (~10 s each with HiGHS).

ScenarioParameter "Market scenario" {
    name = "Market scenario"
    token = "MARKET"
    active = TRUE
    default = "base"
    values = ["base", "alt"]
}

ContinuousParameter "Trade growth" {
    name = "Trade growth"
    token = "TRADE_GROWTH"
    active = TRUE
    default = 0.05
    low_val = 0.02
    high_val = 0.06
    decimals = 3
    distribution = "uniform"

    # the alt market assumes stronger growth
    if MARKET = alt {
        low_val = 0.05
        high_val = 0.09
    }
}

ContinuousParameter "Transport cost" {
    name = "Transport cost"
    token = "TRANSPORT_COST"
    active = TRUE
    default = 0.01
    low_val = 0.005
    high_val = 0.02
    decimals = 4
    distribution = "uniform"
}

Horizon {
    UncFilePath = "template.unc"
    OutputPath = "output/samples.csv"
    NumberOfSamples = 2
    SamplingMethod = LHS
    RandomSeed = 7

    ScenarioParameter("MARKET")
    ContinuousParameter("TRADE_GROWTH")
    ContinuousParameter("TRANSPORT_COST")
}
'''


def _replace_once(path, old, new, what):
    with open(path) as fh:
        text = fh.read()
    if text.count(old) != 1:
        sys.exit(f"error: expected exactly one occurrence of {what} in {path}; "
                 f"found {text.count(old)}. Has example_2 changed upstream?")
    with open(path, "w") as fh:
        fh.write(text.replace(old, new))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("navigate_checkout", help="path to a navigate-zcs checkout")
    ap.add_argument("study_dir", help="directory to create the smoke study in")
    ap.add_argument("--fresh", action="store_true", help="delete the study directory first")
    args = ap.parse_args()

    example = os.path.join(args.navigate_checkout, EXAMPLE_REL)
    if not os.path.isfile(os.path.join(example, "example_2.nav")):
        sys.exit(f"error: {example} does not look like a navigate-zcs checkout "
                 "(missing example_2.nav)")

    if args.fresh and os.path.isdir(args.study_dir):
        shutil.rmtree(args.study_dir)
    os.makedirs(args.study_dir, exist_ok=True)

    shutil.copytree(os.path.join(example, "includes"),
                    os.path.join(args.study_dir, "includes"), dirs_exist_ok=True)
    template = os.path.join(args.study_dir, "template.unc")
    shutil.copyfile(os.path.join(example, "example_2.nav"), template)

    # Tokenize one value in each of two include files (one of them also
    # carries a scenario-dependent override in study.hor).
    _replace_once(os.path.join(args.study_dir, "includes", "fleet.inc"),
                  "TradeGrowth = 0.05", "TradeGrowth = %TRADE_GROWTH%",
                  "the TradeGrowth assignment")
    _replace_once(os.path.join(args.study_dir, "includes", "bunker_logistics.inc"),
                  'set_transport_cost("*", 0.01)', 'set_transport_cost("*", %TRANSPORT_COST%)',
                  "the transport cost call")

    # Scenario token in the deck itself + a Report include so `horizon -c`
    # has something to collect. Navigate's deck grammar only allows Load and
    # Include inside DEFINE, so the Report node lives in an include file.
    _replace_once(template, "DEFINE {",
                  "DEFINE {\n    # market scenario of this realization: %MARKET%",
                  "the DEFINE block opener")
    _replace_once(template, "\tLoad DefaultPlot",
                  '\tLoad DefaultPlot\n\tInclude "includes/report.inc"',
                  "the DefaultPlot load")
    with open(os.path.join(args.study_dir, "includes", "report.inc"), "w") as fh:
        fh.write(REPORT_INC)

    with open(os.path.join(args.study_dir, "study.hor"), "w") as fh:
        fh.write(STUDY_HOR)

    print(f"smoke study written to {args.study_dir}")
    print("next:")
    print(f"  cd {args.study_dir}")
    print("  horizon study.hor --solver highs "
          f"--navigate-flags \"-d {os.path.abspath(args.navigate_checkout)}/assumptions -s\" "
          f"--output-dir {os.path.abspath(args.study_dir)}/out")


if __name__ == "__main__":
    main()
