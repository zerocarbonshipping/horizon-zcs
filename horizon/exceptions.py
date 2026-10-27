# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""The errors Horizon reports as one line and exit code 1."""


def error_line(hor_file, error):
    """One-line `file:line: error: message` for the CLI; errors without a location get the .hor file name."""
    return str(error) if getattr(error, "location", None) else f"{hor_file}: error: {error}"


class HorizonError(Exception):
    """Base of the errors the command line reports."""


class ParseError(HorizonError):
    """A .hor file that cannot be read or does not validate.

    Parameters
    ----------
    message : str
        What is wrong.
    location : str, optional
        ``file:line`` of the offending text; ``str(error)`` is then ``file:line: error: message``.
    """

    def __init__(self, message, location=None):
        super().__init__(message, location)
        self.message = message
        self.location = location

    def __str__(self):
        return f"{self.location}: error: {self.message}" if self.location else self.message


class StudyError(HorizonError):
    """A study folder, or the results of its runs, not in a state the command can work with."""
