# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# File: horizon/test_manager/create_files.py
"""
File creation entrypoint for Horizon.

Responsibilities:
 - parse .hor file via parse_hor_file()
 - sample parameters (either once globally or per-active-scenario combination)
 - write sampled parameters CSV
 - optionally run sampling diagnostics/plotting
 - generate .nav files and commands via FileHandler
 - enqueue and run commands

Behavior summary (plain):
 - When any sample parameter defines conditional overrides and there is at least one
   active scenario parameter, sampling is performed per combination of active scenario
   parameter values. Inactive scenario parameters are used with their default values
   and do not expand the combinations.
 - The sampler uses the same underlying draw matrix (LHS/MC) for each scenario combo,
   so that sample indexes (sample_1..sample_N) line up across scenarios. For each
   scenario we resolve scenario-dependent parameter definitions first, then map the
   shared draw to values for that scenario.
 - If no sample parameter defines overrides (or there are no active scenario params),
   the code uses legacy behavior: sample once across all parameters and let the
   file generator expand scenario combinations if required.
"""

from __future__ import annotations

import logging
import os
import time
from itertools import product
from pathlib import Path

from horizon.file_handler.file_handler import FileHandler, extract_template_tokens
from horizon.misc.util import output_sampled_parameters_to_csv
from horizon.parameters.parameter import ContinuousParameter, DiscreteParameter
from horizon.parameters.sampler import ParameterSampler, resolve_parameters_for_scenario
from horizon.parser.exclusions import get_scenario_label, should_skip_combination
from horizon.parser.parser import parse_hor_file
from horizon.plot.plot import analyze_sampled_parameters
from horizon.run.run_commands import StreamingQueuer

logger = logging.getLogger(__name__)


def _format_rules(rules):
    """Format a list of rule dicts as a human-readable string.

    1-2 rules: inline pipe-separated.
    3+ rules: numbered list, one per line.

    When a rule has ``_name``, it is shown as a label prefix.

    Example
    -------
    >>> _format_rules([{'alignment': 'high', 'biomass': 'mid'}])
    'alignment="high" biomass="mid"'
    """
    lines = []
    for rule in rules:
        label = rule.get("_name")
        body = ' '.join(f'{k}="{v}"' for k, v in rule.items() if k != "_name")
        lines.append(f"[{label}] {body}" if label else body)

    if len(lines) <= 2:
        return ' | '.join(lines)

    return '\n' + '\n'.join(f"  {line}" for line in lines)


def _format_duration(seconds):
    """Format a duration in seconds as a human-readable string."""
    if seconds < 60:
        return "%d seconds" % int(seconds)
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d hours, %d minutes, %d seconds" % (hours, minutes, secs)
    return "%d minutes, %d seconds" % (minutes, secs)


def sample_parameters(parameters, number_of_samples, sampling_method, random_seed, sampler=None):
    """Facade to ParameterSampler.

    Pass a shared ``sampler`` when calling repeatedly (per-scenario sampling):
    the instance caches its seeded draw matrix, so scenario combinations reuse
    one matrix instead of recomputing the identical one per combination.
    """
    if sampler is None:
        sampler = ParameterSampler()
    if sampling_method == "LHS":
        return sampler.sample_latin_hypercube(parameters, number_of_samples, seed=random_seed)
    elif sampling_method == "MC":
        return sampler.sample_group(parameters, number_of_samples, seed=random_seed)
    elif sampling_method == "SENSITIVITY":
        return sampler.sensitivity_analysis(parameters)
    elif sampling_method == "CALIBRATION":
        return sampler.sample_calibration(parameters)
    else:
        raise ValueError("Sampling method %s not allowed. Allowed: LHS, MC, SENSITIVITY, CALIBRATION" % sampling_method)


def construct_parameter_types(parameters):
    """Return a mapping token -> parameter type string for CSV type row."""
    types = {}
    for p in parameters:
        if isinstance(p, ContinuousParameter):
            types[p.token] = "Continuous"
        elif isinstance(p, DiscreteParameter):
            types[p.token] = "Discrete"
        else:
            types[p.token] = "Undefined"
    return types


def _sample_for_active_combos(scenario_parameters, parameters, number_of_samples, sampling_method, random_seed,
                              max_files=None, exclusion_rules=None, inclusion_rules=None):
    """
    Perform per-active-scenario sampling.

    Builds the cartesian product of active scenario parameter values (inactive
    params use their default and do not expand the space). For each combination,
    resolves scenario-dependent parameter definitions, then samples with a shared
    deterministic seed so that sample_i aligns across scenarios.

    Returns
    -------
    tuple[list[dict], int, int]
        (sampled_parameters, skipped_count, total_count)
    """
    active_scenario_params = [sp for sp in scenario_parameters if sp.active]
    active_tokens = [sp.token for sp in active_scenario_params]
    active_value_lists = [sp.values for sp in active_scenario_params]

    sampled_parameters = []
    total_combos = 0
    skipped_combos = 0

    # One sampler shared across combinations: it caches the seeded draw
    # matrix, which is identical for every combination by design (that is
    # what keeps sample_i aligned across scenarios).
    shared_sampler = ParameterSampler()

    # If there are no active tokens, product(*) yields one empty tuple -> single iteration (desired).
    for combo_values in product(*active_value_lists):
        total_combos += 1
        active_map = dict(zip(active_tokens, combo_values))

        # Build full scenario_map: active tokens from combo, inactive tokens -> default (strip quotes)
        scenario_map = {}
        for sp in scenario_parameters:
            if sp.active:
                scenario_map[sp.token] = active_map[sp.token]
            else:
                default_val = sp.default
                if isinstance(default_val, str):
                    default_val = default_val.strip('"').strip("'")
                scenario_map[sp.token] = default_val

        # Check inclusion/exclusion rules before sampling
        if should_skip_combination(scenario_map, exclusion_rules, inclusion_rules):
            logger.debug("Skipping scenario combination %s (filtered by inclusion/exclusion rules)", scenario_map)
            skipped_combos += 1
            continue

        # If Include rules define _name labels, inject as SCENARIO token
        label = get_scenario_label(scenario_map, inclusion_rules)
        if label is not None:
            scenario_map["SCENARIO"] = label

        # Resolve parameters for this scenario (apply overrides)
        params_for_scenario = resolve_parameters_for_scenario(parameters, scenario_map)

        # Use shared deterministic seed so sample_i corresponds across scenarios
        seed_for_combo = random_seed

        sampled_for_combo = sample_parameters(params_for_scenario, number_of_samples, sampling_method,
                                              seed_for_combo, sampler=shared_sampler)

        # Annotate samples with full scenario_map and sample numbers 1..N
        for i, row in enumerate(sampled_for_combo):
            s = dict(row)  # copy to avoid mutation sharing
            s.update(scenario_map)
            s["sample"] = "sample_%d" % (i + 1)
            sampled_parameters.append(s)

        if max_files is not None and len(sampled_parameters) >= max_files:
            sampled_parameters = sampled_parameters[:max_files]
            break

    return sampled_parameters, skipped_combos, total_combos


def _validate_tokens(unc_file_path, scenario_parameters, parameters):
    """Cross-validate tokens defined in .hor against placeholders in the .unc template.

    Warns on tokens defined in .hor but absent from the template (unused parameter).
    Warns on tokens found in the template but not defined in .hor (missing parameter),
    except for built-in tokens like SCENARIO.
    """
    BUILTIN_TOKENS = {"SCENARIO"}

    try:
        template_tokens = extract_template_tokens(unc_file_path)
    except Exception:
        logger.warning("Could not scan template for token validation; skipping.")
        return

    # Collect all defined tokens
    defined_tokens = set()
    for p in scenario_parameters:
        defined_tokens.add(p.token)
    for p in parameters:
        defined_tokens.add(p.token)

    # Tokens in template but not defined
    undefined = template_tokens - defined_tokens - BUILTIN_TOKENS
    if undefined:
        logger.warning(
            "Template contains %d token(s) not defined in .hor file: %s",
            len(undefined), ', '.join(sorted(undefined))
        )

    # Tokens defined but not in template
    unused = defined_tokens - template_tokens
    if unused:
        logger.warning(
            "%d token(s) defined in .hor but not found in template: %s",
            len(unused), ', '.join(sorted(unused))
        )


def create_files(hor_file_path, max_files=None, priority="normal", solver=None,
                 navigate_flags=None, output_dir=None, dry_run=False, full_task_env=False,
                 pueue_cli=False):
    """
    Main entrypoint to create files based on the .hor configuration.

    Args:
        hor_file_path: path to the .hor file
        max_files: optional limit on total nav files to generate
        priority: pueue scheduling priority ('low', 'normal', or 'high')
        solver: optional solver backend for NavigaTE ('auto', 'gurobi', or 'highs')
        navigate_flags: optional extra flags to pass to each navigate command
        output_dir: optional directory for generated scenario folders (default: next to .unc file)
        dry_run: if True, validate and preview without generating files or running simulations
        full_task_env: forward the entire environment to each pueue task instead of the
            whitelist (see run_commands; extend the whitelist with HORIZON_TASK_ENV)
        pueue_cli: force submission through the pueue CLI instead of the direct
            daemon connection
    Returns:
        sample_only (bool) if we terminated early because SampleOnly=True, otherwise None
    """
    start_time = time.time()
    hor_path = Path(hor_file_path)

    try:
        (unc_file_path, output_path, number_of_samples, scenario_parameters, parameters,
         sampling_method, plot, max_parallel_workers, random_seed, sample_only,
         exclusion_rules, inclusion_rules) = parse_hor_file(str(hor_path))
    except Exception as e:
        logger.exception("Failed to parse .hor file '%s': %s", hor_file_path, e)
        raise

    logger.info("Read %d scenario parameters and %d sample parameters", len(scenario_parameters), len(parameters))
    logger.info("Sampling method: %s, Random seed: %s", sampling_method, random_seed)
    if exclusion_rules:
        logger.info("Loaded %d exclusion rule(s): %s", len(exclusion_rules), _format_rules(exclusion_rules))
        logger.debug("Exclusion rules (raw): %s", exclusion_rules)
    if inclusion_rules:
        logger.info("Loaded %d inclusion rule(s): %s", len(inclusion_rules), _format_rules(inclusion_rules))
        logger.debug("Inclusion rules (raw): %s", inclusion_rules)

    # Defensive validation
    if number_of_samples <= 0:
        raise ValueError("NumberOfSamples must be > 0")

    # Token validation: cross-check .hor tokens against .unc template
    _validate_tokens(unc_file_path, scenario_parameters, parameters)

    any_overrides = any(getattr(p, "overrides", None) for p in parameters)
    active_scenario_params = [sp for sp in scenario_parameters if sp.active]

    # Decide sampling mode
    if any_overrides and active_scenario_params:
        logger.info("Detected parameter overrides and active scenario parameters -> per-scenario sampling.")
        sampled_parameters, skipped_combos, total_combos = _sample_for_active_combos(
            scenario_parameters=scenario_parameters,
            parameters=parameters,
            number_of_samples=number_of_samples,
            sampling_method=sampling_method,
            random_seed=random_seed,
            max_files=max_files,
            exclusion_rules=exclusion_rules,
            inclusion_rules=inclusion_rules,
        )
        if skipped_combos:
            logger.info("Skipped %d of %d scenario combinations (filtered by inclusion/exclusion rules)",
                        skipped_combos, total_combos)
    else:
        logger.info("Legacy sampling mode (sample once across all scenarios).")
        sampled_parameters = sample_parameters(parameters, number_of_samples, sampling_method, random_seed)
        if max_files is not None:
            sampled_parameters = sampled_parameters[:max_files]

    # Dry-run summary and early exit
    if dry_run:
        active_scenario_count = len([sp for sp in scenario_parameters if sp.active])
        total_realizations = len(sampled_parameters)

        logger.info("--- Dry Run Summary ---")
        logger.info("Configuration: %s", hor_file_path)
        logger.info("Template: %s", unc_file_path)
        logger.info("Sampling method: %s, Samples: %d, Seed: %s", sampling_method, number_of_samples, random_seed)
        logger.info("Active scenario parameters: %d", active_scenario_count)
        for sp in scenario_parameters:
            status = "ACTIVE" if sp.active else "inactive"
            logger.info("  %s (%s): %s [%s]", sp.token, status,
                        ', '.join(str(v) for v in sp.values) if sp.active else str(sp.default),
                        sp.name)
        logger.info("Sample parameters: %d", len(parameters))
        for p in parameters:
            status = "ACTIVE" if p.active else "inactive"
            if isinstance(p, ContinuousParameter):
                logger.info("  %s (%s): %s [%s, %s] %s", p.token, status, p.distribution,
                            p.low_val, p.high_val, p.name)
            elif isinstance(p, DiscreteParameter):
                logger.info("  %s (%s): %s %s", p.token, status,
                            ', '.join(str(v) for v in p.values), p.name)
        if exclusion_rules:
            logger.info("Exclusion rules: %d", len(exclusion_rules))
        if inclusion_rules:
            logger.info("Inclusion rules: %d", len(inclusion_rules))
        logger.info("Total realizations to generate: %d", total_realizations)
        logger.info("--- End Dry Run (no files generated, no simulations queued) ---")
        return None

    # Guard: ensure we have something to write/diagnose
    if not sampled_parameters:
        if exclusion_rules or inclusion_rules:
            logger.warning("No sampled parameters were produced (all combinations may have been filtered by "
                           "Include/Exclude rules); aborting file creation.")
        else:
            logger.warning("No sampled parameters were produced; aborting file creation.")
        return None

    # Construct parameter types and output CSV
    parameter_types = construct_parameter_types(parameters)
    try:
        output_sampled_parameters_to_csv(sampled_parameters, parameter_types, output_path)
    except Exception:
        logger.exception("Failed writing sampled parameters CSV to %s", output_path)
        raise

    # Deprecate MaxParallelWorkers — pueue handles parallelism natively
    if max_parallel_workers is not None:
        logger.warning(
            "MaxParallelWorkers is set but ignored. "
            "Pueue handles parallel execution natively. "
            "Use 'horizon --priority' to control task priority."
        )

    # Run sampling diagnostics if sample_only. If SampleOnly we run diagnostics then terminate.
    if sample_only:
        logger.info("Running sampling diagnostics (plot=%s, sample_only=%s)", plot, sample_only)
        try:
            analyze_sampled_parameters(sampled_parameters, parameters, output_path)
            logger.info("Sampling diagnostics completed.")
        except Exception:
            logger.exception("Sampling diagnostics failed.")

        if sample_only:
            logger.info("SampleOnly = TRUE; terminating after diagnostics.")
            return sample_only

    # Prepare file generation
    if output_dir is not None:
        output_folder = os.path.abspath(output_dir)
    else:
        output_folder = os.path.dirname(unc_file_path)
        if not output_folder:
            logger.error("UncFilePath '%s' did not contain a directory; aborting.", unc_file_path)
            return None
        output_folder = os.path.abspath(output_folder)

    file_handler = FileHandler()
    logger.info("Starting file creation in %s", output_folder)

    # Queue each realization the moment its .nav is written: the first
    # simulations start while the rest of the study is still generating, and
    # total wall time becomes max(generation, queuing) instead of their sum.
    queuer = StreamingQueuer(priority=priority, full_task_env=full_task_env, pueue_cli=pueue_cli)

    try:
        file_handler.generate_scenarios_and_nav_files(
            unc_file_path,
            sampled_parameters,
            scenario_parameters,
            output_folder,
            exclusion_rules=exclusion_rules,
            inclusion_rules=inclusion_rules,
            solver=solver,
            navigate_flags=navigate_flags,
            command_sink=queuer,
        )
    finally:
        # Wait for the already-submitted tasks even if generation failed
        # midway - they are queued in pueue either way and must be reported.
        try:
            queuer.finish()
        except Exception:
            logger.exception("Failed to finish queue submission.")

    # Log generated files
    if logger.isEnabledFor(logging.DEBUG):
        for path in file_handler.nav_filepaths:
            logger.debug("Generated NAV file: %s", path)

    skip_note = " (%d combination(s) skipped by filters)" % file_handler.skipped_count if file_handler.skipped_count > 0 else ""
    logger.info("Generated %d NAV file(s) in %s%s", len(file_handler.nav_filepaths), output_folder, skip_note)

    logger.info("Queued %d navigate command(s)", len(file_handler.commands))
    if logger.isEnabledFor(logging.DEBUG):
        for cmd in file_handler.commands:
            logger.debug("Command: %s", cmd)

    # Timing
    end_time = time.time()
    logger.info("Finished. Total duration: %s", _format_duration(end_time - start_time))

    return None
