# Two-Column Paper Figure Set

ไฟล์ในโฟลเดอร์นี้เป็นเวอร์ชัน compact สำหรับ paper แบบ 2 columns โดยออกแบบให้วางใน `\columnwidth` ได้ง่ายกว่าชุด state diagram เต็มใน `docs/architecture/sections/`

## Recommendation

- ใช้ `fig1_overview_2col.mmd` เป็นรูปหลักใน Introduction หรือ System Overview
- ใช้ `fig2_data_preparation_2col.mmd` ใน subsection ที่พูดเรื่อง guarded data input, contract/quarantine, coverage, schema guard และ adapter
- ใช้ `fig3_validation_diagnostics_2col.mmd` ใน subsection ที่พูดเรื่อง validation protocol, data-quality scorecard, CDC/break ledger และ purged/embargoed walk-forward CV folds
- ใช้ `fig4_metrics_reporting_2col.mmd` ใน subsection ที่พูดเรื่อง all-fold evaluation/reporting, strategy-required metrics input, diversity annotations และ manifest artifacts

ถ้าต้องการอธิบายละเอียดมากขึ้น ให้ใช้ state diagram ตัวเต็มใน appendix หรือ supplementary material แทนการย่อใส่คอลัมน์เดียว

## IEEE Layout Guidance

สำหรับ 2-column IEEE paper:

- รูป compact เหล่านี้ควรวางด้วย `figure` และ `width=\columnwidth`
- รูปใหญ่ `docs/architecture/pipeline_execution.mmd` ควรวางด้วย `figure*` หรือ appendix
- หลีกเลี่ยงการใส่ label ยาวในกล่อง เพราะเมื่อย่อแล้วตัวอักษรจะเล็กกว่าเนื้อหา paper
- หลัง export แล้วควรเช็กว่า font ในรูปไม่ต่ำกว่า 7 pt เมื่อวางจริง

## LaTeX Example

```latex
\begin{figure}[t]
  \centering
  \includegraphics[width=\columnwidth]{images/fig3_validation_diagnostics_2col.pdf}
  \caption{Validation, data-quality, CDC, and purged/embargoed walk-forward cross-validation protocol.}
  \label{fig:validation_protocol}
\end{figure}
```

ถ้าต้องใช้รูปใหญ่:

```latex
\begin{figure*}[t]
  \centering
  \includegraphics[width=0.92\textwidth]{images/janus_architecture_full.pdf}
  \caption{Full Janus pipeline execution architecture.}
  \label{fig:janus_full_architecture}
\end{figure*}
```

## Export Commands

```bash
mmdc -i docs/architecture/sections/two_col/fig1_overview_2col.mmd -o docs/assets/images/fig1_overview_2col.pdf -b white
mmdc -i docs/architecture/sections/two_col/fig2_data_preparation_2col.mmd -o docs/assets/images/fig2_data_preparation_2col.pdf -b white
mmdc -i docs/architecture/sections/two_col/fig3_validation_diagnostics_2col.mmd -o docs/assets/images/fig3_validation_diagnostics_2col.pdf -b white
mmdc -i docs/architecture/sections/two_col/fig4_metrics_reporting_2col.mmd -o docs/assets/images/fig4_metrics_reporting_2col.pdf -b white
```
