# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Generation of NavigaTE .nav simulation files from .unc templates, with
%TOKEN% replacement and scenario-combination filtering."""

import collections
import concurrent.futures
import hashlib
import itertools
import logging
import os
import re
import threading
import time

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


def unquoted_default(scenario_parameter):
    """Default value of a scenario parameter with surrounding quotes stripped."""
    default = scenario_parameter.default
    return default.strip('"').strip("'") if isinstance(default, str) else default


def _short_hash(text):
    """Deterministic 8-hex-char tag used to disambiguate include names."""
    return hashlib.sha1(text.encode("utf-8", errors="surrogateescape")).hexdigest()[:8]


def _path_stem(path):
    """Filename of ``path`` without its extension."""
    return os.path.splitext(os.path.basename(path))[0]


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
        # Shared-include store state; configured per generate call.
        self._shared_written = set()
        self._shared_cfg = {'enabled': False, 'root': '', 'scenario_keys': set()}

    # -------------------------
    # Low-level file helpers
    # -------------------------
    @staticmethod
    def _write_to_file(filepath, content):
        """Write a string to filepath; the parent directory must already exist."""
        with open(filepath, 'w') as fh:
            fh.write(content)

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
        """Read and compile an include file (see _compile_parts), cached per handler.

        Worker threads may race on the first read; both compile the same
        parts, so no lock is needed.
        """
        parts = self._include_cache.get(filepath)
        if parts is None:
            try:
                with open(filepath, 'r') as fh:
                    parts = _compile_parts(fh.read())
            except FileNotFoundError:
                raise FileOperationError(f"Include file not found: {filepath}")
            self._include_cache[filepath] = parts
        return parts

    # -------------------------
    # Public API
    # -------------------------
    def generate_scenarios_and_nav_files(self, unc_path, sampled_parameters, scenario_parameters, output_folder,
                                         exclusion_rules=None, inclusion_rules=None, solver=None,
                                         navigate_flags=None, command_sink=None, max_workers=None,
                                         shared_includes=True):
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
        command_sink : object or None
            Optional streaming submitter (see run_commands.StreamingQueuer):
            ``start(expected_total)`` is called once before generation begins
            and ``submit(command)`` as each .nav completes, so queuing
            overlaps generation. ``self.commands`` is still populated either
            way; without a sink the caller queues it afterwards as before.
        max_workers : int or None
            Generation thread-pool size. Default: adaptive — ``min(8,
            cpu_count)`` on local disks (measured optimal at core count,
            where over- and undersubscription both cost real time), and
            ``min(64, 4 x cpu_count)`` when a startup probe of the output
            filesystem shows per-operation latency in network-storage
            territory, where generation is bound by file-creation round
            trips and blocked threads are free. An explicit value always
            wins (``horizon --gen-workers``; benchmark in tools/benchmark/).
        shared_includes : bool
            When True (default), a rewritten include whose replaced tokens
            are all scenario tokens — identical content for every sample of
            a scenario combination — is written once per combination under
            ``<output_folder>/shared_includes/<combination>/`` and referenced
            relatively, instead of being copied into every realization
            folder. Include-heavy decks on network filesystems save one file
            creation per such include per realization. Sample-dependent
            rewrites always stay per-realization in simulation_includes/.
        """
        # Realization folders are created under output_folder; absolutize once
        # so every derived path (and the navigate commands) is absolute.
        output_folder = os.path.abspath(output_folder)
        # the shared-include registry depends on the output folder, which can
        # differ between calls on a reused handler
        self._shared_written = set()

        # Read the UNC template once and compile it into a render program:
        # runs of plain lines become compiled literal/token segments, include
        # lines keep their raw text plus a compiled path for interpolation.
        try:
            with open(unc_path, 'r') as fh:
                unc_content = fh.readlines()
        except FileNotFoundError:
            raise FileOperationError(f"UNC template file not found: {unc_path}")

        template_program = self._compile_template(unc_content, unc_path)

        # inactive scenario parameters contribute only their default value
        scenario_keys = [scenario.token for scenario in scenario_parameters]
        scenario_values = [scenario.values if scenario.active else [unquoted_default(scenario)]
                           for scenario in scenario_parameters]

        # Pre-resolved mode: each sample already carries all scenario tokens
        # and yields exactly one NAV; otherwise every scenario combination is
        # generated for every sample.
        pre_resolved = bool(scenario_keys) and bool(sampled_parameters) and all(
            all(k in s for k in scenario_keys) for s in sampled_parameters)

        self._shared_cfg = {
            'enabled': bool(shared_includes),
            'root': os.path.join(output_folder, "shared_includes"),
            'scenario_keys': set(scenario_keys) | {"SCENARIO"},
        }

        # The include/exclude decision depends only on the scenario
        # combination, so evaluate the rules once per combination. This also
        # yields the expected realization count, which a streaming command
        # sink needs before the first submission.
        if pre_resolved:
            combination_counts = collections.Counter(tuple(s[k] for k in scenario_keys) for s in sampled_parameters)
        else:
            combination_counts = dict.fromkeys(itertools.product(*scenario_values), len(sampled_parameters))
        skip_by_combo = {
            combination: should_skip_combination(dict(zip(scenario_keys, combination)), exclusion_rules, inclusion_rules)
            for combination in combination_counts
        }
        expected_navs = sum(n for combination, n in combination_counts.items() if not skip_by_combo[combination])
        self.skipped_count += sum(skip_by_combo.values())
        labels = {combination: get_scenario_label(dict(zip(scenario_keys, combination)), inclusion_rules)
                  for combination in combination_counts}

        def _generate_work_items():
            if pre_resolved:
                rows = ((tuple(s[k] for k in scenario_keys), i, s) for i, s in enumerate(sampled_parameters, 1))
            else:
                rows = ((c, i, s) for c in combination_counts for i, s in enumerate(sampled_parameters, 1))

            for combination, sample_idx, sample in rows:
                if skip_by_combo[combination]:
                    continue

                scenario_dict = dict(zip(scenario_keys, combination))
                label = labels[combination]
                if label is not None:
                    scenario_dict["SCENARIO"] = label
                combo_name = label if label is not None else '_'.join(str(v) for v in combination)

                sample_suffix = sample_name = sample.get("sample", f"sample_{sample_idx}")
                sample_number = sample_idx
                if isinstance(sample_name, str) and sample_name.startswith("sample_"):
                    try:
                        sample_number = int(sample_name.split("_", 1)[1])
                        sample_suffix = f"sample{sample_number:03d}"
                    except ValueError:
                        sample_number = sample_idx

                yield sample, f"{combo_name}_{sample_suffix}", scenario_dict, sample_number, combo_name

        if command_sink is not None:
            command_sink.start(expected_navs)

        # Generate NAV files in parallel, consuming the generator on demand
        self._generate_nav_files_parallel(_generate_work_items(), template_program, unc_path,
                                          command_sink=command_sink, solver=solver,
                                          navigate_flags=navigate_flags,
                                          max_workers=max_workers, output_folder=output_folder)

        # After nav files created, prepare command list
        self.generate_commands_list(solver=solver, navigate_flags=navigate_flags)

    @staticmethod
    def _compile_template(unc_content, unc_path):
        """Compile template lines into a render program.

        Returns a list of ('text', parts) and ('include', info) items. Runs
        of consecutive non-include lines are merged and compiled once, so
        per-realization rendering touches each token slot instead of
        re-scanning every line. Include items carry the original line, its
        indentation, the quoted path ("" if malformed, so the file lookup fails
        and the template line is kept) compiled for token interpolation, and
        ``stem_suffix``.

        Rewritten copies of an include are named after the source file's stem
        so two different .inc files replacing the same tokens do not overwrite
        each other. ``stem_suffix`` is a hash of the source path when two
        static include paths share a stem or the path itself contains tokens
        (its resolution is unknown until rendering), otherwise empty.
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
            raw_path = line.split('"')[1] if '"' in line else ""
            program.append(('include', {
                'line': line,
                'indent': line[:len(line) - len(line.lstrip())],
                'raw_path': raw_path,
                'path_parts': _compile_parts(raw_path),
            }))
        if text_run:
            program.append(('text', _compile_parts(''.join(text_run))))

        unc_dir = os.path.dirname(unc_path)
        includes = [payload for kind, payload in program if kind == 'include']
        stem_sources = collections.defaultdict(set)
        for payload in includes:
            if len(payload['path_parts']) == 1:
                payload['full_path'] = os.path.normpath(os.path.join(unc_dir, payload['raw_path']))
                stem_sources[_path_stem(payload['full_path'])].add(payload['full_path'])
        for payload in includes:
            if len(payload['path_parts']) > 1:
                payload['stem_suffix'] = f"_{_short_hash(payload['raw_path'])}"
            elif len(stem_sources[_path_stem(payload['full_path'])]) > 1:
                payload['stem_suffix'] = f"_{_short_hash(payload['full_path'])}"
            else:
                payload['stem_suffix'] = ""
        return program

    @staticmethod
    def _probe_fs_latency(folder):
        """Median file-creation latency of the filesystem holding ``folder``.

        Times three create+write+close round trips of a tiny probe file -
        exactly the operation generation performs per output file. On local
        disks this is tens of microseconds; on network filesystems it is a
        metadata round trip of one to tens of milliseconds.
        """
        os.makedirs(folder, exist_ok=True)
        probe_path = os.path.join(folder, f".horizon_fs_probe_{os.getpid()}")
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            with open(probe_path, "w") as fh:
                fh.write("probe")
            samples.append(time.perf_counter() - start)
            os.remove(probe_path)
        samples.sort()
        return samples[1]

    # Per-op latency above which the output filesystem is treated as
    # network-backed and the pool is sized for latency hiding. Local disks
    # measure far below this even under load.
    _FS_LATENCY_THRESHOLD = 0.002

    def _select_worker_count(self, requested, output_folder):
        """Choose the generation pool size.

        An explicit request always wins. Otherwise the default is
        ``min(8, cpu)`` — measured optimal at core count on local disks,
        where the work is compute-bound and oversubscription costs real
        time. When the output filesystem shows per-operation latency in
        network-storage territory, generation is bound by file-creation
        round trips instead: threads blocked on I/O are free, so the pool
        is sized at ``min(64, 4 x cpu)`` to keep more operations in flight.
        """
        cpu_count = os.cpu_count() or 4
        if requested is not None:
            return max(1, requested)
        workers = min(8, cpu_count)
        try:
            latency = self._probe_fs_latency(output_folder)
        except OSError:
            return workers
        if latency >= self._FS_LATENCY_THRESHOLD:
            workers = min(64, cpu_count * 4)
            logger.info(
                "Output filesystem responds in ~%.1f ms per file operation "
                "(network storage?); using %d generation workers to hide the "
                "latency. Override with --gen-workers.",
                latency * 1000, workers)
        return workers

    def _generate_nav_files_parallel(self, work_items, template_program, unc_path,
                                     command_sink=None, solver=None, navigate_flags=None,
                                     max_workers=None, output_folder=None):
        """Generate NAV files using a thread pool for I/O-bound parallelism.

        Accepts any iterable of work items (including generators) to avoid
        materializing all realizations in memory at once. Items are submitted
        to the thread pool as they are yielded. With a ``command_sink``, each
        completed .nav's navigate command is submitted immediately, so
        queuing (and the first simulations) overlap generation.
        """
        def generate(item):
            try:
                return self._generate_nav_file_for_sample(unc_path, template_program, *item, output_folder)
            except Exception:
                logger.exception("Failed generating NAV file for %s", item[1])
                return None

        nav_filepaths = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self._select_worker_count(max_workers, output_folder)) as executor:
            for nav_filepath in executor.map(generate, work_items):
                if nav_filepath is None:
                    continue

                nav_filepaths.append(nav_filepath)
                if command_sink is not None:
                    command_sink.submit(self._build_command(nav_filepath, solver, navigate_flags))
                if len(nav_filepaths) % _PROGRESS_HEARTBEAT_INTERVAL == 0:
                    logger.info("File creation progress: %d NAV files generated...", len(nav_filepaths))

        self.nav_filepaths = nav_filepaths

    def _generate_nav_file_for_sample(self, unc_path, template_program, sample, simulation_name,
                                      scenario_parameters, sample_number, combo_name, output_folder):
        """
        Create one .nav file for a given sample + scenario.
        - Copies & replaces tokens in included .inc files when needed into simulation_includes/
        - Returns the nav filepath.
        """
        realization_folder = os.path.join(output_folder, simulation_name)
        # rewritten includes are collected and written after one makedirs: on
        # network filesystems every extra directory call is a round trip
        pending_includes = []

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

            # include line: interpolate tokens in the path, then process the file
            line = payload['line']
            include_path = payload['raw_path']
            full_include_path = payload.get('full_path')
            if full_include_path is None:
                include_path = _render_parts(payload['path_parts'], replacements)
                line = line.replace(payload['raw_path'], include_path)
                full_include_path = os.path.normpath(os.path.join(unc_dir, include_path))

            # collision-safe stem for rewritten copies (see _compile_template)
            name_stem = _path_stem(include_path) + payload['stem_suffix']

            try:
                updated_line = self._process_and_update_include_file(
                    full_include_path,
                    replacements,
                    sample_number,
                    realization_folder,
                    payload['indent'],
                    name_stem or "include",
                    combo_name,
                    pending_includes,
                )
            except Exception:
                logger.exception("Failed to process include file: %s", full_include_path)
                updated_line = None

            # Fall back to the template line if the include could not be handled, but still
            # normalize the keyword so the .nav never carries a legacy spelling Navigate rejects.
            nav_content.append(updated_line if updated_line else _normalize_include_keyword(line))

        nav_filepath = os.path.join(realization_folder, f"{simulation_name}.nav")
        includes_folder = os.path.join(realization_folder, "simulation_includes")
        os.makedirs(includes_folder if pending_includes else realization_folder, exist_ok=True)
        for file_name, content in pending_includes:
            self._write_to_file(os.path.join(includes_folder, file_name), content)
        self._write_to_file(nav_filepath, ''.join(nav_content))
        return nav_filepath

    def _shared_include_line(self, parts, replacements, tokens_replaced, name_stem,
                             combo_name, indent, realization_folder):
        """Write a scenario-only rewritten include once per scenario combination.

        The file lands in ``<output>/shared_includes/<combination>/`` and every
        realization of the combination references it relatively - realization
        folders already reference unmodified includes outside themselves, so
        this adds no new portability constraint, and include-heavy decks save
        one file creation per shared include per realization (the dominant
        generation cost on network filesystems).

        Concurrent first-writers may race on the same file; each writes the
        identical content to a private temp file and atomically renames it
        into place, so the shared file is complete the moment any thread
        returns a line referencing it (the streamed queue can start Navigate
        runs while generation continues).
        """
        shared_dir = os.path.join(self._shared_cfg['root'], combo_name)
        file_name = f"{name_stem}_{'_'.join(tokens_replaced)}.inc"
        shared_path = os.path.join(shared_dir, file_name)

        if shared_path not in self._shared_written:
            os.makedirs(shared_dir, exist_ok=True)
            tmp_path = f"{shared_path}.{threading.get_ident()}.tmp"
            self._write_to_file(tmp_path, _render_parts(parts, replacements))
            os.replace(tmp_path, shared_path)
            self._shared_written.add(shared_path)

        return _format_include_line(os.path.relpath(shared_path, realization_folder).replace(os.sep, '/'), indent)

    @staticmethod
    def _build_command(path, solver=None, navigate_flags=None):
        """Build one navigate command for a generated (absolute) .nav path."""
        solver_flag = f' --solver {solver}' if solver else ''
        extra_flags = f' {navigate_flags}' if navigate_flags else ''
        return f'navigate "{path.replace(os.sep, "/")}"{solver_flag}{extra_flags}'

    def generate_commands_list(self, solver=None, navigate_flags=None):
        """Generate list of commands to run NavigaTE on all generated .nav files."""
        self.commands = [self._build_command(path, solver, navigate_flags) for path in self.nav_filepaths]

    def _process_and_update_include_file(self, include_file_path, replacements, sample_number,
                                         realization_folder, indent, name_stem, combo_name,
                                         pending_includes):
        """
        Process an include (.inc) file: replace tokens and return the `Include` line for the .nav.

        `replacements` is the realization's unified token->string mapping (scenario
        and sampled values, pre-formatted). A sample-dependent rewrite is appended
        to `pending_includes` as (file name, content) for the caller to write into
        simulation_includes/; a scenario-only rewrite goes to the shared store.
        `indent` is the leading whitespace of the template line, carried over so
        the generated .nav keeps the template's indentation. `name_stem` is the
        collision-safe stem for the rewritten copy (see _compile_template);
        `combo_name` names the realization's scenario combination.
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
            # A rewrite that replaces only scenario tokens has identical
            # content for every sample of the combination: write it once per
            # combination in the shared store instead of once per realization.
            if (self._shared_cfg['enabled'] and combo_name
                    and set(tokens_replaced) <= self._shared_cfg['scenario_keys']):
                return self._shared_include_line(
                    parts, replacements, tokens_replaced, name_stem,
                    combo_name, indent, realization_folder)

            # Sample-dependent rewrite: one copy per realization, named by the
            # source stem plus the replaced tokens (the stem is what keeps two
            # different .inc files replacing the same token set from
            # overwriting each other).
            combined_tokens = '_'.join(tokens_replaced)
            new_file_name = f"{name_stem}_{combined_tokens}_sample_{sample_number}.inc"
            pending_includes.append((new_file_name, _render_parts(parts, replacements)))
            return _format_include_line(f"simulation_includes/{new_file_name}", indent)

        # no tokens changed: reference the original file relative to the .nav
        return _format_include_line(
            os.path.relpath(include_file_path, realization_folder).replace(os.sep, '/'), indent)
