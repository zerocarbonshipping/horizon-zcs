# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""
Tests for horizon.file_handler.file_handler module.

Covers the format of the include directives Horizon writes into generated
.nav files. Navigate's grammar accepts the deck-level directive spelled
``Include`` (Title case) only, so an all-caps ``INCLUDE`` line makes the whole
deck fail to parse. These tests pin the emitted spelling.

Also covers:
- include-line detection (case-insensitive, word-boundary aware)
- token replacement in include paths and in the .inc files themselves
- indentation of the generated include lines
"""

import os

import pytest

from horizon.file_handler.file_handler import (
    FileHandler,
    _compile_parts,
    _format_include_line,
    _is_include_line,
    _normalize_include_keyword,
    _render_parts,
    extract_template_tokens,
)
from horizon.parameters.parameter import ScenarioParameter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture
def template_project(tmp_path):
    """A .unc template with a plain, a tokenized and a token-path include."""
    _write(tmp_path / "inc" / "plain.inc", 'Vessel "a" {\n    Length = 300\n}\n')
    _write(tmp_path / "inc" / "tokened.inc", 'Vessel "b" {\n    Length = %LEN%\n}\n')
    _write(tmp_path / "inc" / "policy_strict.inc", 'Vessel "c" {\n    Length = 100\n}\n')
    unc = _write(
        tmp_path / "template.unc",
        'DEFINE {\n'
        '    Load DefaultModelDefinition\n'
        '    Include "inc/plain.inc"\n'
        '    Include "inc/tokened.inc"\n'
        '    Include "inc/policy_%POLICY%.inc"\n'
        '}\n',
    )
    return unc


def _generate(unc_path, output_folder, samples, scenarios=()):
    handler = FileHandler()
    handler.generate_scenarios_and_nav_files(
        unc_path=str(unc_path),
        sampled_parameters=samples,
        scenario_parameters=list(scenarios),
        output_folder=str(output_folder),
    )
    return handler


def _include_lines(nav_path):
    return [ln for ln in open(nav_path).read().splitlines() if _is_include_line(ln)]


# ---------------------------------------------------------------------------
# Include line helpers
# ---------------------------------------------------------------------------
class TestIsIncludeLine:
    """Tests for _is_include_line()."""

    @pytest.mark.parametrize("line", [
        'Include "a.inc"',
        '    Include "a.inc"',
        '\tInclude "a.inc"',
        '\tINCLUDE "a.inc"',
        'include "a.inc"',
        'InClUdE "a.inc"',
    ])
    def test_matches_include_directives(self, line):
        assert _is_include_line(line)

    @pytest.mark.parametrize("line", [
        'IncludeRate = 5',
        'Includes = 3',
        'Load DefaultModelDefinition',
        '# Include "a.inc"',
        '',
        'Vessel "Include" {',
    ])
    def test_rejects_non_include_lines(self, line):
        assert not _is_include_line(line)


class TestNormalizeIncludeKeyword:
    """Tests for _normalize_include_keyword()."""

    def test_uppercase_is_rewritten(self):
        assert _normalize_include_keyword('\tINCLUDE "a.inc"\n') == '\tInclude "a.inc"\n'

    def test_lowercase_is_rewritten(self):
        assert _normalize_include_keyword('include "a.inc"\n') == 'Include "a.inc"\n'

    def test_indentation_is_preserved(self):
        assert _normalize_include_keyword('    INCLUDE "a.inc"\n') == '    Include "a.inc"\n'

    def test_already_correct_is_unchanged(self):
        line = '    Include "a.inc"\n'
        assert _normalize_include_keyword(line) == line

    def test_path_is_untouched(self):
        line = '\tINCLUDE "../inc/INCLUDE_dir/a.inc"\n'
        assert _normalize_include_keyword(line) == '\tInclude "../inc/INCLUDE_dir/a.inc"\n'


class TestFormatIncludeLine:
    """Tests for _format_include_line()."""

    def test_uses_title_case_keyword(self):
        assert _format_include_line("a.inc", "    ") == '    Include "a.inc"\n'

    def test_defaults_to_tab_indent(self):
        assert _format_include_line("a.inc") == '\tInclude "a.inc"\n'


# ---------------------------------------------------------------------------
# Generated .nav include format
# ---------------------------------------------------------------------------
class TestGeneratedNavIncludeFormat:
    """The include directives written into .nav files must match Navigate."""

    def test_all_include_lines_use_title_case(self, template_project, tmp_path):
        handler = _generate(
            template_project,
            tmp_path / "out",
            [{"sample": 1, "LEN": 250.0}],
            [ScenarioParameter(name="Policy", token="POLICY", active=True,
                               default="strict", values=["strict"])],
        )
        nav = handler.nav_filepaths[0]
        lines = _include_lines(nav)

        assert len(lines) == 3
        for line in lines:
            assert line.lstrip().startswith('Include "'), line
        assert "INCLUDE" not in open(nav).read()

    def test_untokenized_include_points_at_original_file(self, template_project, tmp_path):
        handler = _generate(template_project, tmp_path / "out", [{"sample": 1, "LEN": 250.0}])
        lines = _include_lines(handler.nav_filepaths[0])
        assert any(line.endswith('plain.inc"') for line in lines)

    def test_tokenized_include_points_at_rewritten_copy(self, template_project, tmp_path):
        handler = _generate(template_project, tmp_path / "out", [{"sample": 1, "LEN": 250.0}])
        nav = handler.nav_filepaths[0]
        lines = _include_lines(nav)

        rewritten = [line for line in lines if "simulation_includes/" in line]
        assert len(rewritten) == 1
        assert rewritten[0].lstrip().startswith('Include "')

        # the rewritten .inc really exists next to the .nav and has the token replaced
        path = rewritten[0].split('"')[1]
        resolved = os.path.join(os.path.dirname(nav), path)
        assert os.path.isfile(resolved)
        assert "250" in open(resolved).read()
        assert "%LEN%" not in open(resolved).read()

    def test_template_indentation_is_preserved(self, template_project, tmp_path):
        handler = _generate(template_project, tmp_path / "out", [{"sample": 1, "LEN": 250.0}])
        for line in _include_lines(handler.nav_filepaths[0]):
            assert line.startswith("    Include"), repr(line)

    def test_tab_indented_template_keeps_tabs(self, tmp_path):
        _write(tmp_path / "inc" / "plain.inc", 'Vessel "a" {\n    Length = 300\n}\n')
        unc = _write(tmp_path / "t.unc", 'DEFINE {\n\tInclude "inc/plain.inc"\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1}])
        assert _include_lines(handler.nav_filepaths[0])[0].startswith("\tInclude")

    def test_token_in_include_path_is_interpolated(self, template_project, tmp_path):
        handler = _generate(
            template_project,
            tmp_path / "out",
            [{"sample": 1, "LEN": 250.0}],
            [ScenarioParameter(name="Policy", token="POLICY", active=True,
                               default="strict", values=["strict"])],
        )
        lines = _include_lines(handler.nav_filepaths[0])
        assert any(line.endswith('policy_strict.inc"') for line in lines)
        assert not any("%POLICY%" in line for line in lines)


class TestCompiledRendering:
    """The template/include text is compiled once and rendered per realization;
    rendering must keep exactly the token-replacement semantics of the old
    per-line regex path."""

    def test_unknown_tokens_are_preserved(self):
        parts = _compile_parts("a %X% b %Y% c\n")
        assert _render_parts(parts, {"X": "1"}) == "a 1 b %Y% c\n"

    def test_text_without_tokens_is_unchanged(self):
        text = "no tokens here\nsecond line\n"
        assert _render_parts(_compile_parts(text), {"X": "1"}) == text

    def test_adjacent_tokens(self):
        assert _render_parts(_compile_parts("%A%%B%"), {"A": "1", "B": "2"}) == "12"

    def test_empty_replacement_value_is_used(self):
        assert _render_parts(_compile_parts("v=%A%;"), {"A": ""}) == "v=;"

    def test_rewritten_include_name_uses_first_appearance_order(self, tmp_path):
        """The rewritten copy is named after its source stem plus the replaced
        tokens in the order they first appear in the include file."""
        _write(tmp_path / "inc" / "two.inc", "x = %B%\ny = %A%\nz = %B%\n")
        unc = _write(tmp_path / "t.unc", 'DEFINE {\n\tInclude "inc/two.inc"\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1, "A": 1.0, "B": 2.0}])

        line = _include_lines(handler.nav_filepaths[0])[0]
        assert line.split('"')[1] == "simulation_includes/two_B_A_sample_1.inc"


class TestIncludeNameCollisions:
    """Two different .inc files replacing the same token set used to be
    rewritten to the same filename, silently overwriting each other inside a
    realization (both nav lines then pointed at whichever was written last,
    so the simulation ran with one include's content missing and the other's
    duplicated)."""

    def test_same_tokens_different_files_stay_distinct(self, tmp_path):
        _write(tmp_path / "inc" / "fuel.inc", "fuel_price = %FOO%\n")
        _write(tmp_path / "inc" / "demand.inc", "demand_level = %FOO%\n")
        unc = _write(tmp_path / "t.unc",
                     'DEFINE {\n\tInclude "inc/fuel.inc"\n\tInclude "inc/demand.inc"\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1, "FOO": 42.0}])

        nav = handler.nav_filepaths[0]
        paths = [line.split('"')[1] for line in _include_lines(nav)]
        assert len(set(paths)) == 2, f"include lines collide: {paths}"
        contents = {}
        for rel in paths:
            full = os.path.join(os.path.dirname(nav), rel)
            contents[rel] = open(full).read()
        assert any("fuel_price = 42" in c for c in contents.values())
        assert any("demand_level = 42" in c for c in contents.values())

    def test_same_stem_in_different_dirs_stays_distinct(self, tmp_path):
        _write(tmp_path / "a" / "policy.inc", "alpha = %FOO%\n")
        _write(tmp_path / "b" / "policy.inc", "beta = %FOO%\n")
        unc = _write(tmp_path / "t.unc",
                     'DEFINE {\n\tInclude "a/policy.inc"\n\tInclude "b/policy.inc"\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1, "FOO": 7.0}])

        nav = handler.nav_filepaths[0]
        paths = [line.split('"')[1] for line in _include_lines(nav)]
        assert len(set(paths)) == 2, f"include lines collide: {paths}"
        rendered = "".join(open(os.path.join(os.path.dirname(nav), rel)).read() for rel in paths)
        assert "alpha = 7" in rendered and "beta = 7" in rendered


class _RecordingSink:
    """Minimal command_sink implementing the StreamingQueuer protocol."""

    def __init__(self):
        self.started = []
        self.commands = []

    def start(self, expected_total):
        self.started.append(expected_total)

    def submit(self, command):
        self.commands.append(command)


class TestCommandSink:
    """generate_scenarios_and_nav_files streams commands into a sink so
    queuing can overlap generation."""

    def test_sink_gets_expected_total_and_every_command(self, template_project, tmp_path):
        sink = _RecordingSink()
        handler = FileHandler()
        handler.generate_scenarios_and_nav_files(
            unc_path=str(template_project),
            sampled_parameters=[{"sample": 1, "LEN": 250.0}, {"sample": 2, "LEN": 300.0}],
            scenario_parameters=[ScenarioParameter(name="Policy", token="POLICY", active=True,
                                                   default="strict", values=["strict"])],
            output_folder=str(tmp_path / "out"),
            solver="highs",
            command_sink=sink,
        )

        assert sink.started == [2]
        # the streamed commands are exactly the batch command list
        assert sorted(sink.commands) == sorted(handler.commands)
        assert all(cmd.endswith("--solver highs") for cmd in sink.commands)

    def test_without_sink_commands_are_still_built(self, template_project, tmp_path):
        handler = _generate(template_project, tmp_path / "out", [{"sample": 1, "LEN": 250.0}])
        assert len(handler.commands) == 1


class TestGenerationWorkers:
    """The generation pool size is overridable per machine."""

    def test_single_worker_produces_all_files(self, template_project, tmp_path):
        handler = FileHandler()
        handler.generate_scenarios_and_nav_files(
            unc_path=str(template_project),
            sampled_parameters=[{"sample": i, "LEN": 100.0 + i} for i in range(1, 6)],
            scenario_parameters=[],
            output_folder=str(tmp_path / "out"),
            max_workers=1,
        )
        assert len(handler.nav_filepaths) == 5
        assert len(handler.commands) == 5

    def test_zero_or_negative_workers_clamped(self, template_project, tmp_path):
        handler = FileHandler()
        handler.generate_scenarios_and_nav_files(
            unc_path=str(template_project),
            sampled_parameters=[{"sample": 1, "LEN": 250.0}],
            scenario_parameters=[],
            output_folder=str(tmp_path / "out"),
            max_workers=0,
        )
        assert len(handler.nav_filepaths) == 1


class TestLegacyTemplateNormalization:
    """Templates still using the old all-caps INCLUDE are normalized."""

    def test_legacy_uppercase_template_emits_title_case(self, tmp_path):
        _write(tmp_path / "inc" / "plain.inc", 'Vessel "a" {\n    Length = 300\n}\n')
        _write(tmp_path / "inc" / "tokened.inc", 'Vessel "b" {\n    Length = %LEN%\n}\n')
        unc = _write(
            tmp_path / "legacy.unc",
            'DEFINE {\n'
            '\tINCLUDE "inc/plain.inc"\n'
            '\tinclude "inc/tokened.inc"\n'
            '}\n',
        )

        handler = _generate(unc, tmp_path / "out", [{"sample": 1, "LEN": 250.0}])
        content = open(handler.nav_filepaths[0]).read()

        assert "INCLUDE" not in content
        assert content.count('Include "') == 2

    def test_missing_include_file_still_normalizes_keyword(self, tmp_path):
        """A dangling include keeps its path but must not keep a legacy keyword."""
        unc = _write(tmp_path / "legacy.unc", 'DEFINE {\n\tINCLUDE "inc/missing.inc"\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1}])
        content = open(handler.nav_filepaths[0]).read()

        assert '\tInclude "inc/missing.inc"\n' in content
        assert "INCLUDE" not in content

    def test_non_include_lines_are_left_alone(self, tmp_path):
        """`IncludeRate` is an attribute, not a directive, and must pass through."""
        unc = _write(tmp_path / "t.unc", 'DEFINE {\n\tIncludeRate = 5\n}\n')

        handler = _generate(unc, tmp_path / "out", [{"sample": 1}])
        assert "\tIncludeRate = 5\n" in open(handler.nav_filepaths[0]).read()


# ---------------------------------------------------------------------------
# Token extraction across includes
# ---------------------------------------------------------------------------
class TestExtractTemplateTokens:
    """Tests for extract_template_tokens()."""

    def test_collects_tokens_from_template_and_includes(self, template_project):
        tokens = extract_template_tokens(str(template_project))
        assert {"LEN", "POLICY"} <= tokens

    def test_follows_legacy_uppercase_include(self, tmp_path):
        _write(tmp_path / "inc" / "tokened.inc", "Length = %LEN%\n")
        unc = _write(tmp_path / "legacy.unc", 'DEFINE {\n\tINCLUDE "inc/tokened.inc"\n}\n')
        assert "LEN" in extract_template_tokens(str(unc))
