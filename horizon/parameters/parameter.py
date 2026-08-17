# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

# File: horizon/parameters/parameter.py
"""
Parameter classes for Horizon.

Contains:
 - BaseParameter
 - ScenarioParameter
 - ContinuousParameter (added 'overrides' support)
 - DiscreteParameter (added 'overrides' support)

Overrides are stored as a list on the parameter instance. Each override is expected
to be a dict of the form parsed by the parser's parse_overrides() helper:
    { "conditions": [...], "attrs": {...}, "start": int, "end": int }
"""
from horizon.exceptions import ParameterError


class BaseParameter:
    """
    A base class for simulation parameters.

    Attributes:
        name (str): Human-readable name of the parameter.
        token (str): A unique identifier used in simulation templates.
        active (bool): Indicates if the parameter is active in the current simulation.
        default: Default value to use when parameter is inactive.
    """

    def __init__(self, name, token, active=True, default=None):
        self.name = name
        self.token = token
        self.active = active
        self.default = default

    def shallow_copy_with_overrides(self, attrs):
        """Create a shallow copy of this parameter with specific attributes overridden.

        Much faster than copy.deepcopy() since parameter objects are simple data
        holders with immutable or shared-safe fields. Only the override attrs
        (numeric values like low_val, high_val, mid_val, default) are changed.

        Parameters
        ----------
        attrs : dict
            Attribute names and values to override on the copy.

        Returns
        -------
        BaseParameter
            A new instance of the same class with overridden attributes.
        """
        import copy
        new = copy.copy(self)  # shallow copy — shares lists/dicts by reference
        for k, v in attrs.items():
            setattr(new, k, v)
        return new


class ScenarioParameter(BaseParameter):
    """
    Represents a scenario parameter within a range of values.

    Args:
        name (str)
        token (str)
        values (list): Possible scenario values (strings)
        active (bool)
        default: default scenario value
    """

    def __init__(self, name, token, values, active, default):
        super().__init__(name, token, active, default)
        self.values = values

    def __repr__(self):
        return (f"ScenarioParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"values={self.values})")


class ContinuousParameter(BaseParameter):
    """
    Represents a continuous parameter with range and sampling metadata.

    New:
        overrides (list): optional list of per-scenario override dicts parsed from .hor (see parser)
    """

    def __init__(self, name, token, active, default, low_val, mid_val, high_val,
                 distribution='uniform', decimals=2, emissions_low=None, emissions_high=None,
                 overrides=None):

        super().__init__(name, token, active, default)
        self.low_val = low_val
        self.mid_val = mid_val
        self.high_val = high_val
        self.distribution = distribution
        self.decimals = decimals
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high
        self.overrides = overrides or []

    def __repr__(self):
        return (f"ContinuousParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"low_val={self.low_val}, mid_val={self.mid_val}, high_val={self.high_val}, distribution="
                f"{self.distribution}, decimals={self.decimals}), emissions_low={self.emissions_low}, "
                f"emissions_high={self.emissions_high}, overrides={self.overrides}")


class DiscreteParameter(BaseParameter):
    """
    Represents a discrete parameter with a set of possible values and probabilities.

    New:
        overrides (list): optional list of per-scenario override dicts parsed from .hor (see parser)
    """

    def __init__(self, name, token, active, default, values, probabilities,
                 emissions_low=None, emissions_high=None, overrides=None):
        super().__init__(name, token, active, default)

        # Validate that values list is not empty
        if not values:
            raise ParameterError(f"DiscreteParameter '{name}' has empty values list")

        self.values = values
        self.emissions_low = emissions_low
        self.emissions_high = emissions_high

        # Check if probabilities should be uniform
        if isinstance(probabilities, str) and probabilities.lower() == "uniform":
            uniform_probability = 1.0 / len(values)
            self.probabilities = [uniform_probability for _ in values]
        else:
            self.probabilities = probabilities

        self.overrides = overrides or []

    def __repr__(self):
        return (f"DiscreteParameter({self.name}, token={self.token}, active={self.active}, default={self.default}, "
                f"values={self.values}, probabilities={self.probabilities}, emissions_low={self.emissions_low}, "
                f"emissions_high={self.emissions_high}, overrides={self.overrides})")
