"""docs/contract.md names everything it promises: it fails when it falls behind."""

from __future__ import annotations

from pathlib import Path

import blesession
from blesession import CAUSES, FACT_KEYS, stages
from blesession import errors as errors_mod

DOC = (Path(__file__).parent.parent / "docs" / "contract.md").read_text()


def _missing(names):
    return sorted(n for n in names if f"`{n}`" not in DOC and f"`{n}" not in DOC)


def test_every_public_name_is_in_the_contract():
    assert not _missing(blesession.__all__)


def test_every_cause_key_is_in_the_contract():
    assert not _missing(CAUSES)


def test_every_radio_fact_key_is_in_the_contract():
    assert not _missing(FACT_KEYS)


def test_every_primary_stage_is_in_the_contract():
    assert not _missing(stages.ORDER)


def test_every_error_class_is_in_the_contract():
    classes = [
        name
        for name, obj in vars(errors_mod).items()
        if isinstance(obj, type) and issubclass(obj, errors_mod.BleSessionError)
    ]
    assert not _missing(classes)
