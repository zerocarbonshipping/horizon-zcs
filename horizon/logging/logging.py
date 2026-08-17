# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Logging setup for Horizon: mirrors log messages to both console and file."""

import logging


def setup_logging(log_file_path):
    """
    Set up logging to output messages to both the console and a log file.
    Parameters:
        log_file_path: The path to the log file.
    """
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create file handler with write mode ('w') to overwrite the file each time
    file_handler = logging.FileHandler(log_file_path, mode='w')
    file_handler.setLevel(logging.DEBUG)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("Logging to %s (DEBUG), console (INFO)", log_file_path)
