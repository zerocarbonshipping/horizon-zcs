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

# Include directive at the start of a line, e.g. `    Include "includes/a.inc"`.
# Matched case-insensitively so legacy templates using the old all-caps
# `INCLUDE` spelling are still recognised; the emitted .nav is always written
# with the spelling below.
_INCLUDE_LINE_RE = re.compile(r'^(\s*)Include\b', re.IGNORECASE)

# Spelling Navigate's grammar accepts for the deck-level include directive.
# Navigate parses `Include` in Title case only; anything else is a deck syntax
# error, so every include line Horizon writes must use exactly this.
_INCLUDE_KEYWORD = 'Include'

logger = logging.getLogger(__name__)


_PROGRESS_HEARTBEAT_INTERVAL = 50


def _is_include_line(line):
    """Return True if `line` is a deck-level include directive."""
    return _INCLUDE_LINE_RE.match(line) is not None


def _normalize_include_keyword(line):
    """Rewrite the leading include keyword to Navigate's `Include` spelling.

    Leaves indentation and the rest of the line untouched, so a legacy
    `\\tINCLUDE "a.inc"` template line becomes `\\tInclude "a.inc"`.
    """
    return _INCLUDE_LINE_RE.sub(lambda m: f'{m.group(1)}{_INCLUDE_KEYWORD}', line, count=1)


def _format_include_line(path, indent='\t'):
    """Build an include line for a .nav file in Navigate's current format."""
    return f'{indent}{_INCLUDE_KEYWORD} "{path}"\n'


def _compile_parts(text):
    """Compile text into alternating literal/token segments.

    Returns ``[literal, token, literal, token, ..., literal]`` as produced by
    splitting on the %TOKEN% pattern: odd indices hold bare token names, even
    indices the literal text between them. Compiling once and rendering per
    realization replaces a full regex scan of the text per realization with a
    handful of dict lookups.
    """
    return _TOKEN_RE.split(text)


def _render_parts(parts, replacements):
    """Render compiled segments with a replacements mapping.

    Tokens missing from ``replacements`` are kept as ``%token%`` (so
    debugging / fallback is easier). Replacement values must be strings,
    already formatted for textual placement.
    """
    if len(parts) == 1:
        return parts[0]
    out = list(parts)
    for i in range(1, len(out), 2):
        val = replacements.get(out[i])
        out[i] = f"%{out[i]}%" if val is None else val
    return "".join(out)


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
                # Follow Include directives to scan .inc files
                if _is_include_line(line):
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
    - Include lines in the .unc file are processed: path tokens are interpolated,
      the target .inc file is read and tokens within it are replaced, and a modified
      .inc file is written to `simulation_includes` if any tokens were replaced.
    """

    def __init__(self):
        self.nav_filepaths = []
        self.commands = []
        self.skipped_count = 0
        self._include_cache = {}
        # Rendered include lines for includes used unmodified, keyed by
        # (include path, indent). Valid because every realization folder of a
        # run sits directly under the same output folder, so the relative
        # path is the same for all of them.
        self._include_line_cache = {}
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
        Write content (a string or a list of lines) to filepath. The parent
        directory must already exist - callers create it once per realization
        instead of paying a makedirs round trip on every write.
        """
        with open(filepath, 'w') as fh:
            if isinstance(content, str):
                fh.write(content)
            else:
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

    def _compiled_include(self, filepath):
        """Read and compile an include file, using a thread-safe cache.

        Returns the compiled literal/token segments (see _compile_parts).
        """
        with self._include_lock:
            parts = self._include_cache.get(filepath)
            if parts is None:
                try:
                    with open(filepath, 'r') as fh:
                        text = fh.read()
                except FileNotFoundError:
                    raise FileOperationError(f"Include file not found: {filepath}")
                parts = _compile_parts(text)
                self._include_cache[filepath] = parts
            return parts

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
        # Realization folders are created under output_folder; absolutize once
        # so every derived path (and the navigate commands) is absolute.
        output_folder = os.path.abspath(output_folder)
        # The rendered include lines depend on the output folder, which can
        # differ between calls on a reused handler.
        self._include_line_cache = {}

        # Read the UNC template once and compile it into a render program:
        # runs of plain lines become compiled literal/token segments, include
        # lines keep their raw text plus a compiled path for interpolation.
        try:
            unc_content = self._read_file_lines(unc_path)
        except FileNotFoundError:
            raise FileOperationError(f"UNC template file not found: {unc_path}")

        template_program = self._compile_template(unc_content)

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
                # The include/exclude decision depends only on the scenario
                # combination, which repeats for every sample - cache it per
                # combination instead of re-evaluating the rules per sample.
                skip_cache = {}
                for sample_idx, sample in enumerate(sampled_parameters, start=1):
                    scenario_dict = {k: sample[k] for k in adjusted_scenario_keys}

                    combo_key = tuple(scenario_dict.values())
                    skip = skip_cache.get(combo_key)
                    if skip is None:
                        skip = should_skip_combination(scenario_dict, exclusion_rules, inclusion_rules)
                        skip_cache[combo_key] = skip
                    if skip:
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
        self._generate_nav_files_parallel(_generate_work_items(), template_program, unc_path)

        # After nav files created, prepare command list
        self.generate_commands_list(solver=solver, navigate_flags=navigate_flags)

    @staticmethod
    def _compile_template(unc_content):
        """Compile template lines into a render program.

        Returns a list of ('text', parts) and ('include', info) items. Runs
        of consecutive non-include lines are merged and compiled once, so
        per-realization rendering touches each token slot instead of
        re-scanning every line. Include items carry the original line, its
        indentation, and the quoted path (compiled for token interpolation);
        a malformed include line (no quoted path) keeps ``raw_path=None`` and
        is handled per realization exactly like before.
        """
        program = []
        text_run = []
        for line in unc_content:
            if not _is_include_line(line):
                text_run.append(line)
                continue
            if text_run:
                program.append(('text', _compile_parts(''.join(text_run))))
                text_run = []
            indent = line[:len(line) - len(line.lstrip())]
            try:
                raw_path = line.split('"')[1]
            except IndexError:
                raw_path = None
            program.append(('include', {
                'line': line,
                'indent': indent,
                'raw_path': raw_path,
                'path_parts': _compile_parts(raw_path) if raw_path is not None else None,
            }))
        if text_run:
            program.append(('text', _compile_parts(''.join(text_run))))
        return program

    def _generate_nav_files_parallel(self, work_items, template_program, unc_path):
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
                    unc_path, template_program,
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

    def _generate_nav_file_for_sample(self, unc_path, template_program, sample, simulation_name,
                                      scenario_parameters, realization_folder, sample_number):
        """
        Create one .nav file for a given sample + scenario.
        - Copies & replaces tokens in included .inc files when needed into simulation_includes/
        - Returns the nav filepath on success, or None on failure.
        """
        os.makedirs(realization_folder, exist_ok=True)
        # simulation_includes/ is created lazily, only when an include is
        # actually rewritten for this realization.
        simulation_includes_folder = os.path.join(realization_folder, "simulation_includes")

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

        unc_dir = os.path.dirname(unc_path)
        nav_content = []
        for kind, payload in template_program:
            if kind == 'text':
                nav_content.append(_render_parts(payload, replacements))
                continue

            # Include line: interpolate tokens in the path, then process the file
            line = payload['line']
            include_path_relative = payload['raw_path']
            if include_path_relative is not None:
                interpolated = _render_parts(payload['path_parts'], replacements)
                if interpolated != include_path_relative:
                    line = line.replace(include_path_relative, interpolated)
                    include_path_relative = interpolated
            else:
                # malformed Include line (no quoted path); handled below via fallback
                include_path_relative = ""

            full_include_path = os.path.normpath(os.path.join(unc_dir, include_path_relative))

            try:
                updated_line = self._process_and_update_include_file(
                    full_include_path,
                    replacements,
                    sample_number,
                    simulation_includes_folder,
                    realization_folder,
                    payload['indent']
                )
            except Exception:
                logger.exception("Failed to process include file: %s", full_include_path)
                updated_line = None

            # Fall back to the template line if the include could not be handled, but still
            # normalize the keyword so the .nav never carries a legacy spelling Navigate rejects.
            nav_content.append(updated_line if updated_line else _normalize_include_keyword(line))

        # Write nav file
        nav_filename = f"{simulation_name}.nav"
        nav_filepath = os.path.join(realization_folder, nav_filename)
        try:
            self._write_to_file(nav_filepath, ''.join(nav_content))
            return nav_filepath
        except Exception:
            logger.exception("Failed writing NAV file %s", nav_filepath)
            return None

    def generate_commands_list(self, solver=None, navigate_flags=None):
        """Generate list of commands to run NavigaTE on all generated .nav files.

        nav_filepaths from generation are already absolute (the output folder
        is absolutized at generation entry); isabs is a pure string check, so
        the common case pays no per-path abspath round trip.
        """
        solver_flag = f' --solver {solver}' if solver else ''
        extra_flags = f' {navigate_flags}' if navigate_flags else ''
        self.commands = [
            'navigate "{}"{}{}'.format(
                (path if os.path.isabs(path) else os.path.abspath(path)).replace(os.sep, '/'),
                solver_flag,
                extra_flags
            )
            for path in self.nav_filepaths
        ]

    def _process_and_update_include_file(self, include_file_path, replacements, sample_number,
                                         simulation_includes_folder, realization_folder,
                                         indent='\t'):
        """
        Process an include (.inc) file: replace tokens and optionally save to simulation_includes.

        `replacements` is the realization's unified token->string mapping (scenario
        and sampled values, pre-formatted). Returns a new `Include` line to be
        placed in the .nav file, or None if the include could not be handled.
        `indent` is the leading whitespace of the template line, carried over so
        the generated .nav keeps the template's indentation.
        """
        parts = self._compiled_include(include_file_path)

        # Tokens that will actually be replaced, unique, in first-appearance order
        # (the rewritten file is named after them).
        tokens_replaced = []
        seen = set()
        for i in range(1, len(parts), 2):
            token = parts[i]
            if token in replacements and token not in seen:
                seen.add(token)
                tokens_replaced.append(token)

        if tokens_replaced:
            # Name the file based on tokens that were actually replaced
            combined_tokens = '_'.join(tokens_replaced)
            new_file_name = f"{combined_tokens}_sample_{sample_number}.inc"
            new_file_path = os.path.join(simulation_includes_folder, new_file_name)
            try:
                os.makedirs(simulation_includes_folder, exist_ok=True)
                self._write_to_file(new_file_path, _render_parts(parts, replacements))
            except Exception:
                logger.exception("Failed writing modified include file %s", new_file_path)
                return None
            new_include_path_relative = os.path.join("simulation_includes", new_file_name).replace(os.sep, '/')
            return _format_include_line(new_include_path_relative, indent)

        # No tokens changed: include the original file with a relative path
        # from the .nav file's directory. The relative path is identical for
        # every realization of a run (all realization folders share one
        # parent), so the rendered line is computed once and cached.
        cache_key = (include_file_path, indent)
        line = self._include_line_cache.get(cache_key)
        if line is None:
            original_rel = os.path.relpath(
                include_file_path,
                realization_folder
            ).replace(os.sep, '/')
            line = _format_include_line(original_rel, indent)
            self._include_line_cache[cache_key] = line
        return line
