# KZ Plate Formats — ParkGuard KZ Reference

## 1. Format overview

Kazakhstan uses three active plate format families plus a legacy series.

### 1.1 New standard (kz_new)  — since 2012

**Pattern:** `ddd LLL rr`  
- `ddd` — 3 digits (vehicle series)  
- `LLL` — 3 Latin uppercase letters  
- `rr`  — 2-digit region code (01–20)

**Examples:** `001AAA01`, `555XYZ14`, `999ZZZ20`

### 1.2 Old standard (kz_old)  — pre-2012, still on roads

**Pattern:** `L ddd LL[L]`  
- One leading letter, 3–4 digits, 2–3 trailing letters  
- No region suffix

**Examples:** `A643BCG`, `K900EE`, `M312PP`

### 1.3 Diplomatic (kz_diplo)

**Pattern:** `[DHFMTC] ddd LL[L]`  
- Prefix letter: **D** (diplomatic), **H** (honorary consul), **F** (foreign org), **M** (military attache), **T** (technical staff), **C** / **HC** (compound variants)  
- Followed by 3 digits and 2–3 letters

**Examples:** `D123AB`, `HC987KL`, `T456CD`

### 1.4 Unknown / invalid

Any plate that does not match the patterns above, or whose region code falls outside 01–20, is classified as `unknown` with `is_valid=False`.

---

## 2. Region codes

| Code | Oblast / City |
|------|---------------|
| 01 | Akmola |
| 02 | Aktobe |
| 03 | Almaty (Oblast) |
| 04 | Atyrau |
| 05 | East Kazakhstan |
| 06 | Zhambyl |
| 07 | West Kazakhstan |
| 08 | Karaganda |
| 09 | Kostanay |
| 10 | Kyzylorda |
| 11 | Mangystau |
| 12 | Pavlodar |
| 13 | North Kazakhstan |
| 14 | Turkestan |
| 15 | South Kazakhstan (historical) |
| 16 | Nur-Sultan / Astana |
| 17 | Almaty (City) |
| 18 | Shymkent |
| 19 | Abay |
| 20 | Zhetisu |

---

## 3. OCR normalization pipeline

Nomeroff-net OCR output passes through `normalize_kz_plate()` in `shared/plate_normalize.py`.

### 3.1 Cyrillic → Latin transliteration

Visually identical Cyrillic characters are mapped to their Latin equivalents before format matching:

| Cyrillic | Latin |
|----------|-------|
| А | A |
| В | B |
| Е | E |
| К | K |
| М | M |
| Н | H |
| О | O |
| Р | P |
| С | C |
| Т | T |
| У | Y |
| Х | X |
| І | I |

### 3.2 Position-aware digit/letter correction (kz_new)

Positions 0–2 are **digit slots**; positions 3–5 are **letter slots**; positions 6–7 are **digit slots** (region code).

| OCR error | Digit slot fix | Letter slot fix |
|-----------|---------------|-----------------|
| O / О     | → 0           | (valid letter)  |
| I / І     | → 1           | (valid letter)  |
| S         | → 5           | (valid letter)  |
| B         | → 8           | (valid letter)  |
| G         | → 6           | (valid letter)  |
| 0         | (valid digit) | → O             |
| 1         | (valid digit) | → I             |
| 8         | (valid digit) | → B             |
| 5         | (valid digit) | → S             |
| 6         | (valid digit) | → G             |

### 3.3 Whitespace / separator removal

Spaces, hyphens, and dots in OCR output are stripped before matching:  
`"123 ABC 02"` → `"123ABC02"`  
`"123-ABC-02"` → `"123ABC02"`

### 3.4 Case normalization

All characters are uppercased: `"123abc02"` → `"123ABC02"`

---

## 4. Accuracy thresholds (spec §23)

| Metric | Threshold |
|--------|-----------|
| Format detection | ≥ 98 % |
| Validity classification | ≥ 98 % |
| Canonical text (exact match after normalization) | ≥ 97 % |
| Region code extraction | ≥ 99 % |
| Diplomatic flag detection | 100 % |

Test coverage: 100 ground-truth entries in  
`python-services/plate-service/tests/test_kz_accuracy.py`

---

## 5. API fields

When a violation is written, plate fields are:

| Field | Type | Description |
|-------|------|-------------|
| `plate_text` | `str \| null` | Canonical normalized text |
| `plate_confidence` | `float \| null` | OCR model confidence (0–1) |
| `plate_format` | `str` | `kz_new` / `kz_old` / `kz_diplo` / `unknown` |
| `plate_region_code` | `str \| null` | `"01"`–`"20"` (kz_new only) |
| `plate_valid_format` | `bool` | True if format recognized and region valid |
| `is_diplomatic` | `bool` | True if kz_diplo format |

---

## 6. Confidence gating

`process_candidate()` in `plate-service` applies a confidence gate:

- **`confidence ≥ 0.75`** — plate accepted; CVI plate_text updated via vote.
- **`confidence < 0.75` and invalid format** — result discarded.
- **`confidence < 0.75` but valid format** — result still forwarded (logged as low-confidence).

The CVI plate vote requires a **majority** (≥ 3 of last 5 readings) before the plate is stabilised. This prevents single-frame OCR noise from creating wrong identities.
