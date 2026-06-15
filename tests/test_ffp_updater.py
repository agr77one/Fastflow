from __future__ import annotations

import ffp_updater
import pytest


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("v2.5.0-beta1", (2, 5, 1)),
    ],
)
def test_version_tuple_parses_and_pads(value, expected):
    assert ffp_updater.version_tuple(value) == expected
