# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pueue task-state classification across pueue JSON dialects.

pueue changed its ``status --json`` schema between 3.x and 4.x:

* 3.x: lifecycle states are plain strings (``"status": "Running"``) and a
  finished task reports ``{"Done": "Success"}`` / ``{"Done": {"Failed": 1}}``.
* 4.x: every state is a dict carrying metadata
  (``{"Running": {"enqueued_at": ...}}``) and a finished task reports
  ``{"Done": {"enqueued_at": ..., "result": "Success"}}`` /
  ``{"Done": {..., "result": {"Failed": 1}}}``.

The 4.x payloads below were captured from a live pueue 4.0.4 daemon.
Regression: successful tasks were classified as failed under 4.x, so
``horizon --status`` reported failures for studies that completed cleanly.
"""

import pytest

from horizon.run.run_commands import (
    _task_failed,
    _task_is_queued,
    _task_is_running,
    _task_succeeded,
)

V4_SUCCESS = {
    "id": 0,
    "label": "scen_sample001",
    "status": {
        "Done": {
            "enqueued_at": "2026-08-27T10:54:18.821439730Z",
            "start": "2026-08-27T10:54:19.563165156Z",
            "end": "2026-08-27T10:54:19.867139493Z",
            "result": "Success",
        }
    },
}

V4_FAILED = {
    "id": 1,
    "label": "scen_sample002",
    "status": {
        "Done": {
            "enqueued_at": "2026-08-27T10:54:19.031658502Z",
            "start": "2026-08-27T10:54:19.565060769Z",
            "end": "2026-08-27T10:54:19.867223325Z",
            "result": {"Failed": 1},
        }
    },
}

V4_RUNNING = {
    "id": 2,
    "label": "scen_sample003",
    "status": {
        "Running": {
            "enqueued_at": "2026-08-27T10:54:19.237703299Z",
            "start": "2026-08-27T10:54:19.869253970Z",
        }
    },
}

V4_QUEUED = {
    "id": 3,
    "label": "scen_sample004",
    "status": {"Queued": {"enqueued_at": "2026-08-27T10:54:58.507912263Z"}},
}

V4_STASHED = {
    "id": 4,
    "label": "scen_sample005",
    "status": {"Stashed": {"enqueue_at": None}},
}

V3_SUCCESS = {"id": 0, "label": "a", "status": {"Done": "Success"}}
V3_FAILED = {"id": 1, "label": "b", "status": {"Done": {"Failed": 1}}}
V3_RUNNING = {"id": 2, "label": "c", "status": "Running"}
V3_QUEUED = {"id": 3, "label": "d", "status": "Queued"}


@pytest.mark.unit
class TestPueue4StatusShapes:
    """Captured pueue 4.0.4 payloads must classify correctly."""

    def test_success_is_succeeded(self):
        assert _task_succeeded(V4_SUCCESS) is True
        assert _task_failed(V4_SUCCESS) is False

    def test_failed_is_failed(self):
        assert _task_failed(V4_FAILED) is True
        assert _task_succeeded(V4_FAILED) is False

    def test_running(self):
        assert _task_is_running(V4_RUNNING) is True
        assert _task_is_queued(V4_RUNNING) is False
        assert _task_succeeded(V4_RUNNING) is False
        assert _task_failed(V4_RUNNING) is False

    def test_queued(self):
        assert _task_is_queued(V4_QUEUED) is True
        assert _task_is_running(V4_QUEUED) is False

    def test_stashed_is_none_of_the_buckets(self):
        assert not _task_is_queued(V4_STASHED)
        assert not _task_is_running(V4_STASHED)
        assert not _task_succeeded(V4_STASHED)
        assert not _task_failed(V4_STASHED)


@pytest.mark.unit
class TestPueue3StatusShapes:
    """The 3.x dialect must keep working."""

    def test_success_is_succeeded(self):
        assert _task_succeeded(V3_SUCCESS) is True
        assert _task_failed(V3_SUCCESS) is False

    def test_failed_is_failed(self):
        assert _task_failed(V3_FAILED) is True
        assert _task_succeeded(V3_FAILED) is False

    def test_running(self):
        assert _task_is_running(V3_RUNNING) is True
        assert _task_is_queued(V3_RUNNING) is False

    def test_queued(self):
        assert _task_is_queued(V3_QUEUED) is True
        assert _task_is_running(V3_QUEUED) is False
