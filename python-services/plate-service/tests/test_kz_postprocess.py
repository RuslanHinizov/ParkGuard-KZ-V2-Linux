"""
python-services/plate-service/tests/test_kz_postprocess.py

Spec §9.4 + §9.3 — the pure post-process layer. No Nomeroff, no Kafka.
"""

from __future__ import annotations

from src.kz_postprocess import FOREIGN_REGION_PENALTY, process_candidate


def test_kz_new_accepts_valid_plate() -> None:
    got = process_candidate(text="123ABC02", confidence=0.92, region_name="kz")
    assert got is not None
    assert got.plate_text == "123ABC02"
    assert got.format_type == "kz_new"
    assert got.region_code == "02"
    assert got.valid_format is True
    assert not got.is_diplomatic


def test_kz_new_fixes_cyrillic_and_ocr_confusion() -> None:
    # 'О' is Cyrillic, 'O' in letter slot should become correct, '8' in
    # letter slot should become 'B', '0' in digit slot unchanged.
    got = process_candidate(text="1О8АВС02", confidence=0.85, region_name="kz")
    assert got is not None
    assert got.plate_text == "108ABC02"
    assert got.valid_format is True


def test_foreign_region_confidence_downgraded() -> None:
    # Nomeroff misclassifies a valid KZ plate as "ru" with 0.9 →
    # post-process halves confidence to 0.45 but still surfaces the
    # signal so the CVI can rack up votes at reduced weight.
    got = process_candidate(text="123ABC02", confidence=0.9, region_name="ru")
    assert got is not None
    assert abs(got.plate_confidence - (0.9 * FOREIGN_REGION_PENALTY)) < 1e-6
    assert got.format_type == "kz_new"
    assert got.region_name == "ru"


def test_low_confidence_and_invalid_format_returns_none() -> None:
    got = process_candidate(
        text="!!!garbage!!!",
        confidence=0.2,
        region_name="kz",
        min_confidence=0.5,
    )
    assert got is None


def test_low_confidence_but_valid_format_still_returned() -> None:
    # Below the gate but still a recognisable plate — we surface it so
    # the violation-service can accumulate plate_votes (spec §5.3).
    got = process_candidate(
        text="555XYZ14",
        confidence=0.3,
        region_name="kz",
        min_confidence=0.5,
    )
    assert got is not None
    assert got.format_type == "kz_new"
    assert got.valid_format is True


def test_diplomatic_plate() -> None:
    got = process_candidate(text="D123AB", confidence=0.8, region_name="kz")
    assert got is not None
    assert got.format_type == "kz_diplo"
    assert got.is_diplomatic is True
    assert got.region_code is None       # diplo plates don't have a region
