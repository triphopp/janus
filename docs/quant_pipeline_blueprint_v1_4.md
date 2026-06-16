---
title: "Quant Pipeline Framework — Implementation Blueprint"
version: "v1.4"
source_file: "quant_pipeline_blueprint_v1_4.html"
---

Implementation Blueprint · v1.4

# Layered pipeline สำหรับ *systematic* strategy validation

Spec สำหรับทีม coder ในการสร้าง validation pipeline ที่ใช้ได้ทั้ง equity, futures และ options โดยมี core เดียวร่วมกัน ส่วน asset-specific แยกเป็น adapter ทุก function signature, build order และ acceptance criteria ระบุไว้เพื่อ implement โดยไม่ต้องเดา

- Pattern · Layered + Adapter
- Layers · 5
- Core reuse · ~100%
- Options reuse · ~65%

- v1.4 · data versioning + available\_at
- transaction cost (3 ระดับ)
- attribution waterfall (options + equity)
- v1.3 · Greeks · tests · audit · Black-76

*01 / FOUNDATION*

## สถาปัตยกรรมแบบ Layered

5 ชั้น ไหลจากบนลงล่าง `ingestion/` ดึงข้อมูลจาก provider ภายนอกแล้วคืน raw frame มาตรฐาน `adapters/` เตรียมข้อมูลเฉพาะ asset `core/` ประมวลผลโดยไม่รู้จัก asset เลย

> **กฎข้อเดียว ที่ห้ามฝ่าฝืน:** `core/` ต้องไม่มี `if asset == ...` หรือชื่อ instrument จริง (เช่น `wti`, `cl`, `spx`) ปรากฏในโค้ดเลย ทุก function ใน core รับแค่ `DataFrame` + `cfg dict` ถ้าต้องเขียน asset logic ใน core แปลว่า logic นั้นต้องย้ายไป adapter

*Fig 1 — ห้าชั้น: provider → ingestion (ดึง+cache) → adapter (เตรียม) → core (ประมวลผล) → output*

*02 / FOUNDATION*

## โครงสร้างไฟล์

เพิ่ม `ingestion/` และตั้งชื่อแบบ instrument-family (ไม่ผูกกับ wti) ทุกไฟล์มีหน้าที่เดียวที่ชัดเจน

**project structure** — *directory layout*

```
# repository root
quant_pipeline/
├── run_pipeline.py          # entry point — argparse, โหลด config, เรียก ingestion+adapter+core
│
├── ingestion/               # ดึงข้อมูลภายนอก → raw frame มาตรฐาน
│   ├── base.py              # ProviderBase.fetch() contract + raw_schema validation
│   ├── settlement_loader.py # pipe-delimited EOD settlement (energy futures+options)
│   ├── equity_loader_a.py   # equity provider A (เช่น Yahoo สำหรับ POC/test) — raw vs adjusted
│   ├── equity_loader_b.py   # equity provider B (cross-check provider A)
│   ├── symbology.py         # PRODUCT_ID/HUB/CONTRACT → internal symbol map (กฎใน yaml)
│   ├── cache.py             # raw parquet cache + incremental update + PIT roll
│   └── versioned_cache.py   # NEW v1.4: immutable partition write + version read + manifest
│
├── core/                    # asset-agnostic — ห้ามมีชื่อ instrument จริง
│   ├── validators.py        # stage 1: logical bounds, missing, outlier
│   ├── stability.py         # stage 2: stationarity, distribution shift, feature quality
│   ├── splitter.py          # stage 3: walk-forward, purge/embargo, diversity gate (KL+JS)
│   ├── metrics.py           # stage 4: full metric set + per-fold/per-regime breakdown
│   ├── overfitting.py       # deflated sharpe, PBO, min track record
│   ├── regime.py            # rule-based label + transition + HMM/GMM validator
│   ├── pricing.py           # Black-76 / BS-Merton + IV solver (Brent)
│   ├── greeks.py            # delta/gamma/theta/vega + net greeks สำหรับ spread
│   ├── dte.py               # calendar/DTE convention (single source of truth)
│   ├── txcost.py            # NEW v1.4: 3-level transaction cost model
│   ├── attribution.py       # NEW v1.4: waterfall (Greek decompose / factor decompose)
│   └── audit.py             # lightweight before/after snapshot (row count, hash, key stats)
│
├── adapters/                # asset-aware — instrument family
│   ├── base.py              # AdapterBase: prepare() contract
│   ├── equity_adapter.py    # corp action, MAD clip, survivorship
│   ├── futures_adapter.py   # roll, term structure, session, scheduled events
│   ├── options_base.py      # OptionsBase: IV surface, greeks, PCP, VRP, skew (~65%)
│   ├── equity_options_adapter.py   # override: strike-adjust, NYSE close — ใช้ BS-Merton
│   └── futures_options_adapter.py  # override: roll, term structure — ใช้ Black-76
│
├── configs/                 # ทุก threshold + instrument spec อยู่ที่นี่
│   ├── equity.yaml          # family-level default
│   ├── futures.yaml
│   ├── instruments/         # per-instrument: ชื่อจริงอยู่ที่นี่เท่านั้น
│   │   ├── bz.yaml          # Brent — event_calendar ชี้ไป EIA/OPEC
│   │   ├── spx.yaml
│   │   └── aapl.yaml
│   ├── events/
│   │   ├── eia.csv          # scheduled event dates (data ไม่ใช่ code)
│   │   └── earnings.csv
│   └── symbology/           # NEW v1.3: mapping rules อยู่ใน yaml ไม่ใช่โค้ด
│       └── product_map.yaml # product_id ↔ contract_root ↔ hub (validate ทุกครั้งที่โหลด)
│
├── outputs/                 # artifact (gitignore)
│   ├── clean_data/    ├── stability_report/
│   ├── fold_manifest/ ├── perf_report/    # per-fold + per-regime
│   ├── attribution/   # NEW v1.4: waterfall per fold/regime (options + equity)
│   └── audit/         # lightweight before/after snapshot per stage
│
├── raw/                     # NEW v1.4: immutable versioned raw data (gitignore)
│   ├── bz/ingested_at=YYYY-MM-DD/settlement.parquet
│   ├── eq_a/ingested_at=YYYY-MM-DD/prices.parquet
│   └── _versions.jsonl      # manifest: ingested_at, rows, schema_hash, run_id
│
└── tests/                   # NEW v1.3: ขยายจาก stub เป็น test suite จริง
    ├── test_core/           # pricing, greeks, dte, splitter, metrics
    ├── test_adapters/       # prepare() contract tests + golden snapshots
    ├── test_ingestion/      # symbology join, schema, future/option split, PIT
    ├── test_dte/            # calendar convention edge cases (1st/last day, weekend, holiday)
    ├── golden/              # reference Greek/IV values (cross-checked vs QuantLib offline)
    └── fixtures/            # small synthetic+real-row samples (commit-safe)
```

*03 / FOUNDATION*

## Naming convention

ปัญหาเดิม: ชื่อ `wti_adapter`, `wti.yaml`, `flag_eia_events()` ผูกกับ instrument เดียว ทำให้เพิ่ม Brent หรือ Gas ต้อง copy โค้ด หลักการใหม่: **โค้ดตั้งชื่อตาม instrument family / behaviour ส่วนชื่อ instrument จริงอยู่ใน config เท่านั้น**

| เดิม (ผูก instrument) | ใหม่ (generic) | ชื่อ instrument จริงไปอยู่ที่ |
| --- | --- | --- |
| wti\_adapter.py | futures\_adapter.py | configs/instruments/cl.yaml |
| wti\_options\_adapter.py | futures\_options\_adapter.py | cl.yaml (มี options block) |
| wti.yaml | futures.yaml + instrument file | instruments/cl.yaml |
| flag\_eia\_events() | flag\_scheduled\_events() | cfg['event\_calendars'] → eia.csv |
| ovx (hardcoded) | cfg['vol\_col'] | cl.yaml: vol\_col: ovx |

ผลลัพธ์: เพิ่ม Brent (BZ) = เพิ่มไฟล์ `configs/instruments/bz.yaml` ไฟล์เดียว ไม่แตะโค้ดเลย เพราะ `futures_adapter` และ `futures_options_adapter` ไม่รู้จักคำว่า WTI อยู่แล้ว มันรู้แค่ว่า "instrument นี้ roll, มี term structure, มี event calendar ตามที่ config ชี้มา"

> **Litmus test:** ถ้า `grep -ri "wti\|eia\|ovx" core/ adapters/` แล้วเจอผลลัพธ์ใน `.py` ใดก็ตาม แปลว่ายังตั้งชื่อเจาะจงเกินไป — ชื่อพวกนี้ต้องอยู่ใน `configs/` เท่านั้น (โค้ดเจอได้เฉพาะใน `symbology.py` ที่ map PRODUCT\_ID/HUB/CONTRACT ของ provider จริง)

*04 / FOUNDATION*

## Interface contract

มี 2 contract: (1) **ingestion → adapter** คืน raw frame ตาม raw\_schema และ (2) **adapter → core** คืน `(df, cfg)` ที่ core ใช้ได้ทันที ทั้งสอง contract ต้องนิ่งก่อนเขียนโค้ดบรรทัดแรก

**contract 1 + 2** — *the two seams*

```
# ── contract 1: ingestion → adapter (raw_schema) ──
# รองรับ futures + options ในตารางเดียว แยกด้วย instrument_type
RAW_SCHEMA = {
    "as_of_date":    "datetime64[ns]",   # = TRADE DATE (PIT anchor) — settlement รู้ EOD
    "timestamp":     "datetime64[ns, UTC]|None", # เฉพาะ intraday; EOD = None
    "product_id":    "int",               # stable key (เช่น 254) — join ใช้ตัวนี้
    "contract_root": "str",               # B (Brent), CL (WTI) ...
    "hub":           "str",               # North Sea ...
    "instrument_type": "str",             # 'future' | 'option'
    "right":         "str|None",          # 'C'|'P' (option); None (future)
    "strike":        "float|None",        # None สำหรับ future
    "delivery_month": "datetime64[ns]",   # = STRIP — สำหรับ term structure + roll
    "expiry":        "datetime64[ns]",   # = EXPIRATION DATE — DTE/purge
    "price":         "float",             # = SETTLEMENT (ไม่ใช่ last-trade!)
    "net_change":    "float",
    "iv_provided":   "float|None",        # = OPTION_VOLATILITY (exchange-calc) — validate
    "delta_provided": "float|None",       # = DELTA_FACTOR
    "provider":      "str",             # settlement | yfinance | massive
}
# equity (Yahoo/Massive) ใช้ schema ย่อ: as_of_date, symbol, raw_close,
# adj_factor, volume, is_delisted — ไม่มี strike/expiry

# ── contract 2: adapter → core (cfg) ──
cfg = {
    "price_col": "price_std", "vol_col": "vol_std", "return_col": "return_std",
    "vol_window": 21, "trend_window": 126, "purge_bars": 5,
    "regime_axes": ["vol_regime", "vrp_sign"],
    "rf_rate_col": "sofr", "event_flags": [],
    "max_concentration": 0.80, "kl_threshold": 0.5, "js_threshold": 0.3,
}
```

core ไม่เคยอ้างชื่อ column จริง (`ovx`, `cl_m1`) — อ่าน `cfg["vol_col"]` แล้ว adapter ชี้ว่ามันคืออะไร นี่คือกลไกที่ทำให้ core ใช้ร่วม 100%

*05 / IMPLEMENTATION*

## Ingestion layer new

ชั้นที่ blueprint เดิมขาด — รับผิดชอบดึงข้อมูลจาก provider, normalize เป็น raw\_schema, cache และจัดการ survivorship/PIT แต่ละ provider มี schema ต่างกัน จึงแยกเป็น loader ต่อ provider โดยทุกตัว implement `ProviderBase` เดียวกัน

**ingestion/base.py** — *provider contract*

```
class ProviderBase(ABC):
    @abstractmethod
    def fetch(self, symbol: str, start, end) -> pd.DataFrame:
        """ดึง raw → normalize เป็น RAW_SCHEMA → คืน
        ห้ามทิ้ง expired series (survivorship). ต้องเป็น point-in-time"""
        ...
    @abstractmethod
    def list_expired(self, root: str, asof) -> list:
        """option series ที่หมดอายุก่อน asof — ต้องเก็บไว้ backtest"""
        ...

def load(symbol, start, end, cfg) -> pd.DataFrame:
    """เช็ค cache ก่อน → ถ้าไม่มี/ไม่ครบ fetch เฉพาะส่วนที่ขาด (incremental)
    → validate ตาม RAW_SCHEMA → เขียน parquet cache → คืน"""
    cached = cache.read(symbol, start, end)
    if cache.is_complete(cached, start, end):
        return cached
    gap = cache.missing_ranges(cached, start, end)
    fresh = _provider(cfg).fetch(symbol, *gap)   # rate-limit aware
    out = validate_schema(pd.concat([cached, fresh]), RAW_SCHEMA)
    cache.write(symbol, out); return out
```

### Field mapping — จากไฟล์จริง (pipe-delimited settlement)

ตัวอย่าง row จริง: `9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10.0000|63.46000|-1.71000|9/25/2024|254|0.01000|0.00000` — `settlement_loader.py` map column ดังนี้ (ชื่อ instrument จริงไม่หลุดเข้าโค้ด core)

| Source column | → standardized | หมายเหตุสำคัญ |
| --- | --- | --- |
| TRADE DATE | as\_of\_date | PIT anchor — settlement รู้ EOD ของวันนี้ (ไม่มี release lag) |
| HUB | hub | North Sea → ส่วนหนึ่งของ symbology |
| PRODUCT | (symbology) | "Brent Crude Futures" → resolve เป็น contract\_root |
| STRIP | delivery\_month | 11/1/2024 = เดือนส่งมอบ — แกนของ term structure + roll |
| CONTRACT | contract\_root | B = Brent |
| CONTRACT TYPE | right + instrument\_type | C/P → option; ว่าง/F → future (ตัวแยกหลัก) |
| STRIKE | strike | มีค่า → option; null → future |
| SETTLEMENT PRICE | price | **settlement ไม่ใช่ last-trade** — กระทบ timestamp audit (stage 3) |
| NET CHANGE | net\_change | ใช้ validate: price[t] − price[t−1] ควรใกล้ค่านี้ |
| EXPIRATION DATE | expiry | คำนวณ DTE → purge gap |
| PRODUCT\_ID | product\_id | **stable numeric key** — ใช้เป็น join key ไม่ใช่ชื่อ product |
| OPTION\_VOLATILITY | iv\_provided | IV จาก exchange — validate/ใช้ ไม่ต้อง re-solve เสมอ |
| DELTA\_FACTOR | delta\_provided | greek จาก exchange |

**ingestion/settlement\_loader.py** — *pipe parser + disambiguation*

```
def fetch(self, path, start, end) -> pd.DataFrame:
    df = pd.read_csv(path, sep="|")                       # pipe-delimited
    # US date M/D/YYYY → datetime (ห้ามให้ pandas เดา format)
    for c in ["TRADE DATE", "STRIP", "EXPIRATION DATE"]:
        df[c] = pd.to_datetime(df[c], format="%m/%d/%Y")
    # แยก future vs option จาก CONTRACT TYPE + STRIKE
    is_opt = df["CONTRACT TYPE"].isin(["C", "P"]) & df["STRIKE"].notna()
    df["instrument_type"] = np.where(is_opt, "option", "future")
    df.loc[~is_opt, ["STRIKE", "OPTION_VOLATILITY", "DELTA_FACTOR"]] = np.nan
    # settlement = EOD → as_of_date เป็น date, timestamp = None
    out = _rename_to_schema(df)                          # ตาม field mapping
    return validate_schema(out, RAW_SCHEMA)

# validate net_change: |price.diff() - net_change| ต้องเล็ก (ภายใน 1 tick)
```

### สิ่งที่ ingestion ต้องรับผิดชอบ (acceptance)

| หน้าที่ | ทำไมต้องอยู่ที่นี่ ไม่ใช่ adapter |
| --- | --- |
| **Survivorship** — เก็บ expired option/contract series | ถ้าทิ้งตั้งแต่ดึง adapter จะไม่มีทางกู้คืน → bias ถาวร |
| **PIT futures roll** — บันทึก roll ณ เวลาจริง | roll เป็นคุณสมบัติของ data ไม่ใช่ strategy ต้อง correct ก่อนถึง adapter |
| **Symbology resolve** — PRODUCT\_ID/HUB/CONTRACT → internal symbol | ชื่อ "Brent Crude Futures" ต้องไม่หลุดเข้า adapter/core; ใช้ product\_id เป็น key |
| **Future/option split** — แยกด้วย CONTRACT TYPE + STRIKE | ไฟล์เดียวมีทั้งสองชนิด ต้อง tag ก่อนส่งต่อ |
| **EOD vs intraday** — settlement = date-only | as\_of\_date เป็น date, timestamp = None; กัน bug สมมติว่ามี intraday |
| **Cache + incremental** — parquet, ดึงเฉพาะที่ขาด | แยกจาก logic การเตรียมข้อมูล |

### Provider เริ่มต้น + PIT caveat ที่ต้องระวัง

**หมายเหตุเรื่อง vendor:** ชื่อ provider ด้านล่างเป็นเพียง *ตัวเลือกสำหรับ POC/test* เพื่อให้เห็น input/output ก่อน–หลังของ adapter ตัวจริง ระบบไม่ผูกกับ vendor — เพิ่ม provider ใหม่ = เขียน `NewLoader(ProviderBase)` ใหม่ตัวเดียว ไม่แตะ core/adapter

| Provider (ตัวอย่าง) | ใช้กับ | PIT caveat ที่ต้อง handle |
| --- | --- | --- |
| **settlement file** (pipe) | energy futures + options (Brent/WTI) | settlement EOD (ไม่ใช่ last-trade); roll ต้อง PIT; เก็บ expired strikes |
| **equity provider A** (POC: Yahoo) | equity เริ่มต้นสำหรับ test pipeline | **Adj Close ถูกคำนวณย้อนหลังใหม่ทุก split/div** → ต้องเก็บ raw\_close + adj\_factor แยก, backtest ใช้ raw ณ t; **delisted ticker หายจาก feed** → survivorship, ต้องมี delisting list เอง |
| **equity provider B** (POC: Massive/วัตถุประสงค์ test) | cross-check provider A | ตรวจ adjustment convention ว่าตรงกันไหมก่อน merge — ความต่างเป็นสัญญาณว่าใครคำนวณผิด |
| **institutional feed** (prod: Databento/Polygon/…) | production เมื่อ POC ผ่าน | swap ที่ `ingestion/` เท่านั้น — adapter/core ไม่รู้ตัวว่า provider เปลี่ยน |

> **Adj Close PIT trap:** provider ฟรีบางตัว (เช่น Yahoo) คืน `Close` และ `Adj Close` — **ห้ามใช้ Adj Close ตรงๆ ใน backtest** เพราะมันคือราคาที่ปรับด้วยข้อมูลอนาคต (ทุก dividend/split ในอนาคตเปลี่ยนค่าทั้ง history) เก็บ `raw_close` + `adj_factor` แยกกัน แล้วให้ adapter สร้าง adjusted-on-the-fly ด้วยข้อมูลถึง t เท่านั้น caveat นี้ใช้กับทุก vendor ที่ทำ retroactive adjustment

*06 / IMPLEMENTATION*

## Core modules

เครื่องมือคณิตศาสตร์ครบระดับ institutional — stage 2 มี feature-quality & normality tests, stage 4 แยกเป็น metrics (วัด) + overfitting (กัน false discovery) แต่ละ function มี signature ตายตัวด้านล่าง

#### validators.py — stage 1

logical bound, completeness, cap outlier — point-in-time, peer-group

- logical\_bounds\_check(df, cfg)
- missing\_completeness(df, cfg)
- outlier\_cap(df, cfg)

#### stability.py — stage 2

stationarity, distribution shift, normality และ feature quality

- adf\_kpss\_check(series)
- arch\_lm\_test(series, lags)
- variance\_ratio\_test(series) new
- ljung\_box(series, lags) new
- jarque\_bera(series) new
- hurst\_exponent(series) new
- distribution\_shift(train, val)
- information\_coefficient(pred, fwd) new
- vif\_condition\_number(df) new
- sign\_consistency(df, cfg)

#### splitter.py — stage 3

walk-forward + purge/embargo + diversity gate (KL & JS)

- walk\_forward\_split(df, cfg)
- purge\_embargo(folds, df, cfg)
- regime\_diversity\_gate(folds, labels, cfg)
- combinatorial\_purged\_cv(df, cfg) new

#### metrics.py — stage 4

full metric set + per-fold/per-regime breakdown (ดู section 08)

- return\_metrics(returns)
- risk\_adjusted(returns, rf)
- drawdown\_metrics(equity) new
- distribution\_metrics(returns) new
- tail\_metrics(returns, α)
- hit\_metrics(returns) new
- per\_fold\_breakdown(...) new
- per\_regime\_breakdown(...) new
- stability\_score(per\_fold) new

#### overfitting.py — stage 4 · new

กัน false discovery จากการ test หลายกลยุทธ์ — ระดับ Lopez de Prado

- deflated\_sharpe\_ratio(sr, n\_trials, ...)
- prob\_backtest\_overfitting(ret\_matrix)
- min\_track\_record\_length(sr, target)

#### regime.py — all stages

Primary labeler = rolling rule-based เท่านั้น — HMM/GMM เป็น validator offline

- assign\_regime\_labels(df, cfg)
- compute\_transition\_matrix(labels)
- validate\_labels\_hmm(labels, df)
- diversity\_check\_gmm(windows)

### เครื่องมือคณิตศาสตร์ครบชุด — แยกตามวัตถุประสงค์

การประเมินว่า "math เพียงพอไหม": validation pipeline ที่รัดกุมต้องตอบ 4 คำถาม แต่ละข้อต้องมีเครื่องมือรองรับ ตารางนี้คือ checklist

| คำถามที่ pipeline ต้องตอบ | เครื่องมือ | stage |
| --- | --- | --- |
| feature นิ่งพอจะ model ไหม (stationary) | ADF, KPSS, Variance Ratio, Hurst | 2 |
| มี volatility clustering / autocorrelation ตกค้างไหม | ARCH-LM, Ljung-Box | 2 |
| feature กระจายตัวปกติไหม / fat tail แค่ไหน | Jarque-Bera, skew, kurtosis | 2 |
| train กับ val คนละ distribution ไหม | PSI, KS, Wasserstein, JS divergence | 2/3 |
| feature ทำนาย target ได้จริงไหม / collinear ไหม | Information Coefficient (IC, rank IC), VIF, condition number | 2 |
| fold มี regime หลากหลายพอไหม | KL divergence, JS divergence, concentration, unseen-regime | 3 |
| performance ดีจริงหรือฟลุ้คจากการ test เยอะ | Deflated Sharpe, PBO, Min Track Record Length | 4 |
| กำไรกระจายยังไงข้าม fold/regime (ไม่ใช่แค่ค่าเฉลี่ย) | per-fold/per-regime breakdown, stability score | 4 |

### KL divergence gate — ยังจำเป็น แต่ต้องคู่กับ JS

KL **ยังจำเป็น**ครับ แต่มีข้อจำกัดที่ต้องเข้าใจ: `KL(val‖train)` เป็น asymmetric และจะ **เป็นอนันต์**ทันทีถ้า val มี regime ที่ train ไม่มี (division by zero) — ซึ่งซ้อนกับ unseen-regime check อยู่แล้ว ดังนั้น KL ตัวเดียวจับได้แค่ "support ต่างกัน" แต่จับ "รูปร่าง distribution ต่างกันเมื่อ support เท่ากัน" ได้ไม่เสถียร

วิธีที่ถูกต้อง: ใช้ทั้งคู่ — KL จับ shift รุนแรง, JS divergence (symmetric, bounded 0–1) เป็นตัวหลักที่เสถียรกว่าและเทียบข้าม fold ได้ ทั้งสองอยู่ใน `regime_diversity_gate()`

**core/splitter.py** — *diversity gate · KL + JS*

```
def regime_diversity_gate(folds, labels, cfg) -> pd.DataFrame:
    """fail fold ถ้า: (1) unseen regime ใน val  (2) concentration > max
       (3) KL(val‖train) > kl_threshold  (4) JS(val,train) > js_threshold"""
    rows = []
    for i, (tr, va) in enumerate(folds):
        p = labels.iloc[va].value_counts(normalize=True)
        q = labels.iloc[tr].value_counts(normalize=True)
        unseen = set(p.index) - set(q.index)
        kl = _kl_div(p, q)                  # asymmetric — จับ shift รุนแรง
        js = _js_div(p, q)                  # symmetric bounded — เทียบข้าม fold
        ok = (not unseen) and p.max() <= cfg["max_concentration"] \
             and kl <= cfg["kl_threshold"] and js <= cfg["js_threshold"]
        rows.append({"fold": i, "pass": ok, "unseen": unseen,
                     "conc": p.max(), "kl": kl, "js": js})
    return pd.DataFrame(rows)
```

*07 / IMPLEMENTATION*

## Adapter layer

options math (IV, greeks, PCP, VRP, skew) เหมือนกันไม่ว่า underlying เป็น equity หรือ futures จึงใช้ inheritance: `OptionsBase` เก็บส่วนร่วม ~65% subclass override เฉพาะส่วนต่าง (ชื่อ generic แล้ว — ไม่มี wti)

**adapters/futures\_options\_adapter.py** — *override pattern · generic*

```
class FuturesOptionsAdapter(OptionsBase):
    def prepare(self, raw_df):
        df = self.build_continuous_futures(raw_df)        # futures-general
        df = self.flag_scheduled_events(df, self.cfg)    # อ่าน cfg['event_calendars']
        df = self.compute_term_structure(df)            # futures-general
        # iv_source: provided → ใช้ iv_provided จาก exchange (validate); else solve เอง
        df = self.build_iv_surface(df, self.cfg)        # inherited ✓ (เคารพ cfg['iv_source'])
        df = self.compute_greeks(df, self.cfg)          # inherited ✓ (ใช้ delta_provided ถ้ามี)
        df = self.compute_vrp_sign(df, self.cfg)        # inherited ✓
        cfg = {**self.cfg,
               "regime_axes": ["vol_regime", "term_structure",
                   "vrp_sign", "skew_direction"] + self.cfg["event_regimes"],
               "purge_bars": self.cfg["max_dte"]}
        return df, cfg

# bz.yaml (Brent) ชี้: iv_source: provided, event_calendars: [events/eia.csv, opec.csv]
# เพิ่ม WTI = สร้าง cl.yaml (iv_source: solve, vol_col: ovx) — โค้ดเดิม ไม่แก้อะไร
```

สังเกต 2 จุด: `flag_scheduled_events()` เป็น generic — Brent ชี้ EIA/OPEC, equity ชี้ earnings/FOMC ตัว function ไม่รู้จัก "EIA"; และ `build_iv_surface()` เคารพ `cfg['iv_source']` — data Brent ให้ `OPTION_VOLATILITY` มาแล้ว จึง validate แล้วใช้เลย ไม่ต้อง re-solve ทุกจุด (ประหยัดและตรงกับ exchange convention)

*08 / IMPLEMENTATION*

## Greeks & spread math new v1.3

v1.2 บอกแค่ `iv_source: provided | solve` แต่ไม่ได้ระบุ *วิธี solve* และ *วิธี net Greeks สำหรับ spread* ส่วนนี้แก้ปัญหานั้น — ทุกอย่างอยู่ใน `core/pricing.py` + `core/greeks.py` ไม่ผูกกับ asset ใด (adapter เลือก model ผ่าน `cfg['pricing_model']`)

### เลือก model ตาม underlying

| Underlying | Pricing model | ทำไม | cfg |
| --- | --- | --- | --- |
| Equity (ไม่มี div) | Black-Scholes | spot ราคาที่ใช้ซื้อ-ขายได้ตรง | `pricing_model: bs` |
| Equity (มี div) / Index | BS-Merton (q=div yield) | discount forward ด้วย div yield | `pricing_model: bsm` |
| Futures options (Brent, WTI, ฯลฯ) | **Black-76** | underlying คือ futures (no carry); ใช้ BS ตรงๆ จะผิด — เพราะ drift ≠ r | `pricing_model: black76` |

> **Black-76 ≠ BS:** ความผิดพลาดที่พบบ่อย: ใช้ Black-Scholes คำนวณ Greeks ของ futures options โดยใส่ futures price แทน spot — **delta จะผิดประมาณ `e^(rT)` เท่า** และ theta จะรวม cost-of-carry ของ underlying ที่ไม่มีจริง สำหรับ Brent/WTI ที่ DTE ~30 วันที่ r=5% ค่าผิดประมาณ 0.4% ของ delta — ดูเล็ก แต่พอเอามารวมกับ leverage ของ spread แล้วชี้ทิศทาง P&L ผิด

### Black-76 — formula สำหรับ futures options

ราคา call: `C = e^(-rT) · [F·N(d₁) − K·N(d₂)]`; put: `P = e^(-rT) · [K·N(-d₂) − F·N(-d₁)]` โดย `d₁ = [ln(F/K) + ½σ²T] / (σ√T)`, `d₂ = d₁ − σ√T`

**core/pricing.py** — *Black-76 + BS-Merton (single API)*

```
def price(model, S_or_F, K, T, r, sigma, right, q=0.0) -> float:
    """ราคา option ที่ค่าเดียว — adapter เรียกผ่าน vectorize() อีกชั้น"""
    if model == "black76":
        F = S_or_F                                  # futures price, ไม่ใช่ spot
        d1 = (np.log(F/K) + 0.5*sigma**2*T) / (sigma*np.sqrt(T))
        d2 = d1 - sigma*np.sqrt(T)
        disc = np.exp(-r*T)
        if right == "C": return disc*(F*norm.cdf(d1) - K*norm.cdf(d2))
        else:            return disc*(K*norm.cdf(-d2) - F*norm.cdf(-d1))
    elif model in ("bs", "bsm"):
        S = S_or_F
        d1 = (np.log(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
        d2 = d1 - sigma*np.sqrt(T)
        if right == "C":
            return S*np.exp(-q*T)*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)
        else:
            return K*np.exp(-r*T)*norm.cdf(-d2) - S*np.exp(-q*T)*norm.cdf(-d1)
    raise ValueError(f"unknown model {model}")

def solve_iv(model, mkt_price, S_or_F, K, T, r, right, q=0.0,
              bounds=(1e-4, 5.0), tol=1e-6) -> float:
    """Brent root-find — ทนกว่า Newton เมื่อ vega ใกล้ 0 (deep ITM/OTM, T→0).
       คืน NaN ถ้า: arb violation (มี intrinsic > mkt_price), bounds ไม่ครอบ root"""
    intrinsic = max(0.0, (S_or_F - K) if right == "C" else (K - S_or_F))
    if mkt_price < intrinsic * np.exp(-r*T) - tol:
        return np.nan                            # arb — log แล้วข้าม
    f = lambda s: price(model, S_or_F, K, T, r, s, right, q) - mkt_price
    try:    return brentq(f, *bounds, xtol=tol)
    except ValueError: return np.nan      # root นอก bounds → log + skip
```

### Greeks — formula closed-form (ห้ามใช้ numerical bump เป็น primary)

| Greek | Black-76 (call) | BS-Merton (call) | หมายเหตุ |
| --- | --- | --- | --- |
| **Delta** | `e^(-rT)·N(d₁)` | `e^(-qT)·N(d₁)` | Black-76 delta < BS เสมอ (ด้วย discount factor) |
| **Gamma** | `e^(-rT)·φ(d₁) / (F·σ·√T)` | `e^(-qT)·φ(d₁) / (S·σ·√T)` | per $1 ของ underlying |
| **Vega** | `e^(-rT)·F·φ(d₁)·√T` | `e^(-qT)·S·φ(d₁)·√T` | per 1.00 vol unit (หาร 100 = per vol point) |
| **Theta** | มี 2 term: vega-decay + interest | มี 3 term: vega-decay + interest + div | per 1 ปี — หาร 365 หรือ 252 ตาม convention |
| **Rho** | `-T·C` (rate ติด disc) | `K·T·e^(-rT)·N(d₂)` | เล็กในระยะสั้น แต่สำคัญใน calendar spread |

**ทำไมห้ามใช้ bump?** bump method `(price(σ+ε) − price(σ−ε)) / 2ε` จะมี noise floor ที่ระดับ `ε` และผิดเป็นระบบใกล้ T→0 หรือ deep ITM/OTM ใช้ closed-form เป็น primary, bump เป็น sanity check ใน test เท่านั้น (tolerance ≤ 1e-4)

### Net Greeks สำหรับ spread (calendar เป็นหลัก)

spread = ผลรวมเชิงเส้นของ leg แต่ละ leg มีน้ำหนัก ±qty ตามทิศทาง (+1 = long, −1 = short) net Greek ไม่ใช่แค่ `sum(greek)` — มี subtlety 3 จุดสำหรับ calendar spread:

**core/greeks.py** — *net greeks · spread-aware*

```
def net_greeks(legs: list[Leg], cfg) -> dict:
    """legs = [Leg(qty, right, K, expiry, F_at_t, iv_at_t, T_at_t), ...]
       qty = +1 long / -1 short / +n multi-contract"""
    g = {"delta":0, "gamma":0, "theta":0,
         "vega_total":0, "vega_short_term":0, "vega_long_term":0}
    for L in legs:
        leg_g = single_leg_greeks(L, cfg)              # closed-form ตามตารางข้างบน
        for k in ("delta","gamma","theta"):
            g[k] += L.qty * leg_g[k]
        # ── vega ต้องแยก bucket ── เพราะ calendar = long-vega ขายาว / short-vega ขาสั้น
        # แต่ vega ของขายาวกับขาสั้น "ไม่ใช่ของอย่างเดียวกัน": IV term-structure
        # shift ไม่ขนานกัน — short-end เคลื่อนแรงกว่า long-end (vega beta < 1)
        bucket = "vega_short_term" if L.T < cfg["vega_bucket_cutoff"] else "vega_long_term"
        g[bucket] += L.qty * leg_g["vega"]
        g["vega_total"] += L.qty * leg_g["vega"]            # raw sum (มักทำให้ดู flat)
    # vega_term_risk = ความเสี่ยงที่ term structure เคลื่อน "ไม่ขนาน" ซึ่ง
    # vega_total = 0 จับไม่ได้ — นี่คือ root cause ของ vega bleed ใน calendar
    g["vega_term_risk"] = g["vega_long_term"] - cfg["vega_beta"] * g["vega_short_term"]
    return g
```

### 3 จุดที่ calendar spread P&L attribution พลาดบ่อย

| จุดพลาด | อาการ | แก้ด้วย |
| --- | --- | --- |
| **Vega = 0 แต่ขาดทุนจาก vol** | net vega "ดู flat" แต่ short-end IV ตกแรงกว่า long-end → ขาดทุนจริง | แยก `vega_short_term` / `vega_long_term` + `vega_beta` calibrate รายเดือน (เก็บใน cfg) |
| **Theta ไม่เท่ากันสองขา** | short-leg θ ใกล้หมดอายุระเบิดเร็วกว่า; ถ้าใช้ avg theta จะ underestimate decay เร็วในช่วง 7–14 DTE | คำนวณ θ ของแต่ละ leg แยก ไม่ใช้ average; flag เมื่อ short-leg DTE < 14 |
| **DTE convention ผสม** | ขาหนึ่งใช้ calendar day อีกขาใช้ trading day → T ของสองขาคนละหน่วย → Greeks เพี้ยน | ใช้ `core/dte.py` เป็น single source of truth — adapter ห้ามคำนวณ DTE เอง |

### เมื่อใช้ provided IV/Greeks — ยังต้อง validate

data ที่ exchange ให้ `OPTION_VOLATILITY`/`DELTA_FACTOR` มาแล้ว **ก็ยังต้องตรวจ** เพราะ exchange ใช้ rate/dividend assumption ของตัวเอง (อาจไม่ตรงกับ rate ที่เราใช้ใน solver):

**core/greeks.py** — *provided vs solved cross-check*

```
def validate_provided_iv(df, cfg) -> pd.DataFrame:
    """solve IV ของเราเอง แล้วเทียบกับ iv_provided — ถ้าต่างเกิน threshold = log + flag"""
    df["iv_solved"] = df.apply(lambda r: solve_iv(
        cfg["pricing_model"], r.price, r.F, r.strike, r.T, r.r, r.right
    ), axis=1)
    df["iv_diff"] = (df["iv_solved"] - df["iv_provided"]).abs()
    # threshold 0.5 vol-point เป็นจุดเริ่มต้น — ถ้าเกินบ่อย ตรวจ rate convention
    df["iv_flag"] = df["iv_diff"] > cfg["iv_validate_threshold"]
    return df
```

*09 / IMPLEMENTATION*

## Performance diagnosis expanded

หัวใจของ stage 4 ที่ blueprint เดิมพูดหลวมไป — ห้ามดูแค่ค่าเฉลี่ย ต้องเห็นการกระจายราย fold และราย regime เพราะ "สถิติเหมือนกัน แต่การใช้งานจริงต่างกัน": ค่าเฉลี่ย +15%/ปี อาจซ่อน fold ที่ขาดทุนหนักไว้

**ตัวอย่าง: กลยุทธ์เดียวกัน — มุมมองค่าเฉลี่ย vs มุมมองราย fold**

| Fold / regime | Return |
| --- | --- |
| 2017 · low-vol | +18% |
| 2018 · trade-war | +40% |
| 2019 · recovery | +22% |
| 2020 · COVID | −25% |
| 2021 · tech-boom | +30% |

> **ถ้าดูแต่ค่าเฉลี่ย:** เห็น **+15%/ปี** ดูดีมาก ตัดสินใจ deploy ทันที — แต่ไม่รู้เลยว่ามี fold ที่พังหนัก

> **ถ้าดูราย fold:** เห็นว่า 2020 ขาดทุน **−25%** ในวิกฤต = กลยุทธ์เปราะใน regime วิกฤต ต้องมี circuit breaker หรือ regime filter ก่อน deploy จริง

ดังนั้น `per_fold_breakdown()` และ `per_regime_breakdown()` ไม่ใช่ optional — มันคือ output หลักของ stage 4 ที่ต้องแสดงทุก fold พร้อม regime ที่ครอบงำ ช่วงวันที่ และ worst-case ไม่ใช่แค่ค่าเฉลี่ยรวม

**core/metrics.py** — *per-fold / per-regime*

```
def per_fold_breakdown(fold_returns: dict, regime_labels: pd.Series) -> pd.DataFrame:
    """หนึ่งแถวต่อ fold — เห็นการกระจายจริง ไม่ใช่ค่าเฉลี่ยรวม"""
    rows = []
    for fid, r in fold_returns.items():
        rows.append({
            "fold": fid,
            "date_range":   (r.index[0], r.index[-1]),
            "dominant_regime": regime_labels.loc[r.index].mode()[0],
            "total_return": (1+r).prod() - 1,
            "sharpe":       _sharpe(r),
            "sortino":      _sortino(r),
            "max_dd":       _max_drawdown(r),
            "cvar_95":      _cvar(r, 0.95),
            "hit_rate":     (r > 0).mean(),
            "worst_day":    r.min(),
        })
    return pd.DataFrame(rows)

def stability_score(per_fold: pd.DataFrame) -> dict:
    """วัดความสม่ำเสมอข้าม fold — ค่าเฉลี่ยสูงแต่ผันผวนมาก = เปราะ"""
    s = per_fold["sharpe"]
    return {"sharpe_mean": s.mean(), "sharpe_std": s.std(),
            "sharpe_min": s.min(), "pct_profitable_folds": (per_fold["total_return"]>0).mean(),
            "worst_fold_return": per_fold["total_return"].min()}
```

### Full metric set — มาตรฐานที่ต้องวัด

blueprint เดิมมีแค่ Sharpe/CVaR ซึ่งไม่พอ ของจริงต้องครอบคลุม 6 หมวด ทุกตัววัดทั้งระดับรวม, ราย fold และราย regime

| หมวด | เมตริก | จับอะไรที่ค่าเฉลี่ยไม่เห็น |
| --- | --- | --- |
| **Return** | Total, CAGR, ann. return | ฐานของทุกอย่าง |
| **Risk-adjusted** | Sharpe, Sortino, Calmar, Information Ratio | Sortino แยก downside; Calmar เทียบกับ max DD |
| **Drawdown** | Max DD, avg DD, DD duration, recovery time, Ulcer index | ความเจ็บที่แท้จริง — Sharpe ไม่บอกว่าจมนานแค่ไหน |
| **Distribution** | min, max, mean, median, std, **skew, kurtosis** | fat tail / asymmetry ที่ Sharpe สมมติว่าไม่มี |
| **Tail** | VaR, CVaR, worst day, worst week | ขนาดความเสียหายในหางซ้าย |
| **Hit / consistency** | win rate, profit factor, payoff ratio, longest losing streak | กำไรมาจากไม้ใหญ่ไม่กี่ไม้หรือสม่ำเสมอ |

> **overfitting gate:** หลังวัดครบแล้ว ส่งผ่าน `deflated_sharpe_ratio()` เพื่อปรับ Sharpe ตามจำนวนกลยุทธ์ที่ทดสอบ และ `prob_backtest_overfitting()` — Sharpe สูงจากการลองเยอะๆ ไม่ใช่ edge จริง

*10 / IMPLEMENTATION*

## Reuse matrix — equity options vs futures options

บอกชัดว่า function ใด shared/partial/separate ใช้เป็นแผนที่ตัดสินใจว่าวางโค้ดที่ไหน อย่า duplicate สิ่งที่ shared และอย่ายัด separate ลง base

- ใช้ร่วม 100% — core หรือ OptionsBase
- ใช้ร่วมบางส่วน — base + cfg override
- แยกขาด — subclass เท่านั้น

| Function / concern | Stage | Status | หมายเหตุ |
| --- | --- | --- | --- |
| logical\_bounds\_check | 1 | shared | bid>0, IV>0, PCP ± tol — เหมือนกันทุก asset |
| build\_iv\_surface | 1 | shared | arbitrage-free interpolation เป็น math เดียว |
| missing\_completeness | 1 | partial | logic เดียว ต่างแค่ threshold ใน cfg (OI floor) |
| time alignment | 1 | separate | NYSE close vs CME 23hr + scheduled events |
| corp action / roll | 1 | separate | strike-adjust (equity) vs futures roll |
| adf/kpss/arch/jarque-bera | 2 | shared | test ทางสถิติ ไม่ขึ้นกับ asset |
| distribution\_shift / IC / VIF | 2 | shared | function เดียวทุก asset |
| compute\_vrp\_sign / skew\_25d | 2 | shared | formula เดียว (futures แค่ allow call-skew) |
| term structure feature | 2 | separate | VIX term vs OVX + futures basis |
| walk\_forward / diversity\_gate | 3 | shared | cfg['regime\_axes'] กำหนดแกน |
| purge\_embargo | 3 | partial | purge=max\_dte ทั้งคู่ futures เพิ่ม event gap ผ่าน cfg |
| all metrics + overfitting | 4 | shared | Sharpe/Sortino/DD/DSR/PBO เหมือนกันทุก asset |
| OOS P&L net cost | 4 | partial | net vega bleed ทั้งคู่ futures เพิ่ม roll cost |
| stress event set | 4 | separate | vix/earnings vs scheduled-event/roll-squeeze |

*11 / IMPLEMENTATION*

## Config schema

ความต่างระหว่าง instrument อยู่ที่นี่ ไม่ใช่ในโค้ด ชื่อจริง (B, North Sea, EIA) ปรากฏเฉพาะใน config — ตัวอย่างเป็น Brent ตาม data จริง

**configs/instruments/bz.yaml** — *Brent · instrument spec*

```
family: futures_options          # เลือก adapter จากตรงนี้
provider: settlement             # pipe-delimited EOD loader
symbol:                          # resolve จาก field จริงในไฟล์
  product_id: 254                 # stable key — join ใช้ตัวนี้ ไม่ใช่ชื่อ
  contract_root: B
  hub: North Sea

columns:
  vol_col: brent_iv_index         # ⚠ OVX = WTI เท่านั้น! Brent ใช้ index อื่น
                                  #   หรือ proxy = rolling RV ถ้าไม่มี index
  price_col: front_month_cont

price_field: settlement          # ไม่ใช่ last-trade
iv_source: provided              # ใช้ OPTION_VOLATILITY จาก exchange (validate ก่อน)
date_grain: eod                  # date-only; ไม่มี intraday timestamp

pricing:                        # NEW v1.3: model + IV solver settings
  model: black76                  # futures options → Black-76 (ห้ามใช้ bs)
  iv_validate_threshold: 0.005    # 0.5 vol-point — เกินนี้ flag
  iv_solver_bounds: [0.0001, 5.0] # σ search range (annualized)
  vega_bucket_cutoff: 60          # DTE < 60 = short-term bucket (สำหรับ net vega ของ calendar)
  vega_beta: 0.7                  # short-end vol ขยับแรงกว่า long-end (calibrate รายเดือน)

dte:                            # NEW v1.3: calendar convention (single source of truth)
  basis: calendar                 # calendar | trading — adapter ต้องใช้อันเดียวกัน
  day_count: act_365              # act_365 | act_360 | bus_252
  expiry_cutoff: settlement       # settlement (EOD) | open_next_day
  exclude_expiry_date: true       # T-day = expiry นับเป็น DTE 0 หรือไม่

event_calendars: [events/eia.csv, events/opec.csv]  # ไม่มีคำว่า EIA ในโค้ด
event_regimes: [eia_week]

validation: { min_oi: 100, iv_cap: 5.0, futures_oi_floor: 1000, roll_days: 5 }
stability: { psi_threshold: 0.25, use_iv_change: true }
cv:
  n_folds: 8
  purge_bars: max_dte
  event_embargo_bars: 2
  regime_axes: [vol_regime, term_structure, vrp_sign, skew_direction, eia_week]
  max_concentration: 0.80
  kl_threshold: 0.5
  js_threshold: 0.3              # symmetric bounded companion
performance:
  rf_rate_source: sofr
  net_greek_pnl: true
  include_roll_cost: true
  n_trials: 40                   # สำหรับ deflated sharpe
  stress_events: [eia_miss, opec_shock, roll_squeeze, vix_spike]
audit:                          # NEW v1.3: lightweight snapshot per stage
  enabled: true
  snapshot_cols: [as_of_date, product_id, strike, expiry, price, iv_provided]
  hash_algo: xxh64                # เร็ว, deterministic
  retain_days: 30
```

> **Brent ≠ WTI:** data จริงเป็น Brent ไม่ใช่ WTI — ยืนยันว่าการตั้งชื่อ generic ถูกต้อง `futures_options_adapter` ใช้ได้ทันทีโดยไม่แก้โค้ด แต่ระวัง 2 จุด: **(1)** `OVX` เป็น vol index ของ WTI เท่านั้น Brent ต้องชี้ `vol_col` ไป index อื่นหรือใช้ realized vol เป็น proxy **(2)** EIA inventory เป็น US data — กระทบ WTI โดยตรงกว่า Brent ดังนั้น event weight ของ Brent อาจต่างจาก WTI (พิจารณาเพิ่ม API/EIA global หรือ OPEC เป็นหลัก)

*12 / ADDITIONS v1.4*

## Data versioning + available\_at new v1.4

สองปัญหาต่างกันแต่แก้พร้อมกันได้ — **versioning** ป้องกัน invisible data change (provider แก้ย้อนหลังโดยไม่แจ้ง) และ **available\_at** ป้องกัน look-ahead bias จากการ join ด้วยวันที่แทนที่จะเป็นเวลาที่ข้อมูลพร้อมจริงๆ ทั้งสองเป็น silent bug — ไม่มี error แต่ backtest result ปลอม

### ทำไม overwrite ถึงอันตราย

| เวลา | เหตุการณ์ | สถานะ |
| --- | --- | --- |
| 2024-09-25 18:00 | **Provider publish settlement** — price = 63.46, OI = 12,450 เขียนลง `raw/bz/2024-09-25.parquet` | OK |
| 2024-09-26 09:00 | **Provider แก้ OI ย้อนหลัง** — OI จริงคือ 11,820 (รายงานผิด) **ถ้า overwrite**: ไฟล์เดิมหายไป ไม่รู้ว่าตัวเลขเดิมคืออะไร backtest ที่รันเมื่อวานใช้ตัวเลขอื่นกับวันนี้ | DANGER |
| 2024-09-26 09:00 | **ถ้าใช้ versioning** — เขียน `raw/bz/ingested_at=2024-09-26/2024-09-25.parquet` แยก pipeline เลือก partition ผ่าน `cfg["data_version"]` ของเดิมยังอยู่ครบ | SAFE |
| backtest time | **reproducibility check** — re-run ด้วย `data_version: "2024-09-25"` ได้ผลเดิมเสมอ เปลี่ยนเป็น `"2024-09-26"` เห็นว่า OI revision กระทบ VRP signal ไหม | INSIGHT |

### โครงสร้าง immutable partitioned storage

**raw/ partition layout** — *hive-style · immutable*

```
# ── partition by instrument + ingestion date ──
raw/
├── bz/                              # Brent futures+options
│   ├── ingested_at=2024-09-25/
│   │   └── settlement.parquet       # as_of_date ≤ 2024-09-25
│   ├── ingested_at=2024-09-26/      # revision — partition ใหม่ ไม่แก้เดิม
│   │   └── settlement.parquet
│   └── ingested_at=2024-09-27/
│       └── settlement.parquet
└── eq_a/                            # equity provider A
    ├── ingested_at=2024-09-25/
    │   └── prices.parquet

# ── version manifest ──
raw/_versions.jsonl                  # log ทุก ingestion: {ingested_at, rows, schema_hash, run_id}
```

**ingestion/versioned\_cache.py** — *read/write contract*

```
def write(symbol, df, ingested_at=None):
    """ห้าม overwrite — เสมอเขียน partition ใหม่"""
    ts = ingested_at or datetime.utcnow().date().isoformat()
    path = f"raw/{symbol}/ingested_at={ts}/data.parquet"
    if Path(path).exists():
        raise FileExistsError(f"partition {ts} exists — versioning violated")
    df.to_parquet(path, index=False)
    _append_manifest(symbol, ts, df)   # log rows + schema_hash

def read(symbol, cfg) -> pd.DataFrame:
    """data_version: 'latest' | 'YYYY-MM-DD' | 'as_of_backtest_start'"""
    version = cfg.get("data_version", "latest")
    if version == "latest":
        partition = _latest_partition(symbol)
    elif version == "as_of_backtest_start":
        partition = _partition_at(symbol, cfg["backtest_start"])
    else:
        partition = version                          # explicit YYYY-MM-DD
    return pd.read_parquet(f"raw/{symbol}/ingested_at={partition}/data.parquet")
```

### available\_at — ทำไมถึงต่างจาก as\_of\_date

ปัญหาคือข้อมูลวันที่ T มักไม่พร้อมในทันที มี release lag ตามประเภทข้อมูล ถ้า join ด้วย `as_of_date` อย่างเดียว pipeline จะ "รู้" ข้อมูลก่อนที่ตลาดจริงจะรู้

| Data type | as\_of\_date | available\_at จริง | Lag | Impact ถ้าใช้ as\_of\_date |
| --- | --- | --- | --- | --- |
| **EOD settlement** | trade date | trade date + ~2–3 ชม. (18:00 UTC) | ชั่วโมง | เล็กน้อย — ถ้า strategy เทรด open วันถัดไป |
| **EIA inventory** | week ending | วันพุธถัดไป 14:30 ET | ~5 วัน | **รุนแรง** — เหมือนรู้ผล report ก่อนออก |
| **CFTC COT report** | วันอังคาร | วันศุกร์ 15:30 ET | ~3.5 วัน | รุนแรง |
| **Earnings (equity)** | quarter end | วันที่ announce (ไม่ตายตัว) | 2–8 สัปดาห์ | **รุนแรงมาก** |
| **Realized vol** | period end | คำนวณได้ทันที (EOD) | ชั่วโมง | เล็กน้อย |

**RAW\_SCHEMA — v1.4 update** — *เพิ่ม available\_at*

```
RAW_SCHEMA = {
    "as_of_date":    "datetime64[ns]",     # วันที่ข้อมูล "อ้างถึง" (period end)
    "available_at":  "datetime64[ns, UTC]", # NEW: วันที่ข้อมูลพร้อมจริง (release time)
    "ingested_at":   "datetime64[ns, UTC]", # NEW: วันที่ pipeline ดึงมา (≥ available_at)
    ...                                        # fields อื่นไม่เปลี่ยน
}

# ── PIT-correct join ──
def pit_join(signals, events, decision_time):
    """join เฉพาะ event ที่ available_at <= decision_time (ไม่ใช่ as_of_date)"""
    eligible = events[events["available_at"] <= decision_time]
    return pd.merge_asof(signals, eligible,
                         left_on="decision_time", right_on="available_at",
                         direction="backward")   # ห้าม forward!

# ── fallback เมื่อ provider ไม่ให้ available_at ──
def infer_available_at(as_of_date, data_type, cfg):
    lag_map = cfg["available_at_lag"]  # อยู่ใน yaml ไม่ hardcode
    return as_of_date + pd.Timedelta(lag_map[data_type])
```

**configs/instruments/bz.yaml — available\_at block** — *conservative lag defaults*

```
available_at_lag:                  # fallback เมื่อ provider ไม่ให้ timestamp
  settlement: "3h"                 # EOD + 3 ชั่วโมง (conservative)
  eia_inventory: "P5D"             # week-ending → วันพุธถัดไป + 14:30 ET
  opec_report: "P2D"
  realized_vol: "2h"               # คำนวณ EOD เองได้เร็ว
```

> **join rule เด็ดขาด:** ทุก join ระหว่าง signal กับ external data ต้องใช้ `available_at ≤ decision_time` เสมอ ห้ามใช้ `as_of_date` เป็น join key โดยตรง เพราะ `as_of_date` คือ "ข้อมูลนี้พูดถึงวันไหน" ไม่ใช่ "วันไหนที่เรารู้" — สองอย่างนี้ต่างกันได้มากถึง 5 วัน สำหรับ EIA/COT

*13 / ADDITIONS v1.4*

## Transaction cost / liquidity model new v1.4

สำคัญเป็นพิเศษสำหรับ options spread — calendar spread มีต้นทุนสองขาซึ่งบวกกัน และ bid-ask ของ options ที่ DTE ต่างกันกว้างไม่เท่ากัน ออกแบบเป็น 3 ระดับ ทำตามลำดับ อย่าข้ามไประดับ 3 ก่อนที่ระดับ 1 จะ validate

#### Level 1 · ทำก่อน — Fixed cost per leg

*จับ ~80% ของ cost จริง*

**Commission + half-spread คงที่** per contract ระบุใน cfg  
  
เหมาะกับ: validate ว่า strategy มี edge หลัง cost หรือไม่ ก่อนลงทุนเวลา model ที่ซับซ้อนกว่า  
  
ข้อจำกัด: bid-ask จริงกว้างกว่าใน near-expiry และ far OTM — cost ที่ประมาณจะต่ำกว่าจริง

#### Level 2 · ทำเมื่อ L1 validate — Bid-ask scaling

*DTE-aware + moneyness-aware*

**spread\_cost = f(DTE, moneyness, VIX\_regime)**  
  
ATM near-expiry กว้างกว่า ATM far-expiry มาก เพราะ liquidity ลด calibrate จาก historical bid-ask data  
  
ข้อจำกัด: ยังไม่จับ market impact — ถ้า size ใหญ่ fill จะได้ราคาแย่กว่า mid

#### Level 3 · ทำเมื่อ size ใหญ่ — Market impact

*เมื่อ order กระทบ price จริง*

**Almgren-Chriss / sqrt(participation rate)**  
  
สำคัญเมื่อ order size เริ่มเป็น % ที่มีนัยของ daily volume เช่น >1% ADV  
  
ต้องการ: volume data + spread data ต่อ strike/DTE ซึ่งต้องการ tick data หรือ L2 order book

### โครงสร้าง core/txcost.py

**core/txcost.py** — *3-level unified API*

```
def cost_per_trade(legs: list[Leg], mkt_data, cfg) -> CostBreakdown:
    """entry point — เรียก level ที่ระบุใน cfg['txcost_level']
       คืน CostBreakdown เสมอ (structure เดียว ต่างแค่ความละเอียด)"""
    level = cfg.get("txcost_level", 1)
    total_cost = 0.0
    breakdown = []
    for L in legs:
        if level == 1:
            c = _fixed_cost(L, cfg)          # commission + half_spread_fixed
        elif level == 2:
            c = _scaled_cost(L, mkt_data, cfg)  # f(DTE, moneyness, regime)
        else:
            c = _impact_cost(L, mkt_data, cfg)   # Almgren-Chriss
        breakdown.append({"leg": L, "cost": c, "level": level})
        total_cost += abs(L.qty) * c
    return CostBreakdown(total=total_cost, legs=breakdown, level=level)

def _scaled_cost(leg, mkt, cfg) -> float:
    """Level 2: bid-ask ขึ้นกับ DTE + moneyness + vol regime"""
    base = cfg["commission_per_contract"]
    dte_factor = _dte_spread_factor(leg.T, cfg["dte_spread_curve"])
    # DTE กว้างขึ้น: near-expiry (T<14d) factor ~2-3x ATM ATM far
    mono_factor = _moneyness_factor(leg.strike / leg.F, cfg)
    # OTM 20%+ factor ~1.5-4x เพราะ liquidity บาง
    regime_adj = cfg["spread_regime_mult"].get(mkt.vol_regime, 1.0)
    # high-vol regime: spread กว้างขึ้นอีก 1.5-2x
    return base + (mkt.bid_ask_mid * dte_factor * mono_factor * regime_adj / 2)
```

### Calendar spread — ทำไม cost สำคัญกว่า strategy อื่น

| รายการ | Single leg | Calendar spread (2 legs) | หมายเหตุ |
| --- | --- | --- | --- |
| **Commission** | 1× rate | 2× rate | ทุกครั้งที่ roll ก็เสียอีก 2× |
| **Bid-ask (enter)** | ½ spread × 1 | ½ spread × 2 (แต่ต่างกัน) | short-leg near-expiry กว้างกว่า long-leg |
| **Bid-ask (exit/roll)** | ½ spread × 1 | ½ spread × 2 อีกรอบ | total round-trip = 4× half-spread |
| **Theta capture** | ขึ้นกับ DTE | net theta = short − long | ถ้า cost > expected theta ไม่ควรเทรด |

> **cost check:** ก่อน deploy ให้ตรวจ: `expected_net_theta_per_day × holding_days > total_round_trip_cost` ถ้าไม่ผ่าน theta ที่ capture ได้ไม่คุ้มต้นทุน แม้ Sharpe gross จะดูดีก็ตาม

### Config สำหรับ txcost

**configs/instruments/bz.yaml — txcost block** — *ตัวอย่าง*

```
txcost:
  level: 2                             # เริ่มที่ 1 แล้ว upgrade เมื่อ validate
  commission_per_contract: 2.50        # USD — ใส่ค่าจริงของ broker
  half_spread_fixed: 0.05              # USD/contract (level 1 fallback)
  dte_spread_curve:                    # level 2: multiplier ตาม DTE bucket
    ">60":  1.0
    "30-60": 1.4
    "14-30": 1.8
    "<14":   2.8                        # near-expiry กว้างมาก
  moneyness_otm_threshold: 0.15        # >15% OTM เริ่ม factor เพิ่ม
  spread_regime_mult:
    low_vol:  0.85
    mid_vol:  1.00
    high_vol: 1.60                     # spread กว้างขึ้นตาม vol
  financing_rate_col: sofr             # overnight margin cost
  margin_requirement: 0.10             # 10% ของ notional (ตรวจกับ broker จริง)
```

*14 / ADDITIONS v1.4*

## Performance attribution waterfall new v1.4

ตอบคำถามว่า "กำไรมาจากอะไรจริงๆ" — Gross P&L เป็นแค่ผลลัพธ์ waterfall แยกออกว่าแต่ละ control ขั้นกินหรือให้ผลเท่าไร โครงสร้างต่างกันระหว่าง **options** (Greek decomposition) และ **equity** (factor decomposition) แต่ชั้นล่าง (cost + financing) ใช้ร่วมกัน

### Options waterfall — Calendar spread

ใช้ข้อมูลจาก `core/greeks.py` และ `core/txcost.py` ที่มีอยู่แล้ว ไม่ต้องสร้าง model ใหม่

| Layer | คืออะไร / จับอะไร | ตัวอย่าง | Asset |
| --- | --- | --- | --- |
| Gross P&L |  | +$1,240 | ALL |
| − Delta P&L | underlying เคลื่อน × net delta | −$347 | OPTIONS |
| − Gamma P&L | ½ × gamma × (ΔS)² | +$155 | OPTIONS |
| − Theta P&L | net theta × days held (แยกแต่ละขา) | +$680 | OPTIONS |
| − Vega (parallel) P&L | vega\_total × ΔIV (parallel shift) | +$95 | OPTIONS |
| − Vega term risk P&L | vega\_term\_risk × (ΔIV\_short − β·ΔIV\_long) | −$470 | OPTIONS |
| = Unexplained residual | jump risk, model error, rounding | +$127 | ALL |
|  |  |  |  |
| − Commission + bid-ask | txcost.cost\_per\_trade() ทุก leg | −$195 | ALL |
| − Financing cost | margin × SOFR × days / 365 | −$85 | ALL |
| = Net P&L (OOS จริง) |  | +$1,000 | ALL |

> **อ่านตัวอย่าง:** จากตัวเลขข้างบน: Theta ให้ +$680 แต่ **Vega term risk กิน −$470** นี่คือ root cause ของ calendar spread ขาดทุนที่เคยพบ ถ้าดูแค่ gross P&L จะไม่รู้เลยว่าปัญหาอยู่ที่ term structure shift ไม่ขนาน

### Equity waterfall — Factor decomposition

แทน Greek ด้วย factor exposure ชั้นกลางเปลี่ยน แต่ชั้นบน (Gross) และชั้นล่าง (cost) เหมือนกันทุกอย่าง

| Layer | คืออะไร / จับอะไร | ตัวอย่าง | Asset |
| --- | --- | --- | --- |
| Gross P&L |  | +$2,100 | EQUITY |
| − Market beta P&L | β × market\_return × portfolio\_value | +$1,260 | EQUITY |
| − Sector / style factor P&L | momentum, size, quality, value exposure | +$415 | EQUITY |
| = Alpha (unexplained) | signal จริงที่ไม่ใช่ factor — ถ้าเล็กมากแสดงว่า strategy = ETF | +$425 | EQUITY |
|  |  |  |  |
| − Commission + slippage | txcost.cost\_per\_trade() | −$210 | ALL |
| − Financing cost | short rebate / margin / borrow cost | −$110 | ALL |
| = Net P&L (OOS จริง) |  | +$1,780 | EQUITY |

> **อ่านตัวอย่าง:** Alpha หลัง factor attribution = +$425 จาก Gross $2,100 (~20%) แปลว่า 80% ของกำไรมาจาก market beta และ factor — ซื้อ ETF ถูกกว่า ถ้า Alpha เป็นลบหลัง factor strip แสดงว่า strategy แพ้ passive อย่างชัดเจน

### การ implement — ขึ้นอยู่กับอะไรต้องมีก่อน

| Dependency | ต้องพร้อมก่อน | ถ้าไม่มี waterfall จะผิดอย่างไร |
| --- | --- | --- |
| `available_at` (section 12) | ✓ ต้องมีก่อน | factor/Greek คำนวณจากข้อมูลที่รู้ "ก่อนเวลา" → attribution ปลอม |
| `core/greeks.py` (section 08) | ✓ ต้องมีก่อน (options) | ไม่มี Greek decomposition → options waterfall ทำไม่ได้ |
| `core/txcost.py` (section 13) | ✓ ต้องมีก่อน | Net P&L ยังเป็น Gross — ตัวเลขเกินจริง |
| Factor returns (equity) | ต้องการ factor data (Fama-French / Barra) | alpha ที่คำนวณได้จะ overstate ถ้า factor ไม่ครบ |

**core/attribution.py** — *waterfall unified API*

```
def waterfall(trades_df, cfg) -> WaterfallResult:
    """entry point — detect asset_type จาก cfg แล้ว route ถูก decomposer"""
    gross = trades_df["pnl_gross"].sum()
    asset = cfg["family"]                      # options | equity | futures

    if asset in ("equity_options", "futures_options"):
        layers = _greek_decompose(trades_df, cfg)  # delta, gamma, theta, vega, vega_term
    elif asset == "equity":
        layers = _factor_decompose(trades_df, cfg)  # market beta, sector/style, alpha
    else:                                          # futures (no options)
        layers = _basis_decompose(trades_df, cfg)   # roll carry, spot change, basis

    residual  = gross - sum(l.pnl for l in layers)
    cost      = txcost.total(trades_df, cfg)
    financing = _financing_cost(trades_df, cfg)
    net       = gross - cost - financing
    return WaterfallResult(gross=gross, layers=layers, residual=residual,
                           cost=cost, financing=financing, net=net)

def _greek_decompose(df, cfg) -> list[Layer]:
    """แต่ละ layer = greek × realized_move; ใช้ greeks ณ entry time (PIT)"""
    return [
        Layer("delta",      (df.delta_entry * df.d_underlying).sum()),
        Layer("gamma",      (0.5 * df.gamma_entry * df.d_underlying**2).sum()),
        Layer("theta",      (df.theta_entry * df.days_held).sum()),
        Layer("vega_par",   (df.vega_total_entry * df.d_iv_parallel).sum()),
        Layer("vega_term",  (df.vega_term_risk * df.d_iv_term_slope).sum()),
    ]
```

> **PIT Greeks:** Greek ที่ใช้ใน `_greek_decompose` ต้องเป็น Greek ณ **เวลา entry** ไม่ใช่ Greek เฉลี่ยตลอด period และไม่ใช่ Greek ณ exit — เพราะ waterfall วัดว่า "ถ้ารู้ Greek ณ ตอนเข้า ควรจะได้ P&L เท่าไร" ถ้าใช้ Greek ณ exit จะเป็น look-back attribution ซึ่งเป็น circular reasoning

*15 / EXECUTION*

## Tests & data audit v1.3

v1.2 มี gate criteria แต่ไม่มี *test ระบุชัดเป็นข้อๆ* — ปัญหา DTE และ symbology เป็น **silent killer** (ผิดแล้วไม่ throw error แต่ผลลัพธ์ปลอม) จึงต้องมี test เป็น hard requirement ไม่ใช่ optional ส่วน data audit เป็น lightweight (snapshot ไม่ใช่ observability stack) — ใช้เพื่อ debug ว่าค่าเพี้ยนเกิด stage ไหน

### ทำไม Symbology + DTE ต้องมี test เป็น hard requirement

| ปัญหา | อาการ (ทำไมเป็น silent killer) | ผลกระทบ |
| --- | --- | --- |
| **Symbology map ผิด** PRODUCT\_ID/HUB/CONTRACT | core ได้ row ที่ join ผิด contract มา — ไม่มี error เพราะ schema ถูกต้อง ค่าตัวเลขก็อยู่ในช่วง normal | backtest ผสมราคาของ contract คนละชนิด → P&L ดูดีปลอมๆ; เมื่อ deploy จริงเจอ live data ที่ map ถูก ผลพัง |
| **DTE convention ผิด** calendar vs trading day | T ใน Black-76 เพี้ยน ~1.4x (252 vs 365) → IV solved ผิด ~20%; แต่ output ยังเป็นตัวเลขที่ดูปกติ | theta/vega bleed ของ calendar spread ผิดทิศ; circuit breaker trigger ที่ผิดเวลา |
| **Expiry inclusive/exclusive** | DTE 0 vs DTE 1 ที่ expiry → purge window พลาด 1 วัน | look-ahead bias เล็กๆ ที่สะสมข้าม fold; deflated Sharpe ไม่จับ |
| **Schema drift** column rename, type change | provider เปลี่ยน format → loader อาจ silent-cast (str → float แล้วได้ NaN) | row หายไปจาก fold โดยไม่มีคำเตือน; survivorship bias ใหม่ |

### Test suite — แบ่งตาม layer

| Layer | Test category | ตัวอย่างเคส | Tool |
| --- | --- | --- | --- |
| **ingestion/** | Schema/contract | parse row จริง → match RAW\_SCHEMA ครบ; column missing ต้อง raise ไม่ใช่ silent NaN | pytest + pandera/great\_expectations |
| Symbology join | round-trip: PRODUCT\_ID → resolve → reverse-resolve = ค่าเดิม; uniqueness: ไม่มี PRODUCT\_ID เดียว map ไปสอง contract\_root | pytest property test |
| PIT / survivorship | fetch ช่วงที่มี expired option → ต้องคืน row นั้นกลับมา; cache hit ไม่ทำให้ data หาย | fixture data + pytest |
| **core/dte.py** | Calendar convention | expiry วันศุกร์, T = วันจันทร์ → DTE = ? (depends on basis); expiry วันหยุด exchange | parametrize + golden values |
| Day count consistency | act\_365 vs act\_360 vs bus\_252 — เทียบกับค่าที่คำนวณมือ ≤ 1 minute precision | parametrize |
| Edge case | T = 0 (วัน expiry), T < 0 (post-expiry, ต้องเป็น NaN), T ข้ามปี leap year | parametrize |
| **core/pricing.py** | Closed-form vs QuantLib | Black-76 price/Greeks ของ 50 ATM/OTM/ITM cases → diff < 1e-6 vs QuantLib | golden fixture |
| Put-Call Parity | C − P = e^(-rT)(F − K) ± tol ในทุก strike/expiry | property test |
| IV solver round-trip | price(σ=0.3) → solve\_iv → ได้ 0.3 ± 1e-5; deep OTM ต้องคืน NaN ไม่ใช่ค่ามั่ว | parametrize |
| **core/greeks.py** | Bump-vs-analytic | finite-diff (ε=1e-4) ของ price = analytic delta/gamma/vega ± 1e-4 | parametrize |
| Net greeks (spread) | long-call + short-call ที่ K เดียวกัน, T เดียวกัน → net = 0; calendar spread vega\_term\_risk ≠ 0 แม้ vega\_total = 0 | fixture spread |
| **adapters/** | prepare() contract | output คืน `(df, cfg)` ตรง shape; cfg มี key ที่ core ต้องการครบ | schema test |
| Golden snapshot | input fixture เดิม → output ต้องไม่เปลี่ยน (catch unintended logic change) | syrupy/pytest-regressions |
| **core/splitter.py** | No look-ahead | val fold index ทั้งหมด > train fold max + purge\_bars | property test |
| Embargo | event date ใน train ต้องไม่ leak เข้า val (gap ≥ embargo) | property test |
| **core/metrics.py** | Numeric stability | Sharpe ของ constant return = NaN ไม่ใช่ inf; max DD ของ monotonic up = 0 | parametrize |

### Symbology test — เคสที่ต้อง cover

**tests/test\_ingestion/test\_symbology.py** — *silent-killer cases*

```
def test_product_id_unique_per_contract(product_map):
    """PRODUCT_ID 254 ต้องไม่ map ไปทั้ง B (Brent) และ CL (WTI) — ตรวจ uniqueness"""
    for pid, rows in product_map.groupby("product_id"):
        assert rows["contract_root"].nunique() == 1, f"{pid} ambiguous"

def test_round_trip(sample_rows, symbology):
    """resolve(row) → internal_symbol; reverse(internal_symbol) → ต้องได้ key เดิม"""
    for r in sample_rows:
        sym = symbology.resolve(r.product_id, r.hub, r.contract)
        back = symbology.reverse(sym)
        assert (back.product_id, back.hub, back.contract) == (r.product_id, r.hub, r.contract)

def test_no_orphan_after_join(raw_df, product_map):
    """ทุก row ใน raw ต้อง join ได้ (ถ้ามี orphan = symbology map ไม่ครบ)"""
    joined = raw_df.merge(product_map, on="product_id", how="left", indicator=True)
    orphan = joined[joined["_merge"] == "left_only"]
    assert orphan.empty, f"{len(orphan)} rows unmapped: {orphan['product_id'].unique()}"

def test_real_row_brent_settlement():
    """row จริง: 9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10|63.46|...
       → instrument_type='option', contract_root='B', strike=10, right='C'"""
    row = parse_pipe_row(REAL_BRENT_ROW)
    assert row.instrument_type == "option"
    assert row.contract_root == "B"
    assert row.strike == 10.0 and row.right == "C"
```

### DTE test — calendar convention

**tests/test\_dte/test\_calendar.py** — *single source of truth*

```
@pytest.mark.parametrize("asof,expiry,basis,expected", [
    # calendar day (act_365) — ตรงไปตรงมา
    ("2024-09-25", "2024-11-01", "calendar", 37),
    # trading day (bus_252) — ข้ามเสาร์-อาทิตย์ + holiday
    ("2024-09-25", "2024-11-01", "trading", 26),  # สมมติ 0 holiday ในช่วงนั้น
    # expiry-day edge case (T = 0 ต้องเป็น 0 ไม่ใช่ NaN; T < 0 = NaN)
    ("2024-11-01", "2024-11-01", "calendar", 0),
    ("2024-11-02", "2024-11-01", "calendar", np.nan),
])
def test_dte_convention(asof, expiry, basis, expected):
    cfg = {"basis": basis, "day_count": "act_365", "exclude_expiry_date": False}
    if np.isnan(expected):
        assert np.isnan(compute_dte(asof, expiry, cfg))
    else:
        assert compute_dte(asof, expiry, cfg) == expected

def test_consistency_across_adapter(equity_options_adapter, futures_options_adapter, raw_df):
    """ทั้ง 2 adapter ต้องเรียก core/dte.py เท่านั้น — ถ้าใครคำนวณเอง จะ fail"""
    df_eq, _ = equity_options_adapter.prepare(raw_df)
    df_fut, _ = futures_options_adapter.prepare(raw_df)
    # row เดียวกัน DTE ต้องเท่ากัน (ถ้า adapter หนึ่งใช้ trading day อีกตัวใช้ calendar = bug)
    pd.testing.assert_series_equal(df_eq["dte"], df_fut["dte"])
```

### Data audit — lightweight (ไม่ใช่ full observability)

เป้าหมาย: เก็บ snapshot สั้นๆ ต่อ stage ใส่ `outputs/audit/` เพื่อ debug ว่าค่าเพี้ยนเกิดที่ไหน — **ไม่ใช่**การทำ MLOps monitoring stack ขนาดใหญ่ (ไม่จำเป็นสำหรับ pipeline ขนาดนี้) เก็บแค่:

| Field | เก็บอะไร | ใช้ทำอะไร |
| --- | --- | --- |
| **stage** | ingestion / adapter / validators / splitter / metrics | ระบุจุด |
| **row\_count** | จำนวน row ก่อน/หลัง stage | จับ row หายระหว่างทาง (filter ผิด, join ผิด) |
| **schema\_hash** | hash ของ list (col\_name, dtype) | จับ schema drift ทันที |
| **data\_hash** | xxh64 ของ snapshot\_cols (ไม่ใช่ทุก col) | re-run แล้วได้ค่าเดิมไหม (determinism check) |
| **key\_stats** | min/max/mean/null\_count ของ key column ที่ระบุใน cfg | เห็นความเพี้ยนเชิงสถิติ |
| **na\_pattern** | per-column null count | NaN ที่งอกใหม่ใน stage นี้ = ตัวบอกตำแหน่ง bug |

**core/audit.py** — *snapshot + diff*

```
def snapshot(df, stage: str, cfg) -> dict:
    """เรียกที่ input และ output ของแต่ละ stage — เขียนเป็น JSON line"""
    cols = cfg["audit"]["snapshot_cols"]
    snap = {
        "stage": stage,
        "timestamp": datetime.utcnow().isoformat(),
        "row_count": len(df),
        "schema_hash": hash_schema(df),
        "data_hash": hash_subset(df[cols]),
        "key_stats": {c: _stats(df[c]) for c in cols if c in df},
        "na_pattern": df.isna().sum().to_dict(),
    }
    _append_jsonl(f"outputs/audit/{cfg['run_id']}.jsonl", snap)
    return snap

def diff_stages(before, after) -> dict:
    """ใช้ใน CLI: quant_audit diff --before ingestion --after adapter
       output: row delta, schema diff, NaN ใหม่ที่งอก, key_stats ที่เปลี่ยน"""
    return {
        "row_delta": after["row_count"] - before["row_count"],
        "schema_changed": after["schema_hash"] != before["schema_hash"],
        "new_nans": {c: after["na_pattern"][c] - before["na_pattern"].get(c, 0)
                     for c in after["na_pattern"] if after["na_pattern"][c] > before["na_pattern"].get(c, 0)},
        ...
    }
```

> **scope guard:** audit นี้ **เก็บแค่ที่จำเป็น** — ไม่ใช่ feature store, ไม่ใช่ data lineage tool ถ้าวันหนึ่งต้องการ observability เต็มรูปแบบ (Monte Carlo Data, Soda, ฯลฯ) ค่อย swap ผ่าน `audit.py` interface เดียวกัน หลักการ: cost ของ audit ต้อง < 5% ของ pipeline runtime ถ้าเกินแสดงว่าเก็บมากไป

### CI gate — test ที่ต้อง pass ก่อน merge

| Gate | Hard requirement | Why |
| --- | --- | --- |
| Symbology | uniqueness + round-trip + no-orphan ผ่านทั้งหมด | ผิด = backtest ปลอม |
| DTE | parametrize 12+ เคส (edge: T=0, T<0, leap year, holiday) ผ่านทั้งหมด | ผิด = Greek เพี้ยน |
| Pricing | diff vs QuantLib < 1e-6 ใน golden set; PCP ผ่านทุก row | foundation ของทุกอย่างที่ตามมา |
| Adapter contract | schema + golden snapshot ไม่เปลี่ยน (หรือเปลี่ยนแล้ว update โดยตั้งใจ) | catch unintended regression |
| Audit determinism | re-run pipeline 2 ครั้ง → audit hash ตรงกัน | ถ้าไม่ตรง = มี non-deterministic logic ซ่อนอยู่ |

*16 / EXECUTION*

## Build order & gates

เพิ่ม Phase 1 (ingestion) นำหน้า — ต้องมี data ก่อนถึงจะ validate ได้ gate สีเขียวเป็น hard stop ทุก phase มี test ที่ต้อง pass (อ้างจาก section 12)

#### Phase 0 — Scaffold (~2 ชม.)

- สร้างโครงสร้างโฟลเดอร์ทั้งหมดตาม section 02 (รวม `tests/`, `configs/symbology/`, `outputs/audit/`)
- ตกลง **RAW\_SCHEMA** และ **prepare(df)→(df,cfg)** contract ให้ทั้งทีมเห็นตรงกัน
- เขียน **configs/instruments/spx.yaml** (equity) เป็นตัวตั้งต้น พร้อม `pricing`, `dte`, `audit` block
- ตั้ง CI ให้รัน `pytest tests/` ก่อน merge (เริ่มจาก stub test ที่ pass ได้)

#### Phase 1 — Ingestion layer + symbology test (~1 สัปดาห์)

- **1A ProviderBase + settlement\_loader** — pipe parser, US-date, future/option split, RAW\_SCHEMA validation
- **1B cache.py** — parquet cache + incremental missing-range fetch
- **1C equity\_loader\_a** (provider แรก เช่น Yahoo สำหรับ POC) — เก็บ raw\_close + adj\_factor แยก, delisting list
- **1D equity\_loader\_b** (provider ที่สอง) — cross-check adjustment convention กับ A
- **1E symbology.py + product\_map.yaml** — กฎ mapping อยู่ใน yaml; loader validate ทุกครั้ง
- **1F tests/test\_ingestion/** — symbology (uniqueness + round-trip + no-orphan) + schema + real-row fixture v1.3
- **1G core/audit.py + snapshot** ที่ input/output ของ ingestion v1.3

> **Gate 1:** **parse row จริงได้ตรง RAW\_SCHEMA** (future/option แยกถูก, net\_change ผ่าน validate) + **symbology test ทั้งหมด pass** (uniqueness, round-trip, no-orphan) + **audit snapshot** ของ ingestion เขียนได้ + cache hit ทำงาน

#### Phase 2 — Core + DTE + equity adapter (end-to-end) (~1–2 สัปดาห์)

- **2A core/dte.py** + test\_dte (calendar convention, edge cases) v1.3 — ต้องเสร็จก่อน adapter ใดๆ
- **2B validators + stability** — รวม math ชุดใหม่ (VR, Ljung-Box, JB, Hurst, IC, VIF)
- **2C regime.py** — assign\_regime\_labels แบบ rolling, ยืนยันไม่มี look-ahead
- **2D splitter** — walk\_forward, purge/embargo (purge\_bars อ่านจาก dte cfg), diversity\_gate (KL + JS)
- **2E metrics + overfitting** — full metric set, per-fold/per-regime, DSR, PBO
- **2F equity\_adapter** — เชื่อม ingestion → adapter → core ครบ 4 stage; ทุก stage มี audit snapshot

> **Gate 2:** **pipeline วิ่ง end-to-end บน equity** + DTE test ผ่านทั้งหมด + perf\_report แสดง per-fold breakdown + **audit re-run ได้ deterministic hash**

#### Phase 3 — Futures adapter + Greeks/Pricing (~1–2 สัปดาห์)

- **3A core/pricing.py** — Black-76 + BS-Merton + solve\_iv (Brent root-find) v1.3
- **3B core/greeks.py** — closed-form delta/gamma/theta/vega + net\_greeks สำหรับ spread v1.3
- **3C tests/test\_core/** — pricing vs QuantLib golden + PCP + bump-vs-analytic + net greeks for calendar v1.3
- **3D build\_continuous\_futures** — roll convention, backward-adjust
- **3E flag\_scheduled\_events** — generic event calendar (bz.yaml ชี้ EIA/OPEC)
- **3F compute\_term\_structure** + extend regime axes

> **Gate 3:** **futures fold มีทั้ง contango และ backwardation** + **pricing diff vs QuantLib < 1e-6** ใน golden set + **net greeks ของ calendar spread แสดง vega\_term\_risk แม้ vega\_total = 0**

#### Phase 4 — Options adapter (ซับซ้อนสุด) (~1–2 สัปดาห์)

- **4A options\_base: IV surface** — clean chain, PCP, calendar/butterfly arb (ใช้ pricing.py จาก Phase 3)
- **4B options\_base: validate\_provided\_iv** — เทียบ `iv_provided` กับ `iv_solved` ของเรา flag ถ้าต่าง > threshold v1.3
- **4C DTE-aware purge** (ผ่าน core/dte.py) + timestamp audit (mid px)
- **4D equity\_options (BS-Merton) + futures\_options (Black-76)** — override เฉพาะส่วนต่าง pricing model ระบุใน yaml
- **4E HMM/GMM offline validator** — concordance ≥ 0.7 กับ rule-based label (ไม่ใช่ replace)

> **Gate 4:** **options fold มี VRP+/VRP−/put-heavy/call-heavy ครบ** + DSR เป็นบวกหลังปรับ n\_trials + **iv\_provided vs iv\_solved diff distribution** สมเหตุสมผล (ส่วนใหญ่ < threshold)

*17 / EXECUTION*

## กฎเหล็กสำหรับทีม

ติดไว้ใน PR checklist

#### ทำเสมอ

- core อ่าน column ผ่าน `cfg[...]` เท่านั้น
- ชื่อ instrument จริง (cl, eia, ovx) อยู่ใน `configs/` เท่านั้น
- ทุก threshold อยู่ใน yaml รวม kl/js threshold, n\_trials, vega\_beta
- เก็บ expired series ตั้งแต่ ingestion (survivorship)
- stage 4 แสดง per-fold + per-regime เสมอ ไม่ใช่แค่ค่าเฉลี่ย
- ปรับ Sharpe ด้วย DSR ตาม n\_trials ก่อนสรุปว่ามี edge
- HMM/GMM ใช้หลัง fold สร้างแล้ว เพื่อ validate
- **v1.3** · futures options ใช้ Black-76 เสมอ (ไม่ใช่ BS)
- **v1.3** · ทุก adapter เรียก `core/dte.py` เป็น single source of truth สำหรับ DTE
- **v1.3** · validate `iv_provided` เทียบกับ `iv_solved` ของเราเองก่อนใช้
- **v1.3** · symbology test (uniqueness + round-trip + no-orphan) ต้อง pass ก่อน merge
- **v1.3** · snapshot ก่อน/หลังทุก stage ใส่ `outputs/audit/`

#### ห้ามทำเด็ดขาด

- ชื่อ `wti/eia/ovx` ใน `core/` หรือ `adapters/`
- hardcode magic number ในโค้ด
- สรุปผลจากค่าเฉลี่ยรวมโดยไม่ดู worst fold
- ทิ้ง expired option series ตอนดึงข้อมูล
- ใช้ `.rolling(center=True)` / full-sample fit ใน regime
- cap scheduled-event spike (EIA) — มันคือ real signal
- วัด OOS โดยไม่ net Greek P&L ออกก่อน
- full-sample HMM/GMM → assign label → แบ่ง fold
- **v1.3** · ใช้ Black-Scholes กับ futures options (จะได้ delta ผิด ~e^rT)
- **v1.3** · ให้ adapter คำนวณ DTE เอง — ต้องผ่าน `core/dte.py` เท่านั้น
- **v1.3** · ใช้ numerical bump เป็น primary Greek calculation (closed-form เท่านั้น)
- **v1.3** · สรุปจาก `vega_total` ใน calendar spread — ต้องดู `vega_term_risk`
- **v1.3** · trust `iv_provided` โดยไม่ validate

*18 / REFERENCE*

## Glossary

- **VRP** — Variance Risk Premium = ATM IV − Realized Vol บวก = options แพง
- **OVX** — CBOE Crude Oil Volatility Index — VIX สำหรับ crude options
- **PCP** — Put-Call Parity — bound ที่ใช้ validate ราคา option
- **Contango / Backwardation** — M2>M1 (glut) / M2<M1 (tight) — กระทบ roll carry
- **Survivorship** — เก็บ series ที่หมดอายุ/delisted ไว้ ไม่งั้น backtest มองโลกสวยเกินจริง
- **PIT** — Point-In-Time — ข้อมูล ณ เวลานั้นจริง ไม่ปนข้อมูลที่รู้ทีหลัง
- **Sortino** — เหมือน Sharpe แต่ลงโทษเฉพาะ downside volatility
- **Calmar** — ann. return ÷ max drawdown — return ต่อความเจ็บ
- **Ulcer index** — วัดความลึกและความนานของ drawdown รวมกัน
- **Skew / Kurtosis** — ความเบ้/ความหนาหาง ของ return — Sharpe สมมติว่าเป็น 0/3
- **IC (Information Coefficient)** — correlation ระหว่าง prediction กับ forward return — วัดว่า feature ทำนายได้จริงไหม
- **VIF** — Variance Inflation Factor — วัด multicollinearity ของ feature
- **KL / JS divergence** — วัดความต่างของ distribution; KL asymmetric (จับ shift รุนแรง), JS symmetric bounded (เทียบข้าม fold)
- **Deflated Sharpe (DSR)** — ปรับ Sharpe ตามจำนวนกลยุทธ์ที่ทดสอบ — กัน false discovery จากการลองเยอะ
- **PBO** — Probability of Backtest Overfitting — โอกาสที่ in-sample winner จะแพ้ out-of-sample
- **HMM/GMM (validator)** — ใช้ offline หลัง fold สร้างแล้ว เทียบกับ rule-based label ห้าม label fold โดยตรง
- **Greek P&L attribution** — แยก P&L เป็น delta/gamma/theta/vega ก่อนวัด OOS
- **Settlement price** — ราคาปิดอย่างเป็นทางการของ exchange (≠ last-trade) — ใช้เป็น price ใน EOD data
- **STRIP / delivery month** — เดือนส่งมอบของสัญญา futures — แกนหลักของ term structure และ roll
- **as\_of\_date vs period\_end** — วันที่รู้ข้อมูลจริง vs วันที่ข้อมูลอ้างถึง — join ด้วย merge\_asof บน as\_of\_date เสมอ
- **Adjusted vs raw close** — provider ฟรีบางตัว (เช่น Yahoo) คำนวณ Adj Close ย้อนหลังใหม่ทุก split/div = ข้อมูลอนาคต; backtest ใช้ raw + adj\_factor ถึง t
- **Provided IV/greeks** — OPTION\_VOLATILITY/DELTA\_FACTOR ที่ exchange คำนวณให้ — validate ก่อนใช้ ไม่ต้อง re-solve เสมอ
- **Black-76** — option pricing model สำหรับ futures options — underlying คือ futures (no carry) ไม่ใช่ spot ผิดจาก BS ตรง delta = e^(-rT)·N(d₁) แทน N(d₁); ใช้ BS แทน Black-76 = delta ผิด ~e^rT
- **BS-Merton** — Black-Scholes ที่บวก continuous dividend yield q — สำหรับ equity ที่จ่ายปันผล / index option
- **Net Greeks (spread)** — ผลรวมเชิงเส้น ±qty × leg\_greek; สำหรับ calendar spread ต้องแยก vega\_short\_term/long\_term + vega\_term\_risk (vega\_total เพียงอย่างเดียวจับ term-structure shift ไม่ได้)
- **Vega term risk** — vega\_long\_term − vega\_beta × vega\_short\_term — จับความเสี่ยงที่ short/long-end IV เคลื่อนไม่ขนานกัน root cause ของ vega bleed ใน calendar
- **DTE convention** — basis (calendar/trading) + day\_count (act\_365/act\_360/bus\_252) + expiry inclusive/exclusive — ทุก adapter ต้องอ้าง core/dte.py เดียวกัน ไม่งั้น Greek ของแต่ละขาในสองหน่วยที่ต่าง
- **Symbology silent killer** — PRODUCT\_ID/HUB/CONTRACT map ผิดไม่ throw error — schema ถูก, ค่า normal — แต่ join ผสม contract กัน ตรวจด้วย uniqueness + round-trip + no-orphan
- **Data audit (lightweight)** — snapshot ของ stage ที่เก็บ row\_count + schema\_hash + data\_hash + key\_stats + na\_pattern ใน `outputs/audit/` สำหรับ debug ไม่ใช่ MLOps observability
- **Raw data versioning** — เก็บ raw data เป็น immutable partitioned parquet (partition by ingested\_at) ห้าม overwrite — ป้องกัน invisible data change จาก provider + ทำ reproducibility ได้
- **available\_at** — เวลาที่ข้อมูลพร้อมจริงๆ (≥ as\_of\_date + release lag) ใช้เป็น join key แทน as\_of\_date — ป้องกัน look-ahead bias จาก EIA/COT/earnings ที่ publish ช้ากว่าวันที่อ้างถึง
- **Release lag** — เวลาระหว่าง as\_of\_date กับ available\_at — EIA ~5 วัน, COT ~3.5 วัน, earnings 2–8 สัปดาห์; ระบุใน cfg["available\_at\_lag"] ไม่ hardcode
- **Transaction cost (3 levels)** — L1: fixed cost per leg; L2: bid-ask scaling (DTE + moneyness + vol regime); L3: market impact (Almgren-Chriss). เริ่ม L1 แล้วขยับเมื่อ validate แล้วเท่านั้น
- **Attribution waterfall** — แยก Gross P&L → layers (Greek/factor) → residual → cost → financing → Net P&L; options ใช้ Greek decompose, equity ใช้ factor decompose, cost/financing ชั้นสุดท้ายเหมือนกัน
- **Vega term risk (waterfall)** — layer ใน options waterfall ที่จับ P&L จาก term structure shift ไม่ขนาน — root cause ของ calendar spread ขาดทุนใน IV compression; vega\_total = 0 แต่ layer นี้ ≠ 0
- **Alpha (equity waterfall)** — Gross P&L − factor P&L; ถ้าเล็กมากแปลว่า strategy = beta disguised ซื้อ ETF ถูกกว่า; ต้องใช้ PIT factor loading ไม่ใช่ full-sample

*Quant Pipeline Framework · Implementation Blueprint v1.4  
v1.4: data versioning (immutable partition, ห้าม overwrite) · available\_at timestamp (release lag per data type) · transaction cost model 3 ระดับ (fixed / bid-ask scaling / market impact) · performance attribution waterfall (Greek decompose สำหรับ options, factor decompose สำหรับ equity) · core/txcost.py · core/attribution.py · ingestion/versioned\_cache.py · raw/ directory  
v1.3: tests & data audit เป็น hard requirement · Greeks & spread math (Black-76, IV solver, net greeks for calendar) · provider language genericized  
v1.2: RAW\_SCHEMA ตาม data จริง · field mapping + parser · future/option split · provider PIT caveats  
v1.1: ingestion layer · generic naming · per-fold/per-regime diagnosis · full metric set + overfitting · KL+JS gate  
Iron rule: core/ และ adapters/ ห้ามมีชื่อ instrument จริง · futures options ใช้ Black-76 เสมอ · join ด้วย available\_at ไม่ใช่ as\_of\_date · waterfall ต้องใช้ Greek PIT entry-time เท่านั้น*
