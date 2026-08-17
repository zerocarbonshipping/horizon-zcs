# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Miscellaneous helpers: significant-digit rounding for display and CSV export of sampled parameters."""

import csv
import logging
import os
from math import floor, log10

TOLERANCE = 1e-9
logger = logging.getLogger(__name__)


def calculate_significant_digits(x):
    """
    Rounds off a value to the appropriate decimals for visual display.

    Parameters
    ----------
    x : float
        Value to be rounded for display.

    Returns
    -------
    float | int
        Rounded value.
    """

    abs_x = abs(x)

    if abs_x <= TOLERANCE:
        return 0

    significant = -int(floor(log10(abs_x)))

    if significant <= 0:
        return int(round(x, 0))

    return significant


def output_sampled_parameters_to_csv(sampled_parameters, parameter_types, filename="sampled_parameters.csv"):
    """
    Outputs simulation sets to a CSV file with an additional row indicating parameter types.

    Args:
        sampled_parameters (list of dict): The sampled parameters to output, where each dict contains parameter tokens
                                           as keys and sampled values as values.
        parameter_types (dict): A dictionary mapping parameter tokens to their types ("Continuous" or "Discrete").
        filename (str, optional): The filename of the output CSV file. Defaults to "sampled_parameters.csv".
    """
    # Ensure the filename ends with .csv
    if not filename.endswith('.csv'):
        filename += '.csv'

    # Ensure the directory exists
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    # Adjust headers to include a column for simulation names
    headers = list(sampled_parameters[0].keys()) if sampled_parameters else []
    headers = ["sample_number"] + headers  # Add "sample_number" as the first column

    # Prepare the type_row with "parameter_type" as the first entry
    type_row = [""] + ["parameter_type"] + [parameter_types.get(header, "") for header in headers[2:]]

    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)

        # Write the header row
        writer.writerow(headers)

        # Now, write the modified type_row
        writer.writerow(type_row)

        # Write each row of sampled parameters
        for i, row in enumerate(sampled_parameters):
            row_with_sample_number = {"sample_number": i + 1, **row}  # Add formatted sample number to the row
            writer.writerow([row_with_sample_number.get(header, "") for header in headers])

    logger.info("CSV written to %s", filename)
