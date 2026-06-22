export type Severity = "high" | "medium" | "low" | string;
export type BreakStatus =
  | "DETECTED"
  | "TRIAGED"
  | "ESCALATED"
  | "ACKNOWLEDGED"
  | "AUTO_RESOLVED"
  | "CLOSED"
  | string;

export type RunRow = {
  run_id: string;
  created_at?: string | null;
  date_range?: [string, string] | string[] | null;
  symbol?: string | null;
  instrument?: string | null;
  family?: string | null;
  code_version?: string | null;
  config_hash?: string | null;
  knowledge_cutoff?: string | null;
  n_rows?: number | null;
  metrics_input?: string | null;
  strategy_metrics_available?: boolean | null;
  sharpe_mean?: number | null;
  dq_status?: string | null;
  dq_worst_dimension?: string | null;
  dq_fail_count?: number | null;
  changes: number;
  unattributed: number;
  breaks_total: number;
  breaks_open: number;
  sev_high: number;
  sev_medium: number;
  sev_low: number;
  has_diff: boolean;
  has_report: boolean;
  adjustment_warning_rows?: number | null;
  adjustment_factor_rows?: number | null;
  adjustment_policy?: string | null;
  adjustment_status?: string | null;
  adjustment_max_abs_price_diff?: number | null;
  price_adjustments?: Record<string, unknown> | null;
};

export type FleetSummary = {
  n_runs: number;
  total_changes: number;
  total_unattributed: number;
  total_adjustment_warnings: number;
  dq_runs_failing?: number;
  dq_runs_warning?: number;
  breaks_total: number;
  breaks_open: number;
  sev_high: number;
};

export type RunsResponse = {
  summary: FleetSummary;
  runs: RunRow[];
};

export type TrendDay = {
  day: string;
  high: number;
  medium: number;
  low: number;
  total: number;
};

export type BreakHistoryEntry = {
  to_status: BreakStatus;
  actor_id?: string | null;
  actor_role?: string | null;
  at?: string | null;
  reason_code?: string | null;
  note?: string | null;
  prev_hash?: string | null;
  entry_hash?: string | null;
};

export type BreakRow = {
  run_id: string;
  break_id: string;
  detected_at?: string | null;
  severity: Severity;
  status: BreakStatus;
  type?: string | null;
  stage?: string | null;
  stage_from?: string | null;
  stage_to?: string | null;
  field?: string | null;
  before?: unknown;
  after?: unknown;
  delta?: unknown;
  key?: Record<string, unknown> | null;
  history?: BreakHistoryEntry[];
  chain_valid?: boolean;
  lineage_impact?: string[];
};

export type BreaksResponse = {
  n: number;
  breaks: BreakRow[];
};

export type ChangeRecord = {
  stage_from?: string | null;
  stage_to?: string | null;
  change_type?: string | null;
  column?: string | null;
  reason?: string | null;
  before?: unknown;
  after?: unknown;
  delta?: unknown;
  key?: Record<string, unknown> | null;
};

export type StageHop = {
  stage_from?: string | null;
  stage_to?: string | null;
  changes: number;
  cell_mod: number;
  schema_add: number;
  row_add: number;
  row_drop: number;
  unattributed: number;
};

export type TaggedOutlier = {
  as_of_date?: string | null;
  symbol?: string | null;
  return_raw?: number | null;
  return_std?: number | null;
  return_winsorized?: number | null;
  _return_outlier_policy?: string | null;
  _return_validation_status?: string | null;
  _return_outlier_reason?: string | null;
  _return_outlier_direction?: string | null;
  _return_outlier_zscore?: number | null;
  _return_outlier_severity?: string | null;
  _return_prior_median?: number | null;
};

export type DataQualityDimension = {
  name: string;
  rate: number;
  n_defect: number;
  n_total: number;
  aql: number;
  ltpd: number;
  status: string;
};

export type DataQualityScorecard = {
  status: string;
  enforcement: string;
  worst_dimension: string;
  dimensions: DataQualityDimension[];
};

export type RunDetail = RunRow & {
  breaks: BreakRow[];
  changes_sample: ChangeRecord[];
  stage_hops: StageHop[];
  tagged_return_outliers: TaggedOutlier[];
  tagged_return_outlier_summary?: {
    total?: number;
    shown?: number;
    by_policy?: Record<string, number>;
    by_status?: Record<string, number>;
    by_reason?: Record<string, number>;
    by_direction?: Record<string, number>;
    by_severity?: Record<string, number>;
  };
  data_quality?: DataQualityScorecard | null;
};

export type RawRow = Record<string, unknown>;

// ── Dashboard view-model types (stable, schema-versioned) ─────────────────────

export type DashboardMetric = {
  id: string;
  label: string;
  value: number | string | null;
  format: "integer" | "number" | "text" | string;
  status?: string | null;
};

export type DashboardSection = {
  id: string;
  title: string;
  kind: "scorecard" | "metric_grid" | "artifact_link" | "raw_json" | string;
  status: string | null;
  metrics: DashboardMetric[];
  payload: unknown;
  source_artifacts: string[];
  empty_reason: string | null;
};

export type DashboardIdentity = {
  symbol?: string | null;
  instrument?: string | null;
  family?: string | null;
  date_range?: [string, string] | string[] | null;
};

export type DashboardSourceSchema = {
  summary_schema_version: number;
  dashboard_adapter: string;
};

export type DashboardArtifacts = {
  has_diff: boolean;
  has_report: boolean;
  has_vol_surface: boolean;
};

// Extends RunDetail with the stable view-model fields returned by build_run_detail_v1
export type DashboardRunDetail = RunDetail & {
  schema_version: string;
  identity: DashboardIdentity;
  metrics: DashboardMetric[];
  status: {
    data_quality: { status: string | null; worst_dimension: string | null };
    breaks_open: number;
    unattributed: number;
    normalization: string;
  };
  artifacts: DashboardArtifacts;
  sections: DashboardSection[];
  sections_summary: unknown[];
  source_schema: DashboardSourceSchema;
  extensions: Record<string, unknown>;
  raw_artifact_refs: Record<string, string | null>;
};
