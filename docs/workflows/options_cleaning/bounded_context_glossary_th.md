# พจนานุกรม Bounded Context สำหรับ Options Cleaning Workflow

เอกสารนี้เป็นเวอร์ชันภาษาไทยของ glossary กลาง ใช้คุยให้ตรงกันระหว่าง
ผู้เชี่ยวชาญโดเมน และผู้เชี่ยวชาญเทคนิค ก่อนนำ workflow ในชุด
`options_cleaning_*` ไปทำ slide หรือ paper

เป้าหมายหลักคือแยกให้ชัดว่าแต่ละคำหมายถึงอะไรในเชิงธุรกิจ หมายถึงอะไรในโค้ด
และเมื่อพบเงื่อนไขนั้นแล้ว row หรือ run จะถูก quarantine, filter, flag, cap,
หรือแค่ report

## โครงสร้างโฟลเดอร์

ชุด workflow ทั้งหมดอยู่ใต้ `docs/workflows/options_cleaning/`

- `options_cleaning_01_intake_bronze/`: รับข้อมูลเข้า, fixed-version guard,
  bronze contract, quarantine, coverage, และ handoff ไป family schema
- `options_cleaning_02_silver_adapter/`: standardize option-chain, adapter สำหรับ
  equity/futures options, การคำนวณ IV/Greeks/PCP/VRP, และ option-universe filtering
- `options_cleaning_03_validation_observability/`: validators, data-quality scorecard,
  CDC, break ledger, manifest, และ artifacts ของ prepared data

## กฎคำศัพท์กลาง

- **Quarantine** หมายถึง row ไม่ผ่าน bronze contract แล้วถูกแยกออกก่อนเข้า
  silver adapter เท่านั้น อย่าใช้คำนี้กับ research-universe filter
- **Filter / exclude / drop** หมายถึง row ถูกตัดออกจาก universe ที่ใช้วิจัยหรือคำนวณ
  แต่ไม่ได้แปลว่าเป็น bad data เสมอไป
- **Flag** หมายถึง row ยังอยู่ใน dataset แต่มีคอลัมน์เหตุผลติดไว้เพื่อให้ตรวจสอบได้
- **Cap** หมายถึงค่าตัวเลขถูกปรับตาม policy และต้อง trace ได้ใน CDC
- **Report** หมายถึงสรุปเป็นสถานะระดับ run หรือ artifact โดยอาจไม่เปลี่ยน row
- **Validated prepared dataset** คือคำที่ควรใช้กับ output ปัจจุบัน อย่าเรียก Gold
  จนกว่าจะมี Gold serving layer จริงใน implementation

## Layer 01: Intake และ Bronze

| คำใน diagram | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `Instrument YAML` | configuration ทางธุรกิจของ instrument และ experiment | config ถูก load แล้ว normalize ด้วย `normalize_config()` | ได้ normalized config |
| `family` | ประเภท asset เช่น equity options หรือ futures options | `cfg["family"]` ใช้เลือก provider, contract, adapter | ใช้ route workflow |
| `Fixed-input guard` | backtest ต้องใช้ raw input ที่นิ่ง ไม่ใช่ live feed ที่เปลี่ยนได้ | ตรวจ `data_file_sha256` หรือ immutable `data_version` | fail fast ถ้า input ไม่ถูก pin |
| `Provider/cache read` | ขั้น extract ข้อมูลจากแหล่งต้นทาง | อ่านจาก yfinance, vendor, settlement provider หรือ `VersionedCache.read()` | ได้ raw provider frame |
| `Raw provider frame` | ข้อมูลหลังอ่านจาก source และ standardize ขั้นต้น | DataFrame ก่อนเข้า bronze contract | input ของ contract validation |
| `Bronze contract` | ข้อตกลงแรกระหว่าง source กับ pipeline | YAML contract เช่น `equity_options.v1` หรือ `settlement_options.v1` | แยก row ที่ผ่านกับ row ที่ quarantine |
| `Structural checks` | file มี field ที่จำเป็น และ type ใช้ได้หรือไม่ | required columns, dtype coercion, key rounding | row ที่ผิดได้ `_quarantine_reason` |
| `Semantic checks` | ค่าทางเศรษฐกิจสมเหตุสมผลหรือไม่ | price, strike, right, expiry และ rule ราย row | row ที่ผิดได้ `_quarantine_reason` |
| `PIT check` | ข้อมูลนี้รู้ได้จริง ณ วันที่อ้างหรือไม่ | `available_at` ต้องไม่ก่อน `as_of_date` | row ที่ผิดได้ `_quarantine_reason` |
| `Symbology / orphan` | identity ของ product map ได้ชัดเจนหรือไม่ | ตรวจ `product_id`, `contract_root`, `hub` กับ product map | orphan row ถูก quarantine เมื่อ enforce |
| `Distributional frame checks` | sanity check ระดับ batch/run | null-rate checks; PSI มีในเอกสารแต่รอ reference vintage | report หรือ break ระดับ frame ไม่ใช่ row quarantine เสมอไป |
| `Coverage SLA` | ข้อมูลครอบคลุมช่วงวันที่ที่ขอพอหรือไม่ | expected trading days, coverage ratio, max date gap | pass/warn/fail ระดับ run และอาจเกิด break |
| `Family schema guard` | เผลอเอาข้อมูลที่ไม่ใช่ option chain เข้า option adapter หรือไม่ | ต้องมีคอลัมน์เช่น `expiry`, `right`, `strike`, `price` | fail fast ก่อน adapter math |

## Layer 02: Silver Adapter

| คำใน diagram | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `Normalize option columns` | ทำให้ field ของ option chain อยู่ในรูปเดียวกันก่อนคำนวณ | แปลงวันที่, ตัวเลข, `right = C/P`, และ option mask | coerced columns ใน DataFrame |
| `Option mask` | row ไหนคือ option contract | infer จาก `instrument_type == option` หรือ `right + strike` ที่ถูกต้อง | ใช้กับ filters และ diagnostics |
| `EquityOptionsAdapter` | path เตรียมข้อมูล equity/index options | สร้าง input สำหรับ BS-Merton จาก spot/underlying fields | ได้ `S`, `F`, `option_price`, `price_std` |
| `FuturesOptionsAdapter` | path เตรียมข้อมูล futures options | สร้าง continuous futures, term structure, event flags, underlying future map | ได้ rows ที่พร้อมสำหรับ Black-76 |
| `Underlying future map` | option นี้ควรใช้ futures price ตัวไหนเป็น underlying | join option rows กับ support future rows ด้วย date และ contract identity | missing map อาจถูก drop หรือ raise เมื่อ strict |
| `price_std` | ราคา underlying ที่ standardize แล้วสำหรับ downstream | สำหรับ options โดยทั่วไปคือ underlying/forward price ไม่ใช่ option premium | ใช้คำนวณ returns, validators, metrics |
| `option_price` | premium ของ option | copy จาก raw `price` ใน option rows | ใช้ solve IV และ pricing checks |
| `DTE / T` | เวลาจาก observation date ถึง expiry | `core.dte.compute_dte_series()` สร้าง `T`; `dte_days` เป็น helper แบบ calendar day | row หลัง expiry จะได้เวลา unusable/NaN |
| `Universe filters before pricing` | policy เลือก universe ก่อนคำนวณราคาแพง ๆ | `min_dte_days`, `max_dte_days`, `min_option_price`, `max_relative_spread` | drop option rows และนับใน `option_quality.universe.drop_by_reason` |
| `IV selection/solve` | เลือก IV ที่จะใช้ต่อหนึ่ง option row | `build_iv_surface()` ตั้ง canonical `iv` จาก `iv_provided` หรือ `solve_iv()` | เพิ่ม `iv`, `iv_source_used`, optional `iv_solved`, `iv_diff`, `iv_flag` |
| `Provided IV` | IV ที่ exchange/vendor ให้มา | `iv_source: provided`, ใช้ `iv_provided` และ validate เทียบกับ self-solved IV ได้ | ปกติคงไว้ ยกเว้นโดน max-IV filter |
| `Solved IV` | IV ที่ implied จาก market premium ภายใต้ pricing model ของโปรเจกต์ | `iv_source: solve`, root solve ด้วย price, underlying, strike, T, rate, right | ถ้า solve ไม่ได้จะเป็น missing และอาจถูก filter |
| `Max-IV filter` | ตัด IV ที่สูงเกิน policy หรืออยู่นอก universe | `option_universe.max_iv`; `iv_cap` เป็น alias เก่า | drop `iv_above_cap` หรือ `iv_missing_or_unsolved` เฉพาะ option rows |
| `Greeks` | sensitivities ที่ใช้กับ diagnostics หรือ strategy features | `delta`, `gamma`, `vega`, `theta`, `rho` จาก `core.greeks.batch_greeks()` | เพิ่ม columns และใช้ต่อกับ delta-band filter ได้ |
| `Delta-band filter` | เลือก option ที่อยู่ในช่วง moneyness/sensitivity ที่ต้องการ | `option_universe.delta_band` ใช้ provided หรือ computed delta | drop `delta_below_min` / `delta_above_max` |
| `PCP` | sanity check ด้วย put-call parity | จับคู่ call/put ด้วย date, expiry, strike, contract identity | flag `_pcp_flag`, `pcp_pair_missing`, `pcp_duplicate_pair` |
| `VRP sign` | IV แพงหรือถูกเมื่อเทียบกับ realized vol | `vrp = iv - vol_std` แล้วจัด bucket เป็น sign | เพิ่ม `vrp`, `vrp_sign` |
| `skew_25d` | diagnostic skew 25-delta ที่ตั้งใจจะมี | implementation ปัจจุบันยังเป็น placeholder คืนค่า `0.0` | อย่านำเสนอว่าเป็น full skew surface หรือ active regime axis |
| `Silver quality flags` | เก็บ row ที่น่าสงสัยแต่ยังไม่จำเป็นต้อง invalid | checks เรื่อง IV, delta sign/range, premium ต่ำกว่า intrinsic | เพิ่ม `_iv_quality_flag`, `_delta_quality_flag`, `_premium_quality_flag` |
| `Prepared DataFrame + core_cfg` | output contract ของ adapter สำหรับ stage ถัดไป | cleaned DataFrame และ config เช่น `identity_cols`, `price_col`, `return_col` | input หลักของ validators และ reporting |

หมายเหตุสำคัญ: ในโค้ดใช้ชื่อ function ว่า `build_iv_surface()` แต่ behavior ปัจจุบันคือ
เลือก, validate, หรือ solve IV ราย row ยังไม่ใช่ surface interpolation หรือ smoothing เต็มรูปแบบ
ดังนั้นใน slide/paper ควรใช้คำว่า **IV selection/solve** มากกว่า **IV surface**

## Layer 03: Validation และ Observability

| คำใน diagram | ความหมายสำหรับ domain expert | ความหมายในโค้ด | ผลต่อ row / output |
| --- | --- | --- | --- |
| `Return clip hook` | policy เสริมสำหรับ return ที่ extreme | adapter method เช่น `apply_return_clip()` ถ้ามี | default มักเป็น flag-only |
| `PIT-MAD return policy` | ตรวจ return outlier โดยไม่ใช้ข้อมูลอนาคต | point-in-time MAD policy และ optional derived `return_winsorized` | tag returns; ไม่ควร overwrite canonical returns เงียบ ๆ |
| `Stage 1 validators` | sanity pass สุดท้ายก่อนเข้า experiment stages | เรียก `logical_bounds_check()`, `missing_completeness()`, `outlier_cap()` | เพิ่ม flags และอาจ cap price series บางชนิด |
| `logical_bounds_check()` | ค่าราย row สมเหตุสมผลทางตรรกะ/เศรษฐกิจไหม | ตรวจ price > 0, option premium > 0, premium >= intrinsic, volume >= 0, bid <= ask, IV > 0, strike > 0 | เพิ่ม `_bound_flag`, `_bound_reason`; ไม่ quarantine |
| `missing_completeness()` | มีวันที่หาย, duplicate grain, หรือ liquidity ต่ำไหม | ตรวจ duplicate identity-date, `date_gap`, open-interest floor, optional volume floor | เพิ่ม `_missing_flag`, `_missing_reason` |
| `date_gap` | ช่องว่างของ observation ใน time series | ระยะห่างระหว่าง `as_of_date` ต่อเนื่องในแต่ละ identity; default เป็น business/trading days | flag เช่น `date_gap>5bd`; ไม่ใช่ gap ของ strike grid |
| `outlier_cap()` | วิธีจัดการ price-series outlier | expanding MAD clip โดยใช้เฉพาะข้อมูลอดีตเท่าที่เหมาะสม | เพิ่ม `_outlier_flag`; อาจแก้ค่า `price_col` ที่กำหนด |
| `outlier_gap` | ยังไม่ใช่ term ในโค้ดปัจจุบัน | โดยมากน่าจะหมายถึง `date_gap` หรือ `outlier_cap` ต้องถามให้ชัดก่อนใช้ | หลีกเลี่ยงใน slide จนกว่าจะนิยาม |
| `Data-quality scorecard` | สรุปสุขภาพข้อมูลระดับ run | rate ของ return outliers, price outliers, bounds, missingness, quarantine, coverage | pass/warn/fail และอาจ enforce failure |
| `AQL / LTPD` | threshold ของ defect rate ที่รับได้/รับไม่ได้ | budgets ใน scorecard สำหรับแต่ละ quality dimension | คุมสถานะ pass/warn/fail |
| `CDC stage chain` | มีอะไรเปลี่ยนระหว่าง pipeline stages | diff ingestion -> adapter -> return_clip -> validators | ได้ change records พร้อม reason |
| `Change ledger` | audit log ของ schema, row, cell changes | JSONL มี stage, key, column, before/after, reason | เขียนไปที่ `outputs/diff` |
| `Break ledger` | รายการปัญหาที่ต้อง review | unattributed cell changes, unexpected row additions, unexplained row drops, coverage breaks | เขียนไปที่ `outputs/breaks` |
| `Manifest` | ใบเสร็จ reproducibility | code version, config hash, input/output hashes, contract versions, knowledge cutoff | เขียนไปที่ `outputs/manifest` และ run output dir |
| `option_quality` | quality summary เฉพาะ options | สรุป IV, delta, PCP, universe-drop, silver flags | อยู่ใน `summary.json` |
| `Validated prepared dataset` | output สุดท้ายของ workflow นี้ | prepared CSV/parquet พร้อม metadata artifacts หลัง validation/observability | output ปัจจุบัน ไม่ใช่ Gold layer |

## ค่าที่ต้องให้ Domain Expert ช่วย sign off

ค่าต่อไปนี้เป็น policy choice ไม่ใช่ข้อเท็จจริงทางเทคนิคเพียงอย่างเดียว:

- `coverage_min_ratio` และ `coverage_max_gap_days`: ยอมให้ missing history ได้แค่ไหน
- `date_gap_days` / `max_gap_days`: gap ของ observation ที่รับได้ในแต่ละ asset family
- `min_oi`, `futures_oi_floor`, `min_volume`: liquidity floors
- `min_dte_days`, `max_dte_days`: option horizon universe
- `min_option_price`: premium ขั้นต่ำเพื่อให้ IV/Greek math เสถียร
- `max_relative_spread`: cutoff ของ liquidity/spread
- `option_universe.max_iv`: IV สูงสุดที่ยอมรับใน research universe
- `delta_band`: ช่วง moneyness/sensitivity ที่ต้องการ
- `iv_validate_threshold`: ส่วนต่างที่ยอมรับได้ระหว่าง provided IV กับ self-solved IV
- AQL/LTPD budgets ของ data-quality scorecard

## Label ภาษาไทยที่แนะนำสำหรับ slide

| Technical label | Label ที่แนะนำ |
| --- | --- |
| `logical_bounds_check()` | ตรวจค่าทางตรรกะ |
| `missing_completeness()` | ตรวจความครบถ้วนและ duplicate |
| `date_gap` | ช่องว่างของ observation |
| `outlier_cap()` | จัดการ outlier แบบ point-in-time |
| `build_iv_surface()` | เลือกหรือ solve IV |
| `Max-IV filter` | กรอง IV เกินเพดาน universe |
| `CDC` | audit การเปลี่ยนแปลงระหว่าง stages |
| `Break ledger` | ledger สำหรับการเปลี่ยนแปลงที่อธิบายไม่ได้ |
| `core_cfg` | runtime contract ของ prepared data |

## Checklist ก่อนแก้ figure

- ทุก label ใน diagram ต้อง map ได้กับคำใน glossary นี้หนึ่งความหมายหลัก
- row ที่ถูก drop ต้องแยกชัดว่าเป็น quarantine หรือ research-universe exclusion
- value mutation ต้องหลีกเลี่ยง, แยกเป็น derived column, หรือถูก attribute ผ่าน CDC
- run-level failure ต้องแยกจาก row-level failure
- ห้าม claim ว่ามี Gold layer, full IV surface interpolation, หรือ active skew regime axis
  จนกว่า implementation จะมีจริง
