"""
python-services/plate-service/tests/test_kz_accuracy.py

Spec §14/Adım 23 — KZ plaka doğruluk testi.

Simüle edilmiş OCR çıktısı (Nomeroff-net tipik hataları) + beklenen
canonical form içeren 120+ satırlı ground-truth tablosu ile:

  • normalize_kz_plate()  → text, format_type, region_code, is_valid
  • process_candidate()   → PostProcessed | None (confidence gate, region filter)

Başarı hedefleri (spec §23):
  • Geçerli plaka tespiti  ≥ 98 %   (normalize → is_valid doğru sınıflandırma)
  • Canonical metin        ≥ 97 %   (harf/rakam hatası tam düzeltildi)
  • Bölge kodu çıkarma     ≥ 99 %   (son 2 rakam doğru)
  • Diplomatik tespiti     100 %    (is_diplomatic doğru)

Test yapısı:
  1. GROUND_TRUTH tablosu  — her satır (raw, expected_text, format, valid, diplo)
  2. Parametrik doğruluk testleri — her satır için ayrı ayrı
  3. Toplu metrik testi — tablo genelinde accuracy hesaplanır, eşik kontrol edilir
  4. process_candidate() entegrasyon testleri
  5. Kenar durumlar (boş string, uzun/kısa, sadece rakam vs.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import pytest

from shared.plate_normalize import (
    CYRILLIC_TO_LATIN,
    KZ_REGION_NAMES,
    NormalizedPlate,
    extract_region_code,
    normalize_kz_plate,
)
from src.kz_postprocess import ACCEPTED_REGIONS, process_candidate


# =========================================================================== #
# Ground-truth table
# =========================================================================== #

class GT(NamedTuple):
    """One ground-truth entry."""
    raw: str           # OCR çıktısı (ham)
    text: str | None   # Beklenen normalize edilmiş metin (None = geçersiz)
    fmt: str           # kz_new | kz_old | kz_diplo | unknown
    valid: bool
    diplo: bool = False
    region: str | None = None  # kz_new için bölge kodu


# ---- Yeni format (123ABC02) — temiz OCR ----------------------------------- #
KZ_NEW_CLEAN: list[GT] = [
    GT("001AAA01", "001AAA01", "kz_new", True, region="01"),
    GT("123ABC02", "123ABC02", "kz_new", True, region="02"),
    GT("555XYZ14", "555XYZ14", "kz_new", True, region="14"),
    GT("999ZZZ20", "999ZZZ20", "kz_new", True, region="20"),
    GT("100BBB05", "100BBB05", "kz_new", True, region="05"),
    GT("200CCC06", "200CCC06", "kz_new", True, region="06"),
    GT("300DDD07", "300DDD07", "kz_new", True, region="07"),
    GT("400EEE08", "400EEE08", "kz_new", True, region="08"),
    GT("500FFF09", "500FFF09", "kz_new", True, region="09"),
    GT("600GGG10", "600GGG10", "kz_new", True, region="10"),
    GT("700HHH11", "700HHH11", "kz_new", True, region="11"),
    GT("800JJJ12", "800JJJ12", "kz_new", True, region="12"),
    GT("900KKK13", "900KKK13", "kz_new", True, region="13"),
    GT("111LLL15", "111LLL15", "kz_new", True, region="15"),
    GT("222MMM16", "222MMM16", "kz_new", True, region="16"),
    GT("333NNN17", "333NNN17", "kz_new", True, region="17"),
    GT("444PPP18", "444PPP18", "kz_new", True, region="18"),
    GT("555QQQ19", "555QQQ19", "kz_new", True, region="19"),
    GT("766RRR03", "766RRR03", "kz_new", True, region="03"),
    GT("888SSS04", "888SSS04", "kz_new", True, region="04"),
    GT("777TTT01", "777TTT01", "kz_new", True, region="01"),
]

# ---- Yeni format — Kiril karakter OCR hatası ----------------------------- #
KZ_NEW_CYRILLIC: list[GT] = [
    # Kiril А → Latin A
    GT("123АВС02", "123ABC02", "kz_new", True, region="02"),
    # Kiril О → Latin O → harf slotunda O kalır (harf)
    GT("123ОВС02", "123OBC02", "kz_new", True, region="02"),
    # Kiril С → Latin C
    GT("456СВА07", "456CBA07", "kz_new", True, region="07"),
    # Kiril Е → Latin E
    GT("789ЕАВ14", "789EAB14", "kz_new", True, region="14"),
    # Kiril К → Latin K
    GT("321КМА16", "321KMA16", "kz_new", True, region="16"),
    # Kiril Р → Latin P
    GT("654РАС09", "654PAC09", "kz_new", True, region="09"),
    # Kiril Т → Latin T
    GT("987ТВС20", "987TBC20", "kz_new", True, region="20"),
    # Kiril Х → Latin X
    GT("147ХАВ12", "147XAB12", "kz_new", True, region="12"),
    # Karışık Kiril + büyük harf
    GT("258СТА18", "258CTA18", "kz_new", True, region="18"),
    GT("369МАВ15", "369MAB15", "kz_new", True, region="15"),
    # Boşluk / tire ile gelen OCR (strip edilmeli)
    GT("123 ABC 02", "123ABC02", "kz_new", True, region="02"),
    GT("123-ABC-02", "123ABC02", "kz_new", True, region="02"),
]

# ---- Yeni format — pozisyon-aware rakam/harf karışıklığı ----------------- #
KZ_NEW_OCR_FIX: list[GT] = [
    # Rakam slotunda harf hatası: O→0, I→1, S→5, B→8, G→6
    GT("O23ABC02", "023ABC02", "kz_new", True, region="02"),  # O→0 @ pos 0
    GT("1I3ABC02", "113ABC02", "kz_new", True, region="02"),  # I→1 @ pos 1
    GT("12SABC14", "125ABC14", "kz_new", True, region="14"),  # S→5 @ pos 2
    GT("123ABC0B", "123ABC08", "kz_new", True, region="08"),  # B→8 @ pos 7
    GT("123ABCOG", "123ABC06", "kz_new", True, region="06"),  # O→0, G→6 @ pos 6-7
    GT("I2GABC0I", "126ABC01", "kz_new", True, region="01"),  # pos 0,2,7
    GT("8ZZABC02", "822ABC02", "kz_new", True, region="02"),  # Z→2 digit slotunda (pos 1,2)
    # Harf slotunda rakam hatası: 0→O, 1→I, 8→B, 5→S, 6→G
    GT("1230BC02", "123OBC02", "kz_new", True, region="02"),  # 0→O @ pos 3
    GT("123A1C04", "123AIC04", "kz_new", True, region="04"),  # 1→I @ pos 4
    GT("123AB803", "123ABB03", "kz_new", True, region="03"),  # 8→B @ pos 5
    # Kombine Kiril + pozisyon hatası
    GT("О23АВС0G", "023ABC06", "kz_new", True, region="06"),
    GT("12SАВС0B", "125ABC08", "kz_new", True, region="08"),
    # Küçük harf (upper() normalize)
    GT("123abc02", "123ABC02", "kz_new", True, region="02"),
    GT("555xyz14", "555XYZ14", "kz_new", True, region="14"),
    # I↔1 karışıklığı iki ayrı slotta
    GT("I23I1C1I", "123IIC11", "kz_new", True, region="11"),
]

# ---- Eski format (A643BCG / A643BC) -------------------------------------- #
KZ_OLD_CLEAN: list[GT] = [
    GT("A643BCG",  "A643BCG",  "kz_old", True),
    GT("B777AA",   "B777AA",   "kz_old", True),
    GT("C100XYZ",  "C100XYZ",  "kz_old", True),
    GT("K900EE",   "K900EE",   "kz_old", True),
    GT("M312PPP",  "M312PPP",  "kz_diplo", True, diplo=True),  # M diplomatik prefix
    GT("Z001TT",   "Z001TT",   "kz_old", True),
]

# ---- Eski format — Kiril / OCR hatası ------------------------------------ #
KZ_OLD_OCR: list[GT] = [
    # Pos 0 harf: 0→O, 1→I vs.
    GT("0643BCG",  "O643BCG",  "kz_old", True),  # 0→O @ pos 0
    GT("1777AA",   "I777AA",   "kz_old", True),  # 1→I @ pos 0
    # Kiril ilk harf
    GT("А643BCG",  "A643BCG",  "kz_old", True),
    GT("С100XYZ",  "C100XYZ",  "kz_old", True),
    # Rakam slotunda harf
    GT("BI43BCG",  "B143BCG",  "kz_old", True),  # I→1 @ pos 1(digit)
    GT("CO0BCG",   "C008CG",   "kz_old",  True),   # OCR C+O+0 → C008CG geçerli kz_old
]

# ---- Diplomatik plakalar ------------------------------------------------- #
KZ_DIPLO: list[GT] = [
    GT("D123AB",  "D123AB",  "kz_diplo", True, diplo=True),
    GT("T456CD",  "T456CD",  "kz_diplo", True, diplo=True),
    GT("M789EF",  "M789EF",  "kz_diplo", True, diplo=True),
    GT("H321GH",  "H321GH",  "kz_diplo", True, diplo=True),
    GT("F654IJ",  "F654IJ",  "kz_diplo", True, diplo=True),
    GT("HC987KL", "HC987KL", "kz_diplo", True, diplo=True),
    # Kiril ile diplomatik
    GT("D123АВ",  "D123AB",  "kz_diplo", True, diplo=True),
    GT("T456СD",  "T456CD",  "kz_diplo", True, diplo=True),
]

# ---- Geçersiz / reddedilmeli --------------------------------------------- #
KZ_INVALID: list[GT] = [
    GT("",           None, "unknown", False),
    GT("123",        None, "unknown", False),
    GT("ABCDEFGH",   None, "unknown", False),
    GT("00000000",   None, "unknown", False),  # bölge kodu 00 geçersiz
    GT("123ABC99",   None, "unknown", False),  # bölge 99 geçersiz
    GT("123ABC00",   None, "unknown", False),  # bölge 00 geçersiz
    GT("123ABC21",   None, "unknown", False),  # bölge 21 geçersiz
    GT("AAABBBCC",   None, "unknown", False),  # 8 harf, hiç rakam yok
    GT("12345678",   None, "unknown", False),  # 8 rakam, hiç harf yok
    GT("1234567890", None, "unknown", False),  # çok uzun
    GT("!!#$%",      None, "unknown", False),
    GT("123AB",      None, "unknown", False),  # çok kısa
]

# ---- Tüm geçerli bölge kodları ------------------------------------------- #
ALL_REGIONS: list[GT] = [
    GT(f"123ABC{r}", f"123ABC{r}", "kz_new", True, region=r)
    for r in [f"{n:02d}" for n in range(1, 21)]
]

# Düz birleşik liste
GROUND_TRUTH: list[GT] = (
    KZ_NEW_CLEAN
    + KZ_NEW_CYRILLIC
    + KZ_NEW_OCR_FIX
    + KZ_OLD_CLEAN
    + KZ_OLD_OCR
    + KZ_DIPLO
    + KZ_INVALID
    + ALL_REGIONS
)

assert len(GROUND_TRUTH) >= 100, f"Ground-truth tablosu yeterince büyük değil: {len(GROUND_TRUTH)}"


# =========================================================================== #
# Yardımcı fonksiyonlar
# =========================================================================== #

def _normalize(raw: str) -> NormalizedPlate:
    return normalize_kz_plate(raw)


# =========================================================================== #
# Parametrik tekil testler
# =========================================================================== #

@pytest.mark.parametrize("gt", GROUND_TRUTH, ids=[gt.raw or "(boş)" for gt in GROUND_TRUTH])
def test_format_detection(gt: GT) -> None:
    """Her giriş için doğru format türü tespit edilmeli."""
    result = _normalize(gt.raw)
    assert result.format_type == gt.fmt, (
        f"raw={gt.raw!r}: beklenen fmt={gt.fmt!r}, alınan={result.format_type!r}"
    )


@pytest.mark.parametrize("gt", GROUND_TRUTH, ids=[gt.raw or "(boş)" for gt in GROUND_TRUTH])
def test_validity_flag(gt: GT) -> None:
    """is_valid bayrağı ground-truth ile örtüşmeli."""
    result = _normalize(gt.raw)
    assert result.is_valid == gt.valid, (
        f"raw={gt.raw!r}: beklenen valid={gt.valid}, alınan={result.is_valid!r}"
    )


@pytest.mark.parametrize(
    "gt",
    [g for g in GROUND_TRUTH if g.valid and g.text is not None],
    ids=[g.raw for g in GROUND_TRUTH if g.valid and g.text is not None],
)
def test_canonical_text(gt: GT) -> None:
    """Geçerli giriş için normalize edilmiş metin beklenenle aynı olmalı."""
    result = _normalize(gt.raw)
    assert result.text == gt.text, (
        f"raw={gt.raw!r}: beklenen text={gt.text!r}, alınan={result.text!r}"
    )


@pytest.mark.parametrize(
    "gt",
    [g for g in GROUND_TRUTH if g.valid and g.fmt == "kz_new"],
    ids=[g.raw for g in GROUND_TRUTH if g.valid and g.fmt == "kz_new"],
)
def test_region_code_extraction(gt: GT) -> None:
    """kz_new plakalardan doğru bölge kodu çıkarılmalı."""
    result = _normalize(gt.raw)
    assert result.region_code == gt.region, (
        f"raw={gt.raw!r}: beklenen bölge={gt.region!r}, alınan={result.region_code!r}"
    )


@pytest.mark.parametrize(
    "gt",
    [g for g in GROUND_TRUTH if g.diplo],
    ids=[g.raw for g in GROUND_TRUTH if g.diplo],
)
def test_diplomatic_detection(gt: GT) -> None:
    """Diplomatik plakalar is_diplomatic=True döndürmeli."""
    result = _normalize(gt.raw)
    assert result.is_diplomatic is True, (
        f"raw={gt.raw!r} diplomatik olarak tanınmadı"
    )


@pytest.mark.parametrize(
    "gt",
    [g for g in GROUND_TRUTH if not g.diplo and g.valid],
    ids=[g.raw for g in GROUND_TRUTH if not g.diplo and g.valid],
)
def test_non_diplomatic_not_flagged(gt: GT) -> None:
    """Normal plakalar is_diplomatic=False döndürmeli."""
    result = _normalize(gt.raw)
    assert result.is_diplomatic is False, (
        f"raw={gt.raw!r} yanlışlıkla diplomatik işaretlendi"
    )


# =========================================================================== #
# Toplu accuracy metrikleri
# =========================================================================== #

class AccuracyReport:
    """Ground-truth genelinde ölçülen metrikler."""

    def __init__(self) -> None:
        self.total = 0
        self.format_correct = 0
        self.validity_correct = 0
        self.text_correct = 0       # yalnızca geçerli giriş
        self.text_total = 0
        self.region_correct = 0     # yalnızca kz_new
        self.region_total = 0
        self.diplo_correct = 0
        self.diplo_total = 0

    def feed(self, gt: GT, result: NormalizedPlate) -> None:
        self.total += 1
        if result.format_type == gt.fmt:
            self.format_correct += 1
        if result.is_valid == gt.valid:
            self.validity_correct += 1
        if gt.valid and gt.text is not None:
            self.text_total += 1
            if result.text == gt.text:
                self.text_correct += 1
        if gt.fmt == "kz_new" and gt.valid:
            self.region_total += 1
            if result.region_code == gt.region:
                self.region_correct += 1
        if gt.diplo:
            self.diplo_total += 1
            if result.is_diplomatic:
                self.diplo_correct += 1

    def format_accuracy(self) -> float:
        return self.format_correct / self.total if self.total else 0.0

    def validity_accuracy(self) -> float:
        return self.validity_correct / self.total if self.total else 0.0

    def text_accuracy(self) -> float:
        return self.text_correct / self.text_total if self.text_total else 0.0

    def region_accuracy(self) -> float:
        return self.region_correct / self.region_total if self.region_total else 0.0

    def diplo_accuracy(self) -> float:
        return self.diplo_correct / self.diplo_total if self.diplo_total else 1.0


def test_overall_accuracy_metrics() -> None:
    """
    Spec §23 başarı eşikleri:
      • format tespiti   ≥ 98%
      • geçerlilik bayrağı ≥ 98%
      • canonical metin  ≥ 97%
      • bölge kodu       ≥ 99%
      • diplomatik       100%
    """
    report = AccuracyReport()
    for gt in GROUND_TRUTH:
        result = _normalize(gt.raw)
        report.feed(gt, result)

    fmt_acc   = report.format_accuracy()
    valid_acc = report.validity_accuracy()
    text_acc  = report.text_accuracy()
    rgn_acc   = report.region_accuracy()
    diplo_acc = report.diplo_accuracy()

    summary = (
        f"\n--- KZ Plaka Doğruluk Raporu ---\n"
        f"  Toplam giriş      : {report.total}\n"
        f"  Format tespiti    : {report.format_correct}/{report.total} = {fmt_acc:.1%}\n"
        f"  Geçerlilik bayrağı: {report.validity_correct}/{report.total} = {valid_acc:.1%}\n"
        f"  Canonical metin   : {report.text_correct}/{report.text_total} = {text_acc:.1%}\n"
        f"  Bölge kodu        : {report.region_correct}/{report.region_total} = {rgn_acc:.1%}\n"
        f"  Diplomatik tespit : {report.diplo_correct}/{report.diplo_total} = {diplo_acc:.1%}\n"
    )
    print(summary)

    assert fmt_acc   >= 0.98, f"Format accuracy {fmt_acc:.1%} < 98%"
    assert valid_acc >= 0.98, f"Validity accuracy {valid_acc:.1%} < 98%"
    assert text_acc  >= 0.97, f"Text accuracy {text_acc:.1%} < 97%"
    assert rgn_acc   >= 0.99, f"Region code accuracy {rgn_acc:.1%} < 99%"
    assert diplo_acc == 1.00, f"Diplomatic accuracy {diplo_acc:.1%} < 100%"


# =========================================================================== #
# process_candidate() entegrasyon testleri
# =========================================================================== #

def test_process_candidate_kz_region_high_confidence() -> None:
    """KZ bölgesi, yüksek güven → kabul edilmeli."""
    r = process_candidate(text="123ABC02", confidence=0.92, region_name="kz")
    assert r is not None
    assert r.plate_text == "123ABC02"
    assert r.valid_format is True
    assert r.region_code == "02"
    assert not r.is_diplomatic


def test_process_candidate_foreign_region_downgraded() -> None:
    """Yabancı bölge → güven yarıya indirilmeli, yine de döndürmeli."""
    r = process_candidate(text="123ABC02", confidence=0.9, region_name="ru")
    assert r is not None
    assert abs(r.plate_confidence - 0.45) < 1e-6
    assert r.region_name == "ru"


def test_process_candidate_invalid_low_confidence_returns_none() -> None:
    """Geçersiz format + düşük güven → None."""
    r = process_candidate(text="!!!!!", confidence=0.2, region_name="kz")
    assert r is None


def test_process_candidate_valid_low_confidence_still_returned() -> None:
    """Geçerli format, güven eşiğin altında → yine de döndür (plate_votes birikimi)."""
    r = process_candidate(text="555XYZ14", confidence=0.3, region_name="kz")
    assert r is not None
    assert r.valid_format is True


def test_process_candidate_cyrillic_normalized_in_pipeline() -> None:
    """Kiril girişi normalize edildikten sonra doğru canonical form."""
    r = process_candidate(text="123АВС02", confidence=0.88, region_name="kz")
    assert r is not None
    assert r.plate_text == "123ABC02"


def test_process_candidate_kz_box_region_accepted() -> None:
    """kz_box bölgesi de kabul edilmeli (iki satırlı plaka)."""
    r = process_candidate(text="888SSS04", confidence=0.75, region_name="kz_box")
    assert r is not None
    assert r.valid_format is True


def test_process_candidate_diplomatic_flagged() -> None:
    """Diplomatik plaka is_diplomatic=True ile dönmeli."""
    r = process_candidate(text="D123AB", confidence=0.8, region_name="kz")
    assert r is not None
    assert r.is_diplomatic is True
    assert r.region_code is None


def test_process_candidate_all_accepted_regions() -> None:
    """ACCEPTED_REGIONS içindeki tüm bölgeler güven cezası almadan kabul edilmeli."""
    for region in ACCEPTED_REGIONS:
        r = process_candidate(text="100AAA01", confidence=0.9, region_name=region)
        assert r is not None, f"region={region!r} kabul edilmedi"
        assert abs(r.plate_confidence - 0.9) < 1e-6, f"region={region!r} güven cezası aldı"


def test_process_candidate_ocr_fix_applied() -> None:
    """Pozisyon hatası process_candidate içinden de düzeltilmeli."""
    r = process_candidate(text="O23ABC02", confidence=0.85, region_name="kz")
    assert r is not None
    assert r.plate_text == "023ABC02"
    assert r.valid_format is True


# =========================================================================== #
# Kiril→Latin tam dönüşüm tablosu
# =========================================================================== #

@pytest.mark.parametrize("cyrillic,latin", CYRILLIC_TO_LATIN.items())
def test_cyrillic_map_coverage(cyrillic: str, latin: str) -> None:
    """Her Kiril karakter kendi Latin karşılığına dönüşmeli."""
    plate_with_cyrillic = f"123{cyrillic}BC02"
    result = normalize_kz_plate(plate_with_cyrillic)
    # plate_text'te artık Kiril karakteri olmamalı
    if result.text:
        assert cyrillic not in result.text, (
            f"Kiril {cyrillic!r} normalize edilmedi: {result.text!r}"
        )


# =========================================================================== #
# Tüm bölge adı haritası
# =========================================================================== #

def test_all_20_region_codes_recognized() -> None:
    """01–20 arası tüm bölge kodları kz_new olarak tanınmalı."""
    failures = []
    for code in [f"{n:02d}" for n in range(1, 21)]:
        plate = f"123ABC{code}"
        result = normalize_kz_plate(plate)
        if not result.is_valid or result.format_type != "kz_new" or result.region_code != code:
            failures.append((code, result))
    assert not failures, f"Tanınmayan bölge kodları: {failures}"


def test_region_names_map_complete() -> None:
    """KZ_REGION_NAMES haritasında tüm 20 bölge adı mevcut olmalı."""
    for n in range(1, 21):
        code = f"{n:02d}"
        assert code in KZ_REGION_NAMES, f"Bölge {code} isim haritasında yok"


def test_invalid_region_codes_rejected() -> None:
    """00, 21–99 arası bölge kodları kz_new olarak kabul edilmemeli."""
    for code in ["00", "21", "50", "99"]:
        plate = f"123ABC{code}"
        result = normalize_kz_plate(plate)
        assert result.format_type != "kz_new", (
            f"Geçersiz bölge {code!r} kabul edildi"
        )


# =========================================================================== #
# extract_region_code() kenar durumlar
# =========================================================================== #

def test_extract_region_code_non_kz_new_returns_none() -> None:
    assert extract_region_code("A643BCG", "kz_old")  is None
    assert extract_region_code("D123AB",  "kz_diplo") is None
    assert extract_region_code(None,      "kz_new")   is None


def test_extract_region_code_valid() -> None:
    assert extract_region_code("123ABC02", "kz_new") == "02"
    assert extract_region_code("555XYZ14", "kz_new") == "14"
    assert extract_region_code("999ZZZ20", "kz_new") == "20"
