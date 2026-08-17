# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Generation of NavigaTE .nav simulation files from .unc templates, with
%TOKEN% replacement and scenario-combination filtering."""

import concurrent.futures
import itertools
import logging
import os
import re
import threading

from horizon.exceptions import FileOperationError
from horizon.parser.exclusions import get_scenario_label, should_skip_combination

# Token pattern: matches %token_name%
_TOKEN_RE = re.compile(r'%([A-Za-z0-9_]+)%')

logger = logging.getLogger(__name__)


_PROGRESS_HEARTBEAT_INTERVAL = 50


def extract_template_tokens(unc_path):
    """Extract all %TOKEN% placeholders from a .unc template and its .inc includes.

    Parameters
    ----------
    unc_path : str
        Path to the .unc template file.

    Returns
    -------
    set[str]
        Set of unique token names found in the template and its includes.
    """
    tokens = set()
    try:
        with open(unc_path, 'r') as fh:
            for line in fh:
                tokens.update(m.group(1) for m in _TOKEN_RE.finditer(line))
                # Follow INCLUDE directives to scan .inc files
                if line.strip().upper().startswith('INCLUDE'):
                    try:
                        include_path_relative = line.split('"')[1]
                        # Only follow includes that don't themselves contain tokens
                        if '%' not in include_path_relative:
                            full_path = os.path.normpath(
                                os.path.join(os.path.dirname(unc_path), include_path_relative)
                            )
                            if os.path.isfile(full_path):
                                with open(full_path, 'r') as inc_fh:
                                    for inc_line in inc_fh:
                                        tokens.update(m.group(1) for m in _TOKEN_RE.finditer(inc_line))
                    except (IndexError, OSError):
                        pass
    except FileNotFoundError:
        raise FileOperationError(f"UNC template file not found: {unc_path}")
    return tokens


class FileHandler:
    """
    Responsible for turning a .unc template together with sampled parameter rows
    into per-realization .nav files and a list of commands to run them.

    Behavior notes:
    - Accepts `sampled_parameters` as a list of dicts. Each dict may already contain
      scenario tokens (pre-resolved) or not.
    - If each sample dict already contains the scenario tokens (pre-resolved mode),
      the handler will not expand scenario combinations and will generate one nav per
      sample. Otherwise, it will generate the cartesian product of scenario parameter values
      and iterate `sampled_parameters` per combination.
    - INCLUDE lines in the .unc file are processed: path tokens are interpolated,
      the target .inc file is read and tokens within it are replaced, and a modified
      .inc file is written to `simulation_includes` if any tokens were replaced.
    """

    def __init__(self):
        self.nav_filepaths = []
        self.commands = []
        self.skipped_count = 0
        self._include_cache = {}
        self._include_lock = threading.Lock()

    # -------------------------
    # Low-level file helpers
    # -------------------------
    @staticmethod
    def _read_file_lines(filepath):
        with open(filepath, 'r') as fh:
            return fh.readlines()

    @staticmethod
    def _write_to_file(filepath, content):
        """
        Write a list of lines to filepath (creates directories as needed).
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as fh:
            fh.writelines(content)

    @staticmethod
    def _format_value_for_template(v):
        """
        Format a replacement value for textual placement.

        Floats are rendered compactly (6 significant digits), None -> empty string,
        other values -> str().
        """
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)

    @staticmethod
    def _replace_tokens_in_text(text, replacements):
        """
        Replace %token% placeholders using replacements dict.

        Unknown tokens are left as-is (so debugging / fallback is easier).
        """
        def _repl(m):
            token = m.group(1)
            if token in replacements:
                return str(replacements[token])
            return m.group(0)

        return _TOKEN_RE.sub(_repl, text)

    def _read_include_cached(self, filepath):
        """Read an include file, using a thread-safe cache."""
        with self._include_lock:
            if filepath not in self._include_cache:
                try:
                    self._include_cache[filepath] = self._read_file_lines(filepath)
                except FileNotFoundError:
                    raise FileOperationError(f"Include file not found: {filepath}")
            return self._include_cache[filepath]

    # -------------------------
    # Public API
    # -------------------------
    def generate_scenarios_and_nav_files(self, unc_path, sampled_parameters, scenario_parameters, output_folder,
                                         exclusion_rules=None, inclusion_rules=None, solver=None,
                                         navigate_flags=None):
        """
        Generate .nav files for all realizations.

        Parameters
        ----------
        unc_path : str
            Path to the .unc template file.
        sampled_parameters : list[dict]
            Sampled rows. Each dict contains sample tokens and possibly scenario tokens.
        scenario_parameters : list
            ScenarioParameter objects (with .token, .values, .active, .default).
        output_folder : str
            Directory in which realizations folders will be created.
        exclusion_rules : list[dict] or None
            Exclusion rules from Exclude() directives. Each dict maps token names to values.
        inclusion_rules : list[dict] or None
            Inclusion rules from Include() directives. Each dict maps token names to values.
        """
        # Read UNC template once and pre-classify lines
        try:
            unc_content = self._read_file_lines(unc_path)
        except FileNotFoundError:
            raise FileOperationError(f"UNC template file not found: {unc_path}")

        is_include_line = [line.strip().upper().startswith('INCLUDE') for line in unc_content]

        # Build adjusted lists of scenario tokens and values:
        adjusted_scenario_values = []
        adjusted_scenario_keys = []
        for scenario in scenario_parameters:
            if scenario.active:
                adjusted_scenario_values.append(scenario.values)
                adjusted_scenario_keys.append(scenario.token)
            else:
                # inactive scenario: use default only
                default_val = scenario.default
                if isinstance(default_val, str):
                    default_val = default_val.strip('"').strip("'")
                adjusted_scenario_values.append([default_val])
                adjusted_scenario_keys.append(scenario.token)

        # Detect pre-resolved mode: each sample already has all scenario tokens
        pre_resolved = (
            bool(adjusted_scenario_keys)
            and sampled_parameters
            and all(all(k in s for k in adjusted_scenario_keys) for s in sampled_parameters)
        )

        # Generate work items lazily using a generator to avoid
        # materializing all realizations in memory at once
        def _generate_work_items():
            if pre_resolved:
                logger.debug("Samples appear pre-resolved with scenario tokens; generating one NAV per sample.")
                for sample_idx, sample in enumerate(sampled_parameters, start=1):
                    scenario_dict = {k: sample[k] for k in adjusted_scenario_keys}

                    if should_skip_combination(scenario_dict, exclusion_rules, inclusion_rules):
                        logger.debug("Skipping scenario combination %s (filtered by inclusion/exclusion rules)", scenario_dict)
                        self.skipped_count += 1
                        continue

                    scenario_label = sample.get("SCENARIO")
                    if scenario_label is not None:
                        scenario_combination_name = scenario_label
                    else:
                        scenario_combination_name = '_'.join(str(scenario_dict[k]) for k in adjusted_scenario_keys)

                    sample_name = sample.get("sample", f"sample_{sample_idx}")
                    sample_number_for_includes = sample_idx
                    sample_suffix = sample_name

                    if isinstance(sample_name, str) and sample_name.startswith("sample_"):
                        try:
                            n = int(sample_name.split("_", 1)[1])
                            sample_suffix = f"sample{n:03d}"
                            sample_number_for_includes = n
                        except Exception:
                            sample_suffix = sample_name
                            sample_number_for_includes = sample_idx

                    realization_folder_name = f"{scenario_combination_name}_{sample_suffix}"
                    realization_folder = os.path.join(output_folder, realization_folder_name)

                    yield {
                        'sample': sample,
                        'simulation_name': realization_folder_name,
                        'scenario_dict': scenario_dict,
                        'realization_folder': realization_folder,
                        'sample_number': sample_number_for_includes,
                    }

            else:
                # Legacy behaviour: expand scenario combinations lazily
                for combination in itertools.product(*adjusted_scenario_values):
                    scenario_dict = dict(zip(adjusted_scenario_keys, combination))

                    if should_skip_combination(scenario_dict, exclusion_rules, inclusion_rules):
                        logger.debug("Skipping scenario combination %s (filtered by inclusion/exclusion rules)", scenario_dict)
                        self.skipped_count += 1
                        continue

                    label = get_scenario_label(scenario_dict, inclusion_rules)
                    if label is not None:
                        scenario_dict["SCENARIO"] = label
                        scenario_combination_name = label
                    else:
                        scenario_combination_name = '_'.join(str(x) for x in combination)

                    for sample_idx, sample in enumerate(sampled_parameters, start=1):
                        realization_folder_name = f"{scenario_combination_name}_sample{sample_idx:03d}"
                        realization_folder = os.path.join(output_folder, realization_folder_name)

                        yield {
                            'sample': sample,
                            'simulation_name': realization_folder_name,
                            'scenario_dict': scenario_dict,
                            'realization_folder': realization_folder,
                            'sample_number': sample_idx,
                        }

        # Generate NAV files in parallel, consuming the generator on demand
        self._generate_nav_files_parallel(_generate_work_items(), unc_content, is_include_line, unc_path)

        # After nav files created, prepare command list
        self.generate_commands_list(solver=solver, navigate_flags=navigate_flags)

    def _generate_nav_files_parallel(self, work_items, unc_content, is_include_line, unc_path):
        """Generate NAV files using a thread pool for I/O-bound parallelism.

        Accepts any iterable of work items (including generators) to avoid
        materializing all realizations in memory at once. Items are submitted
        to the thread pool as they are yielded.
        """
        max_workers = min(8, os.cpu_count() or 4)
        nav_filepaths = []
        future_to_name = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item in work_items:
                future = executor.submit(
                    self._generate_nav_file_for_sample,
                    unc_path, unc_content, is_include_line,
                    item['sample'], item['simulation_name'],
                    item['scenario_dict'], item['realization_folder'],
                    item['sample_number']
                )
                future_to_name[future] = item['simulation_name']

            completed = 0
            for future in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    nav_filepath = future.result()
                    if nav_filepath:
                        nav_filepaths.append(nav_filepath)
                        completed += 1
                        if completed % _PROGRESS_HEARTBEAT_INTERVAL == 0:
                            logger.info("File creation progress: %d NAV files generated...", completed)
                except Exception:
                    logger.exception("Failed generating NAV file for %s", name)

        self.nav_filepaths = nav_filepaths

    def _generate_nav_file_for_sample(self, unc_path, unc_content, is_include_line, sample, simulation_name,
                                      scenario_parameters, realization_folder, sample_number):
        """
        Create one .nav file for a given sample + scenario.
        - Copies & replaces tokens in included .inc files when needed into simulation_includes/
        - Returns the nav filepath on success, or None on failure.
        """
        simulation_includes_folder = self._prepare_simulation_directories(realization_folder)

        nav_content = []

        # Build unified replacements map: scenario params (strings) first, then sampled params
        replacements = {}
        for token, value in (scenario_parameters or {}).items():
            if value is None:
                continue
            replacements[token] = str(value)

        for token, value in (sample or {}).items():
            if token == "sample":
                continue
            replacements[token] = self._format_value_for_template(value)

        for i, line in enumerate(unc_content):
            if is_include_line[i]:
                # Update include path with token replacements
                line, include_path_relative = self._update_include_path(line, replacements)

                # Resolve full include path relative to unc_path
                full_include_path = os.path.normpath(os.path.join(os.path.dirname(unc_path), include_path_relative))

                # Process include file: replace tokens and potentially write a modified .inc file
                try:
                    updated_line = self._process_and_update_include_file(
                        full_include_path,
                        sample,
                        sample_number,
                        simulation_includes_folder,
                        scenario_parameters,
                        realization_folder
                    )
                except Exception:
                    logger.exception("Failed to process include file: %s", full_include_path)
                    updated_line = None

                if updated_line:
                    line = updated_line

                nav_content.append(line)
                continue

            # Normal line: perform token replacement directly
            replaced = self._replace_tokens_in_text(line, replacements)
            nav_content.append(replaced)

        # Write nav file
        nav_filename = f"{simulation_name}.nav"
        nav_filepath = os.path.join(realization_folder, nav_filename)
        try:
            self._write_to_file(nav_filepath, nav_content)
            return nav_filepath
        except Exception:
            logger.exception("Failed writing NAV file %s", nav_filepath)
            return None

    def generate_commands_list(self, solver=None, navigate_flags=None):
        """Generate list of commands to run NavigaTE on all generated .nav files."""
        solver_flag = f' --solver {solver}' if solver else ''
        extra_flags = f' {navigate_flags}' if navigate_flags else ''
        self.commands = [
            'navigate "{}"{}{}'.format(
                os.path.abspath(path).replace(os.sep, '/'),
                solver_flag,
                extra_flags
            )
            for path in self.nav_filepaths
        ]

    def _process_and_update_include_file(self, include_file_path, sample, sample_number,
                                         simulation_includes_folder, scenario_parameters, realization_folder):
        """
        Process an include (.inc) file: replace tokens and optionally save to simulation_includes.

        Returns a new INCLUDE line to be placed in the .nav file, or None if the include could not be handled.
        """
        inc_content = self._read_include_cached(include_file_path)

        modified_inc_content = []
        tokens_replaced = []

        # Build unified replacements mapping for the include file:
        replacements = {}
        for token, value in (scenario_parameters or {}).items():
            if value is None:
                continue
            replacements[token] = str(value)
        for token, value in (sample or {}).items():
            if token == "sample":
                continue
            replacements[token] = self._format_value_for_template(value)

        # Replace line-by-line, collecting tokens that were actually replaced
        for inc_line in inc_content:
            present_tokens = [m.group(1) for m in _TOKEN_RE.finditer(inc_line)]
            if not present_tokens:
                modified_inc_content.append(inc_line)
                continue

            # Build line-specific replacement map (only tokens that both exist in line and in replacements)
            formatted_replacements = {}
            tokens_replaced_this_line = []
            for t in present_tokens:
                if t in replacements:
                    formatted_replacements[t] = replacements[t]
                    tokens_replaced_this_line.append(t)

            if tokens_replaced_this_line:
                # record unique tokens for naming
                for t in tokens_replaced_this_line:
                    if t not in tokens_replaced:
                        tokens_replaced.append(t)
                # perform replacements
                inc_line = self._replace_tokens_in_text(inc_line, formatted_replacements)

            modified_inc_content.append(inc_line)

        if tokens_replaced:
            # Name the file based on tokens that were actually replaced
            # sanitize token list -> join with underscore
            combined_tokens = '_'.join(tokens_replaced)
            new_file_name = f"{combined_tokens}_sample_{sample_number}.inc"
            new_file_path = os.path.join(simulation_includes_folder, new_file_name)
            try:
                self._write_to_file(new_file_path, modified_inc_content)
            except Exception:
                logger.exception("Failed writing modified include file %s", new_file_path)
                return None
            new_include_path_relative = os.path.join("simulation_includes", new_file_name).replace(os.sep, '/')
            return f'\tINCLUDE "{new_include_path_relative}"\n'
        else:
            # No tokens changed: include original file with a relative path from the .nav file's directory
            original_rel = os.path.relpath(
                include_file_path,
                realization_folder
            ).replace(os.sep, '/')
            return f'\tINCLUDE "{original_rel}"\n'

    @staticmethod
    def _prepare_simulation_directories(output_folder):
        """
        Ensure the simulation folder and subfolders exist and return paths.
        """
        simulation_includes_folder = os.path.join(output_folder, "simulation_includes")
        os.makedirs(simulation_includes_folder, exist_ok=True)
        return simulation_includes_folder

    def _update_include_path(self, line, replacements):
        """
        Replace tokens in an INCLUDE line path using replacements and return the updated line
        and the interpolated relative include path.

        Example:
            line = '\tINCLUDE "../inc/policy_%bio%_%el%.inc"\n'
            replacements = {'bio': 'low_bio', 'el': 'low_el'}
            -> returns ('\tINCLUDE "../inc/policy_low_bio_low_el.inc"\n', '../inc/policy_low_bio_low_el.inc')
        """
        try:
            include_path_relative = line.split('"')[1]
        except IndexError:
            # malformed INCLUDE line; return as-is
            return line, ""

        original_include_path = include_path_relative

        def _repl(m):
            token = m.group(1)
            if token in replacements:
                return self._format_value_for_template(replacements[token])
            return m.group(0)

        include_path_relative = _TOKEN_RE.sub(_repl, include_path_relative)
        line = line.replace(original_include_path, include_path_relative)
        return line, include_path_relative
