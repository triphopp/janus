# พจนานุกรม Bounded Context สำหรับ Stock และ Equity Options Cleaning Workflow

เอกสารนี้ใช้ปรับภาษาให้ตรงกันระหว่าง Domain Expert และ Technical Expert สำหรับ
workflow ของ stock (`equity`) และ equity option (`equity_options`) โดยตั้งใจไม่รวม
คำเฉพาะของ futures options เช่น contract root, hub, delivery month และ Black-76
underlying future map

## โครงสร้างโฟลเดอร์

- `stock_equity_01_intake_bronze/`: source read, fixed-input guard, bronze contracts,
  quarantine, coverage, และ family-schema handoff
- `stock_equity_02_silver_preparation/`: การเตรียม stock price และ equity-option chain
- `stock_equity_03_validation_observability/`: validators, data-quality scorecard,
  CDC, break ledger, manifest และ prepared-data artifacts

## กฎคำศัพท์กลาง

- **Stock** หมายถึง `family: equity` หรือ daily equity price bars ที่เตรียมด้วย
  `EquityAdapter`
- **Equity option** หมายถึง `family: equity_options` หรือ option-chain rows ที่เตรียมด้วย
  `EquityOptionsAdapter`
- **Quarantine** คือ row ไม่ผ่าน bronze contract และถูกแยกก่อนเข้า silver preparation
- **Universe filter** คือการเลือก research universe หลัง option preparation ไม่ใช่ quarantine
- **Flag** คือ row ยังอยู่ใน dataset แต่มีเหตุผลติดไว้ให้ review
- **Validated prepared dataset** คือ output ปัจจุบัน อย่าเรียก Gold

## Layer 01: Intake และ Bronze

| คำ | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `equity` | workflow ของราคาหุ้น | `cfg["family"] == "equity"` | ใช้ `EquityLoaderA` และ `EquityAdapter` |
| `equity_options` | workflow ของ equity option chain | `cfg["family"] == "equity_options"` | ใช้ `EquityOptionsLoaderYF` หรือ vendor feed และ `EquityOptionsAdapter` |
| `Fixed-input guard` | backtest ต้องใช้ raw input ที่นิ่ง | `data_file_sha256` หรือ immutable `data_version` | fail fast ถ้า input ไม่ถูก pin |
| `Equity price source` | daily stock bars | yfinance price loader หรือ versioned cache | raw stock frame |
| `Equity option source` | option chain snapshot หรือ vendor historical chain | yfinance option-chain loader หรือ vendor feed | raw option-chain frame |
| `equity_price.v1` | bronze contract สำหรับ stock bars | ต้องมี `as_of_date`, `symbol`, `raw_close`, `adj_factor`, `volume`, `is_delisted` | row ผิดถูก quarantine |
| `equity_options.v1` | bronze contract สำหรับ equity options | ต้องมี `as_of_date`, `symbol`, `expiry`, `right`, `strike`, `price`, `underlying_price` | row ผิดถูก quarantine |
| `PIT availability` | ข้อมูลต้องรู้ได้ก่อนใช้ตัดสินใจ | `available_at` และ `decision_time` | timing ผิดอาจ fail PIT guards |
| `Coverage SLA` | มีวันที่พอสำหรับช่วงที่ขอหรือไม่ | coverage ratio และ max date gap | pass/warn/fail ระดับ run |
| `yfinance option snapshot` | option chain ปัจจุบัน ไม่ใช่ประวัติ option chain | snapshot วันเดียว | coverage/min-sample gate ควร flag ว่าไม่ใช่ backtest-grade history |

## Layer 02: Silver Preparation

| คำ | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `raw_close` | close จาก provider ที่ใช้คำนวณ return หุ้น | yfinance `Close`; ในเวอร์ชันใหม่เป็น split-adjusted | feed `price_std` ถ้า policy ไม่เปลี่ยน |
| `raw_close_unadj` | ราคาที่ reconstruct ให้ใกล้ true traded historical price | `raw_close * split_factor` | ใช้เป็น diagnostic หรือ level-strategy support |
| `adj_factor` | adjustment factor จาก provider | Adj Close / Close; สำหรับ yfinance >= 1.x เป็น dividend-only | เก็บไว้เป็น diagnostics |
| `split_factor` | split adjustment แบบ retroactive ที่ provider bake ไว้ | product ของ future split ratios หลัง date `t` | warning เรื่อง price-level leakage risk |
| `dividend` | cash dividend ใน ex-date | ถูกใส่ใน total return ใน `EquityAdapter` | feed `return_raw` |
| `price_std` | ราคาที่ standardize แล้ว | stock: prepared close; option: underlying price | feed returns, validators, metrics |
| `return_raw` | total return หลักของ stock pipeline | `(price_std + dividend) / previous_price - 1` | canonical stock return |
| `return_price` | price-only diagnostic return | `price_std / previous_price - 1` | diagnostic |
| `vol_std` | realized volatility estimate | rolling std ของ `return_std` | feature/diagnostic |
| `survivor_flag` | visibility เรื่อง delisting/survivorship | มาจาก `is_delisted` ถ้ามี | row ยังอยู่ |
| `option_price` | premium ของ equity option | copy จาก option-chain `price` | feed IV solving และ pricing diagnostics |
| `BS-Merton inputs` | inputs สำหรับ pricing equity options | `S`, `F` compatibility, `T`, `r`, `iv`, และ `q` เมื่อ config มี | feed Greeks และ PCP |
| `IV selection/solve` | เลือก IV ต่อ option row | ใช้ `iv_provided` หรือ solve IV ผ่าน `solve_iv()` | เพิ่ม canonical `iv` |
| `Option universe filter` | policy เลือก option rows สำหรับ research | DTE, premium, spread, max IV, delta band | drop option rows แต่ไม่ใช่ quarantine |
| `PCP` | put-call parity check | จับคู่ call/put ด้วย date, expiry, strike, symbol | เพิ่ม PCP flags |
| `skew_25d` | skew diagnostic ที่ตั้งใจจะมี | implementation ตอนนี้ยังเป็น placeholder `0.0` | อย่านำเสนอว่าเป็น full skew surface |

## Layer 03: Validation และ Observability

| คำ | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `PIT-MAD return policy` | ตรวจ stock return ที่ extreme โดยไม่ใช้อนาคต | `EquityAdapter.apply_return_clip()` เป็น stage แยกเพื่อ CDC | default เป็น tag-only |
| `cross-provider validation` | ตรวจว่า large stock return เป็นเหตุการณ์จริงไหม | optional comparison กับ Stooq/AlphaVantage | update return-outlier reason/status |
| `logical_bounds_check()` | ตรวจค่าทางตรรกะ | price, premium, intrinsic, volume, bid/ask, IV, strike | เพิ่ม `_bound_flag` |
| `missing_completeness()` | ตรวจ missing dates, duplicate grain, liquidity floor | duplicate identity-date, `date_gap`, OI/volume floor | เพิ่ม `_missing_flag` |
| `outlier_cap()` | จัดการ outlier แบบ PIT บน price series ที่เหมาะสม | expanding MAD cap สำหรับ `price_col` ที่ config กำหนด | เพิ่ม `_outlier_flag`, อาจแก้ `price_col` |
| `Data-quality scorecard` | health summary ระดับ run | return, price, bounds, missing, quarantine, coverage | pass/warn/fail |
| `CDC` | audit การเปลี่ยนแปลงระหว่าง stages | ingestion -> adapter -> return_clip -> validators | change ledger JSONL |
| `Break ledger` | queue สำหรับ review การเปลี่ยนแปลงที่อธิบายไม่ได้ | UNATTRIBUTED cell changes, unexpected rows, coverage breaks | break JSONL |
| `Manifest` | ใบเสร็จ reproducibility | config hash, code version, input/output hashes | manifest JSON |

## Label ที่แนะนำสำหรับ slide/paper

| Technical label | Label ที่แนะนำ |
| --- | --- |
| `EquityAdapter` | การเตรียมราคาหุ้น |
| `EquityOptionsAdapter` | การเตรียม equity option |
| `raw_close_unadj` | การ reconstruct ราคาซื้อขายจริง |
| `price_adjustment_warning` | คำเตือนเรื่อง provider adjustment |
| `apply_return_clip()` | policy ตรวจ stock return outlier แบบ PIT |
| `build_iv_surface()` | เลือกหรือ solve IV |
| `logical_bounds_check()` | ตรวจค่าทางตรรกะ |
| `missing_completeness()` | ตรวจความครบถ้วน |
| `outlier_cap()` | จัดการ outlier แบบ PIT |

## Checklist ก่อนแก้ figure

- อย่าเอาคำเฉพาะ futures-options มาปนในภาพ stock/equity-options
- อย่าเรียกข้อมูล yfinance equity options ว่า historical option-chain data
- อย่าเรียก final prepared output ว่า Gold
- อย่านำเสนอ `skew_25d` ว่าเป็น surface/regime feature ที่เสร็จแล้ว
- แยก stock return outlier policy ออกจาก option universe filtering ให้ชัด
