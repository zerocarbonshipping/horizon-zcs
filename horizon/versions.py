"""
The versions Horizon works with: the Navigate it runs, the results schema it reads and the study schema it writes.

Navigate's contributing guide, Versions and releases, says how the three tools version what they share.
"""

from __future__ import annotations

import re
import subprocess

from horizon import __version__
from horizon.exceptions import StudyError

# the oldest Navigate whose command line Horizon uses: --set, --name, --output-dir and --version
NAVIGATE_MIN = (1, 1, 0)
# the version of Navigate's results file layout, its schema_version, Horizon reads and writes
SCHEMA_VERSION = 1
# the version of what Horizon adds to that layout in its study files, their study_schema_version
STUDY_SCHEMA_VERSION = 1

# what `navigate --version` prints, at any line breaks
_NAVIGATE_LINE = re.compile(r"Navigate\s+(\S+),\s+results\s+schema\s+(\d+)")
_RELEASE = re.compile(r"\d+(\.\d+)*")


def version_line() -> str:
    """Name the Horizon and the versions it works with, for --version."""
    navigate = ".".join(str(number) for number in NAVIGATE_MIN)

    return (
        f"Horizon {__version__}, runs Navigate {navigate} or later, reads results schema {SCHEMA_VERSION}, "
        f"writes study schema {STUDY_SCHEMA_VERSION}"
    )


def check_navigate(navigate: str) -> None:
    """
    Raise unless the Navigate at this path takes Horizon's arguments and writes a results schema Horizon reads.

    Navigate's --version prints its version and the schema it writes; a Navigate without --version predates the oldest
    Horizon runs. A development version, such as 1.1.0.dev0, counts as its release.

    Parameters
    ----------
    navigate : str
        Path of the navigate command the runs start.
    """
    oldest = ".".join(str(number) for number in NAVIGATE_MIN)

    try:
        done = subprocess.run([navigate, "--version"], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StudyError(f"'{navigate} --version' did not run: {error}") from error

    match = _NAVIGATE_LINE.search(done.stdout)
    if done.returncode != 0 or match is None:
        raise StudyError(f"{navigate} is older than Navigate {oldest}, which this Horizon needs: upgrade Navigate")

    version, schema = match.group(1), int(match.group(2))
    release = _RELEASE.match(version)
    if release is None or tuple(int(number) for number in release.group().split(".")) < NAVIGATE_MIN:
        raise StudyError(f"{navigate} is Navigate {version}; this Horizon runs {oldest} or later: upgrade Navigate")

    if schema != SCHEMA_VERSION:
        remedy = "upgrade Horizon" if schema > SCHEMA_VERSION else "upgrade Navigate"
        raise StudyError(
            f"{navigate} is Navigate {version}, which writes results schema {schema}; this Horizon reads schema "
            f"{SCHEMA_VERSION}: {remedy}"
        )
