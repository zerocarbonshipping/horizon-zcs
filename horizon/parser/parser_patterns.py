# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Regular expression patterns used to parse .hor configuration file blocks and attributes."""

import re

# Pattern to capture the start of each parameter block
param_block_pattern = re.compile(r'(ScenarioParameter|ContinuousParameter|DiscreteParameter)\s+"([^"]+)"\s*{')

# Individual patterns for each attribute
name_pattern = re.compile(r'name\s*=\s*"([^"]+)"')
token_pattern = re.compile(r'token\s*=\s*"([^"]+)"')
values_pattern = re.compile(r'values\s*=\s*\[([^]]+)]')
active_pattern = re.compile(r'active\s*=\s*(TRUE|FALSE)')
default_pattern = re.compile(r'default\s*=\s*([^\s,]+)')

# Per-parameter emissions patterns (optional)
emission_low_pattern = re.compile(r'emissions_low\s*=\s*([^\s,]+)')
emission_high_pattern = re.compile(r'emissions_high\s*=\s*([^\s,]+)')

# Patterns for DiscreteParameter specifics
probabilities_pattern = re.compile(r'probabilities\s*=\s*\[([^]]+)]')

# Patterns for ContinuousParameter specifics
low_val_pattern = re.compile(r'low_val\s*=\s*([^\s,]+)')
mid_val_pattern = re.compile(r'mid_val\s*=\s*([^\s,]+)')
high_val_pattern = re.compile(r'high_val\s*=\s*([^\s,]+)')
distribution_pattern = re.compile(r'distribution\s*=\s*(".*?"|\'.*?\'|[^\n,]+)')
decimals_pattern = re.compile(r'decimals\s*=\s*(-?\d+)')

# Horizon block patterns:
plot_pattern = re.compile(r'Plot\s*=\s*(TRUE|FALSE)')
max_parallel_workers_pattern = re.compile(r'MaxParallelWorkers\s*=\s*(\d+)')
random_seed_pattern = re.compile(r'RandomSeed\s*=\s*(\d+)')
sample_only_pattern = re.compile(r'SampleOnly\s*=\s*(TRUE|FALSE)')
