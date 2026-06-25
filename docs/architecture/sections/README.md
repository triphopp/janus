# Janus Architecture Figures for Academic Writing

ชุดนี้แยกรูปจาก diagram ใหญ่ใน `docs/architecture/pipeline_execution.mmd` เพื่อให้ใช้เขียนรายงานหรือ paper ได้ง่ายขึ้น รูปใหญ่ยังเหมาะกับ repo หรือ appendix ส่วนรูปย่อยเหมาะกับ body ของงาน เพราะแต่ละรูปมีภาระการอธิบายน้อยกว่าและอ่านออกเมื่อถูกย่อในหน้า paper

ถ้า paper เป็น 2 columns ให้ใช้เวอร์ชัน compact ใน `docs/architecture/sections/two_col/` เป็นหลัก เพราะออกแบบให้วางใน `\columnwidth` ได้ดีกว่า state diagram เต็ม

## Recommended Figure Sequence

1. `fig1_overview.mmd`  
   ใช้ใน Introduction, System Overview หรือ Methodology ตอนต้น เพื่อบอกภาพรวมของ pipeline ทั้งหมด

2. `fig2_data_preparation.mmd`  
   ใช้ใน Data Preparation หรือ Dataset Processing เพื่ออธิบาย configuration, fixed input guard, provider/cache, bronze contract, quarantine, coverage SLA, family schema guard, adapter และ prepared data contract

3. `fig3_validation_diagnostics.mmd`  
   ใช้ใน Validation Methodology หรือ Experimental Protocol เพื่อแยกความหมายของ data validation, data-quality scorecard, CDC/break ledger, purged/embargoed walk-forward cross-validation, stability diagnostics และ regime diversity gate

4. `fig4_metrics_reporting.mmd`  
   ใช้ใน Evaluation Protocol หรือ Reporting เพื่ออธิบายว่า metrics ใช้ all purged walk-forward folds พร้อม diversity annotations, เลือก strategy return หรือ market diagnostic return, ตรวจ sample floor แล้วสร้าง artifacts อะไรบ้าง

5. `fig5_greek_only_workflow.mmd`  
   ใช้ใน Options Methodology หรือ System Extension เพื่ออธิบายว่า Janus สามารถรันเฉพาะ Greek computation ได้ โดยใช้ shared Greek engine เดียวกับ full pipeline และข้าม splitter/metrics/reporting stages ที่ไม่จำเป็น

   Related detailed views:
   `fig5a_greek_engine.mmd` แสดง shared Greek engine, input contract, backend selection และ output consumers
   `fig5b_greek_only_flow.mmd` แสดง execution sequence ของ Greek-only runner ตั้งแต่ input loading จนเขียน output

   Implementation plan:
   `memory/plans/greek_only_engine_implementation_plan.md` แยก phase, acceptance criteria และ test gates สำหรับ agent ที่จะ implement ระบบนี้ต่อ

## How This Is Usually Written

การแบ่งรูปแบบนี้ทำได้และพบได้บ่อยในงานวิชาการ โดยมักใช้หนึ่งในสองรูปแบบ:

- ใช้รูปใหญ่หนึ่งรูปใน System Overview แล้วใช้รูปย่อยใน Methodology subsections
- ใช้ multi-panel figure เช่น Fig. 2(a), Fig. 2(b), Fig. 2(c) เพื่อแสดง pipeline แต่ละส่วน

สำหรับงานนี้ แนะนำแบบแรกถ้าต้องการเล่าให้ผู้อ่านค่อย ๆ ตามระบบ และใช้ diagram ใหญ่ใน appendix หรือ repo documentation

สำหรับ paper แบบ 2 columns แนะนำให้ใช้รูป compact เป็น `figure` ปกติในคอลัมน์เดียว และเก็บ diagram ใหญ่ไว้เป็น `figure*` หรือ appendix หากต้องการแสดง execution flow ทั้งหมดในรูปเดียว

## Suggested Captions

**Fig. 1. Overview of the Janus quantitative pipeline.** The pipeline transforms instrument-level configuration and guarded market data into data-quality-aware, leakage-controlled validation metrics and reproducible reporting artifacts.

**Fig. 2. Data preparation flow.** Instrument configuration selects guarded provider/cache input, applies the bronze contract, quarantine, coverage checks, and family schema guard, then produces a prepared DataFrame and core configuration through the asset-specific adapter. Option adapters additionally apply universe filters, IV/Greek/PCP checks, and expiry-based label horizons.

**Fig. 3. Validation and diagnostic protocol.** Janus applies data validators, builds an AQL-style data-quality scorecard, records CDC and break-ledger artifacts, then constructs walk-forward cross-validation folds. Training rows whose label horizons overlap validation are purged, an embargo gap is applied before each validation window, and the resulting folds feed PSI, stability diagnostics, and regime diversity checks.

**Fig. 4. Evaluation and reporting flow.** Stage 4 evaluates all purged walk-forward folds and annotates each fold with diversity-gate status rather than dropping failed folds. Strategy-required option runs skip performance metrics when no strategy/PnL returns are present; diagnostic runs can still use market returns. Janus writes performance, diversity, data-quality, prepared-data, diff, break, manifest, summary, and HTML artifacts.

**Fig. 5. Greek-only computation workflow.** Janus can route either the full option pipeline or a Greek-only runner into the same `core.greeks.batch_greeks()` engine. The Greek-only path accepts prepared option rows or performs minimal option preparation, resolves the required pricing inputs, selects a backend, and writes Greek outputs without running validation folds, metrics, or reporting stages.

**Fig. 5a. Shared Greek engine.** The Greek engine resolves model inputs, validates row-level pricing contracts, dispatches to the configured backend, and returns the same Greek columns for both full-pipeline and Greek-only consumers.

**Fig. 5b. Greek-only execution sequence.** A Greek-only runner loads option rows, normalizes columns, resolves IV, time-to-expiry, rates, model, and backend settings, calls the shared batch engine, and writes Greek outputs and a compact quality summary.

## Suggested Paper Narrative

ตัวอย่างการเล่าในรายงาน:

1. เริ่มจาก Fig. 1 เพื่อบอกว่า Janus เป็น pipeline จาก configuration และ guarded data input ไปจนถึง reporting artifacts
2. อธิบาย Fig. 2 ว่าทำไม fixed input guard, bronze contract, quarantine, coverage SLA, family schema guard และ adapter layer ช่วยแยก data operations ออกจาก core statistical pipeline
3. ใช้ Fig. 3 เพื่อเน้นว่า validation ในระบบมี data validation, data-quality scorecard, CDC/break observability และ purged/embargoed walk-forward CV สำหรับป้องกัน leakage
4. ปิดด้วย Fig. 4 เพื่อแสดงว่า metrics และรายงานถูกสร้างจาก all folds พร้อม diversity annotations ไม่ใช่เลือกเฉพาะ folds ที่ผ่าน gate และมี audit/manifest artifacts รองรับ reproducibility
5. ใช้ Fig. 5 เมื่อต้องการอธิบาย extension สำหรับ Greek-only execution โดยเน้นว่า full pipeline และ Greek-only path ใช้ engine เดียวกัน จึงลดโอกาสที่สูตรหรือ convention จะแตกกัน

## Export Commands

ถ้าติดตั้ง Mermaid CLI แล้ว สามารถ export เป็น SVG หรือ PDF ได้ เช่น:

```bash
mmdc -i docs/architecture/sections/fig1_overview.mmd -o docs/assets/images/fig1_overview.svg -b white
mmdc -i docs/architecture/sections/fig2_data_preparation.mmd -o docs/assets/images/fig2_data_preparation.svg -b white
mmdc -i docs/architecture/sections/fig3_validation_diagnostics.mmd -o docs/assets/images/fig3_validation_diagnostics.svg -b white
mmdc -i docs/architecture/sections/fig4_metrics_reporting.mmd -o docs/assets/images/fig4_metrics_reporting.svg -b white
mmdc -i docs/architecture/sections/fig5_greek_only_workflow.mmd -o docs/assets/images/fig5_greek_only_workflow.svg -b white
mmdc -i docs/architecture/sections/fig5a_greek_engine.mmd -o docs/assets/images/fig5a_greek_engine.svg -b white
mmdc -i docs/architecture/sections/fig5b_greek_only_flow.mmd -o docs/assets/images/fig5b_greek_only_flow.svg -b white
```
