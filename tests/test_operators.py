"""Operator canonicalisation.

These mappings materially change the answer to "which companies draw the most
power", so each is pinned by a test. Roughly 17% of the state's estimated load
was misattributed before this was fixed.
"""

from __future__ import annotations

import pytest

from data_center_dataset.normalize.classify import (
    CONFIDENCE_ALIAS,
    CONFIDENCE_TOKEN,
    CONFIDENCE_UNATTRIBUTED,
    CONFIDENCE_UNRESOLVED,
    UNATTRIBUTED_OPERATOR,
    resolve_operator,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # EPA NEI writes permit-style names; whitespace must not split an entity.
        ("RAGING WIRE", "NTT"),
        ("RAGING WIRE DATA CENTER", "NTT"),
        ("RagingWire", "NTT"),
        # Corporate abbreviations and property vehicles.
        ("DRT LAX10", "Digital Realty"),
        ("DIGITAL ALFRED, LLC", "Digital Realty"),
        ("200 PAUL AVENUE LLC, C/O DIGITAL REALTY TRUST", "Digital Realty"),
        ("1100 SPACE PARK, LLC", "Digital Realty"),
        ("QUALITY INVESTMENT PROPERTIES SANTA CLARA, LLC", "QTS"),
        ("CYXTERA COMMUNICATIONS  LLC SC4-5", "Cyxtera"),
        ("VXCHNGE - CA LLC", "vXchnge"),
        # Acquisitions already curated in the alias table.
        ("Telx", "Digital Realty"),
        ("VPLS", "Evocative"),
        ("Stack Infrastructure, Incorporated", "STACK Infrastructure"),
        ("Level(3) Los Angeles", "Lumen Technologies"),
    ],
)
def test_operator_resolves_to_expected_company(raw, expected):
    assert resolve_operator(raw)[0] == expected


def test_ragingwire_load_consolidates_under_ntt():
    """The specific bug: spaced and unspaced forms must agree."""
    assert resolve_operator("RAGING WIRE")[0] == resolve_operator("RagingWire")[0]


@pytest.mark.parametrize(
    "raw",
    [
        "XERES VENTURES, LP (SC1)",
        "2805 LAFAYETTE",
        "VDC V",
        "1101 SPACE PARK PARTNERS  LLC",
    ],
)
def test_unknown_property_vehicles_are_grouped_not_guessed(raw):
    operator, confidence = resolve_operator(raw)
    assert operator == UNATTRIBUTED_OPERATOR
    assert confidence == CONFIDENCE_UNATTRIBUTED


def test_switching_centre_does_not_match_the_operator_switch():
    """Regression: a plain substring test mapped this to the company "Switch"."""
    operator, _ = resolve_operator("AT&T Switching Center")
    assert operator == "AT&T"
    assert operator != "Switch"


def test_partial_token_match_requires_whole_tokens():
    # "Prime" must not swallow an unrelated name that merely contains the letters.
    assert resolve_operator("Primerica Financial")[0] != "Prime Data Centers"


def test_confidence_is_reported():
    assert resolve_operator("Equinix, Inc.")[1] == CONFIDENCE_ALIAS
    assert resolve_operator("QTS Santa Clara 1")[1] == CONFIDENCE_TOKEN
    assert resolve_operator("Totally Unknown Entity Xyz")[1] == CONFIDENCE_UNRESOLVED


def test_blank_operator_is_none():
    assert resolve_operator(None) == (None, CONFIDENCE_UNRESOLVED)
    assert resolve_operator("   ") == (None, CONFIDENCE_UNRESOLVED)
