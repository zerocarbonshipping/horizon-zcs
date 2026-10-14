# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for trailing-comment stripping and base-field extraction in the parser.

Covers:
- Trailing `#` comments are stripped (including ones containing the word "if"),
  but `#` inside quoted strings is kept.
- Base attributes are read only outside `if ... { ... }` override blocks.
"""

import pytest

from horizon.exceptions import ParseError
from horizon.parser.parser import _base_content, _strip_comment, parse_hor_file

HORIZON_BLOCK = """
Horizon {
    UncFilePath = "test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ScenarioParameter("policy")
    ContinuousParameter("OP_EE")
}
"""

SCENARIO_BLOCK = """
ScenarioParameter "policy" {
    name = "Policy"
    token = "policy"
    values = [bau, liberia, strict]
}
"""


def _parse(tmp_path, content):
    file_path = tmp_path / "test.hor"
    file_path.write_text(content)
    return parse_hor_file(str(file_path))


def _continuous(tmp_path, content):
    result = _parse(tmp_path, content)
    params = result[4]
    assert len(params) == 1
    return params[0]


# ============================================================================
# Test comment stripping
# ============================================================================

class TestStripComment:
    """Unit tests for the _strip_comment helper."""

    @pytest.mark.parametrize("line, expected", [
        ("default = 0.5  # if needed\n", "default = 0.5\n"),
        ("# full-line comment\n", "\n"),
        ('name = "Fuel #1"\n', 'name = "Fuel #1"\n'),
        ("name = 'Fuel #1'  # trailing\n", "name = 'Fuel #1'\n"),
        ('name = "it\'s #1" # c\n', 'name = "it\'s #1"\n'),
        ("low_val = 0.1\n", "low_val = 0.1\n"),
    ])
    def test_strip_comment(self, line, expected):
        assert _strip_comment(line) == expected


class TestTrailingComments:
    """Trailing comments in .hor files must not affect parsing."""

    def test_trailing_comment_with_if_in_parameter_block(self, tmp_path):
        """A trailing comment containing 'if' must not be parsed as an override header."""
        content = SCENARIO_BLOCK + """
ContinuousParameter "OP_EE" {
    name = "Operational EE"
    token = "OP_EE"
    distribution = UNIFORM
    low_val = 0.10  # lower bound, if applicable
    mid_val = 0.15
    high_val = 0.20
    decimals = 3
    default = 0.15  # if needed
    if policy in (bau, liberia) {
        low_val = 0.00  # if bau
        high_val = 0.10
    }
}
""" + HORIZON_BLOCK
        param = _continuous(tmp_path, content)

        assert param.low_val == pytest.approx(0.10)
        assert param.default == "0.15"
        assert len(param.overrides) == 1
        override = param.overrides[0]
        assert override["conditions"] == [{"token": "policy", "op": "in", "values": ["bau", "liberia"]}]
        assert override["attrs"] == {"low_val": 0.0, "high_val": 0.1}

    def test_hash_inside_quoted_string_is_kept(self, tmp_path):
        """A '#' inside a quoted string is not treated as a comment."""
        content = """
ContinuousParameter "OP_EE" {
    name = "Fuel #1"  # trailing comment
    token = "OP_EE"
    low_val = 0.10
    high_val = 0.20
    default = 0.15
    decimals = 3
}

Horizon {
    UncFilePath = "runs#1/test.unc"
    OutputPath = "output"
    NumberOfSamples = 10
    ContinuousParameter("OP_EE")
}
"""
        result = _parse(tmp_path, content)

        assert result[0] == "runs#1/test.unc"
        assert result[4][0].name == "Fuel #1"

    def test_horizon_block_trailing_comment(self, tmp_path):
        """Trailing comments in the Horizon block keep working."""
        content = """
ContinuousParameter "OP_EE" {
    name = "Operational EE"
    token = "OP_EE"
    low_val = 0.10
    high_val = 0.20
    default = 0.15
    decimals = 3
}

Horizon {
    UncFilePath = "test.unc"  # template
    OutputPath = "output"
    NumberOfSamples = 25  # samples per scenario
    SamplingMethod = MC  # select between LHS and MC
    RandomSeed = 7  # if unset, defaults to 69
    ContinuousParameter("OP_EE")
}
"""
        result = _parse(tmp_path, content)

        assert result[0] == "test.unc"
        assert result[2] == 25
        assert result[5] == "MC"
        assert result[8] == 7
        assert len(result[4]) == 1


# ============================================================================
# Test base fields vs. override blocks
# ============================================================================

class TestBaseFieldsIgnoreOverrides:
    """Base attributes must come from outside `if` blocks only."""

    def test_base_content_removes_override_blocks(self):
        block = """
    low_val = 0.1
    if policy = bau {
        mid_val = 0.2
    }
    high_val = 0.3
"""
        base = _base_content(block)
        assert "mid_val" not in base
        assert "low_val = 0.1" in base
        assert "high_val = 0.3" in base

    def test_required_field_only_in_if_block_raises(self, tmp_path):
        """low_val set only inside an override must raise the missing-field error."""
        content = SCENARIO_BLOCK + """
ContinuousParameter "OP_EE" {
    name = "Operational EE"
    token = "OP_EE"
    high_val = 0.20
    decimals = 3
    if policy in (bau, liberia) {
        low_val = 0.00
    }
}
""" + HORIZON_BLOCK
        with pytest.raises(ValueError, match="Missing low_val or high_val"):
            _parse(tmp_path, content)

    def test_mid_val_only_in_if_block_does_not_leak(self, tmp_path):
        """mid_val/default set only inside an override do not count as base values."""
        content = SCENARIO_BLOCK + """
ContinuousParameter "OP_EE" {
    name = "Operational EE"
    token = "OP_EE"
    low_val = 0.10
    high_val = 0.20
    decimals = 3
    if policy = strict {
        mid_val = 0.18
        default = 0.18
        distribution = "triangular"
    }
}
""" + HORIZON_BLOCK
        with pytest.raises(ParseError, match="no mid_val and no default"):
            _continuous(tmp_path, content)
