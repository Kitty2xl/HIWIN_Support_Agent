# Ingestion defects (data-quality log)

> Copied into the repo from the deployment folder (`C:\Users\User_11\Desktop\HIWIN\INGESTION_DEFECTS.md`) on 2026-09-16 so it travels with the code.
> **Read this before re-ingesting any document.** The repairs described here were applied directly in the database (snapshot tables `hiwin_rag.repair_backup_*`); the pipeline itself was NOT changed, so re-running ingestion for these documents reintroduces every defect. See [MAINTENANCE.md](MAINTENANCE.md) §"Re-ingesting a document".


Data-quality problems found in the ingested corpus. Each entry records how it was found,
how to reproduce it, and what a correct ingest would produce — so a fix can be verified
rather than assumed.

Status: `OPEN` not yet fixed · `REPAIRED <date>` corrected in place, reversible from a
`hiwin_rag.repair_backup_*` snapshot · `FIXED <date>` corrected at the pipeline.

**All four entries below are REPAIRED in the database but the PIPELINE IS UNCHANGED.**
Re-ingesting any of these documents will reintroduce every one of them.

---

## 1. Ballscrew catalog p.12 (表3.1 / Table 3.1) — grid column-shifted, legend dropped

**Status:** REPAIRED 2026-08-18 (both language editions)
**Snapshots:** `repair_backup_20260818_130719`, `_131257` (tc), `_132638` (en)
**Affects:** `data_ballscrew`, `Ballscrew_ballscrew-(c)_tc` and `Ballscrew_ballscrew-(e)_en`,
`page_number = 12`

### 1a. The whole grid was shifted, not just the malformed rows

表3.1 is a 26 × 33 matrix — rows are 外徑 (nominal shaft diameter), columns are 導程 (lead).
The corner cell reads `導程 \ 外徑` as a diagonal split label, so **the axis names appear in
the opposite order to a naive reading**.

The first diagnosis counted rows whose cell count disagreed with the 33-column header — 14
in the Chinese edition, 22 in the English — and treated the rest as sound. That was wrong.
Ø32 has exactly 33 cells and is still shifted:

```
stored:  … 25:E,S/T  25.4:E  32:E,S/H  36:S,H  40:H   60:H
page:    … 25:E,S,T  25.4:E  32:E,S,H  40:S,H  50:H   64:H
```

Row Ø32 also begins `—, I, E,I` at lead 1 where the page has `—, —, I, E,I` — a one-column
left shift, consistent with the corner cell being consumed during extraction, which would
also produce the off-by-one cell counts. **Cell-count integrity does not imply alignment.**

### 1b. The 註 legend was dropped, leaving a WRONG decoding available

The page prints its legend directly under the grid:

```
註：E：外循環  I：內循環  S：Super S  H：端蓋  T：Super T
Note: E : External recirculation  I : Internal recirculation  S : Super S
      H : End Cap  T : Super T
```

Absent from both chunks. This is not a gap — the chunk still carries the 3.2.2 螺帽種類
flowchart caption, which defines **S as 單螺帽 (single nut)**. A model decoding a cell of
`E,I,S,T` from the chunk alone therefore reads S as a nut style when the table means the
Super S circulation series, and `T` is not defined at all. Note also that the flowchart and
the p.43 order code write Super S as **(C)**, so the two encodings genuinely differ.

### How it was recovered

Three approaches failed before one worked, and the failures are worth keeping:

1. **Parsing the stored markdown** — already shifted; nothing to recover from.
2. **Bucketing glyphs by nearest header/stub centre** — cells hold TWO text lines (`E,I`
   above `S,T`), so the lower line lands nearer the next diameter's label and letters
   migrate between rows.
3. **Bucketing by the table's ruling lines** — the page draws ~61 vertical rules for 33
   columns (doubled borders), so bands do not map 1:1 without merging coincident rules.
4. **What worked:** render the page in vertical BANDS, each composed with the 外徑 stub
   column and the 導程 header in the same image, then read them. Every mark then has both
   axis labels visible beside it, which is exactly what the sparse right-hand region lacks
   at full-page scale.

Verified by cross-check against approach 2: **215 cells identical, 57 where the glyph
method held a strict subset (its documented two-line failure), 0 where it saw anything the
visual read missed, 0 conflicts.**

### Verification for a pipeline fix

```
Ø32 lead 10 = E,I,S,T        Ø32 lead 20 = E,I,S,H,T
26 rows, 34 header cells (corner + 33 leads), no row with a differing cell count
the 註 / Note line present in the chunk text
```

---

## 2. Ballscrew catalog p.13 (循環數) — `E : 5.5卷` stored as `L : 5.5倍`

**Status:** REPAIRED 2026-08-18 · snapshot `repair_backup_20260818_132216`
**Affects:** `Ballscrew_ballscrew-(c)_tc`, `page_number = 13`

The 外循環 turns column runs A, B, C, D, E. The chunk keeps A–D inside the table and emits
a corrupted orphan line below it:

```
stored:   | D : 4.5卷 | … |
          L : 5.5倍                 <- wrong letter AND wrong unit
page:     D : 4.5卷
          E : 5.5卷
```

Both halves wrong: code letter `E` became `L`, and 卷 (turns) became 倍 (times). A `…E<n>`
circuit code becomes unresolvable, and 倍 read as a multiplier yields a wrong rigidity
figure.

---

## 3. Ballscrew catalog p.43 (規格表示法) — `D : 雙螺帽` dropped

**Status:** REPAIRED 2026-08-18 · snapshot `repair_backup_20260818_132216`
**Affects:** `Ballscrew_ballscrew-(c)_tc`, `page_number = 43`

螺帽型式 lists a pair; the chunk carries only the first:

```
page:     螺帽型式   S : 單螺帽    D : 雙螺帽
stored:              S : 單螺帽    (雙螺帽 absent)
```

Double-nut models become undecodable, and the natural fallback — guessing from the series
name — is the error class 核對 BS-08/BS-16 recorded.

---

## Root causes — three distinct mechanisms, three distinct fixes

Worth separating, because a fix aimed at one leaves the others in place.

**A. Font/CID mangling in the PDF text layer (defects 2 and 3).** Raw extraction returns
`D : ꧰螺䌧` for 雙螺帽, `넝㼪玎` for 高導程, `禸列` for 系列 — 125 U+FFFD on p.43 via pypdf.

*This does NOT mean the content is unrecoverable, and treating it that way cost time here.*
The corruption is in the font's ToUnicode mapping, which breaks **extraction** while the
glyphs still **render** correctly. Rendering the page and reading the image recovers
everything, including the exact strings extraction loses.

**B. Chunking/region loss (defect 1b).** p.12's 註 line extracts as clean Chinese and was
simply not carried into the chunk. No OCR improvement addresses this.

**C. Table reconstruction (defect 1a).** The grid's columns were shifted during conversion
to markdown. Independent of both A and B.

---

## Why no existing tool caught these

`audit_ingestion.py` measures per-token-class **recall** — whether tokens went missing.
None of these are losses:

* p.12 — every E/I/S/H/T is present, in the wrong cells. Recall reads as perfect.
* p.13 — `E : 5.5卷` → `L : 5.5倍` is a substitution; token count unchanged.
* p.43 — one short line, below any threshold.

Consequently `repair_pages.py --symbols` declines p.12 with `recall 1.00 -> 1.00, not
worth it`, which is correct by its own metric. **The audit is blind to corruption and
misalignment; it only sees absence.**

### Suggested detectors

Cheap, and each targets a mechanism the recall probe cannot see:

* **Cell-count integrity** — for every markdown table, assert each row's cell count equals
  the header's. Would have flagged 14 rows in tc p.12 and 22 in en p.12. *Necessary but not
  sufficient — Ø32 passes it and is still shifted.*
* **Half-pairs** — for every chunk containing `單螺帽`, assert `雙螺帽` is present; likewise
  for other paired legends. Would have flagged p.43.
* **Letter-sequence gaps** — for a code column running A, B, C, D, assert E is not missing
  while an unrelated letter appears orphaned nearby. Would have flagged p.13.
* **Legend presence** — a page whose tables are dense in single-letter cells should carry a
  line defining those letters. Would have flagged p.12's missing 註.

## Not yet audited

Whether other wide tables share defect 1a. 表4.5 (p.20), 表格8 (linear guideway p.23) and
the §6.5/6.6 spec tables are the ones reviewer findings lean on most, and the cell-count
check above would survey the whole corpus cheaply.
