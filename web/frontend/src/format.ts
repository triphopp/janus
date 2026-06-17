export function isNum(value: unknown): value is number {
  if (typeof value === "number") return Number.isFinite(value);
  if (value === "" || value == null || Array.isArray(value)) return false;
  return !Number.isNaN(Number(value));
}

export function fmtNum(value: unknown): string {
  if (!isNum(value)) return "-";
  const n = Number(value);
  const a = Math.abs(n);
  if (a === 0) return "0";
  if (a < 0.0001) return n.toExponential(2);
  const max = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 6;
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: max }).format(n);
}

export function pct(value: unknown): string {
  return value == null || !isNum(value) ? "-" : `${(Number(value) * 100).toFixed(3)}%`;
}

export function t19(value?: string | null): string {
  return String(value || "").replace("T", " ").slice(0, 19);
}

export function h8(value?: string | null): string {
  return value ? String(value).slice(0, 8) : "-";
}

export function compactCounts(value?: Record<string, number>): string {
  const entries = Object.entries(value || {}).filter(([key, count]) => key && count);
  return entries.length ? entries.map(([key, count]) => `${key} ${count}`).join(" | ") : "-";
}

export function valueText(value: unknown): string {
  if (value == null || value === "") return "-";
  return isNum(value) ? fmtNum(value) : String(value);
}
