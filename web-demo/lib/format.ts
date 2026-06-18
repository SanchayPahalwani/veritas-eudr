/** Presentation formatting. The engine's values are authoritative; we only round
 * for display. Monospace tabular figures are assumed at the call site. */

export function ha(value: number | null | undefined, digits = 4): string {
  if (value == null) return "—";
  return `${value.toFixed(digits)} ha`;
}

export function pct(value: number | null | undefined, digits = 2): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

/** A 0–1 coverage fraction as a percentage. */
export function frac(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function coord(lon: number, lat: number, digits = 6): string {
  return `${lon.toFixed(digits)}, ${lat.toFixed(digits)}`;
}

export function int(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toLocaleString("en-US");
}

/** ISO date/datetime → short UTC date. */
export function isoDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function isoStamp(value: string | null | undefined): string {
  if (!value) return "—";
  // "2026-06-18T00:00:00+00:00" -> "2026-06-18 00:00:00Z"
  return value.slice(0, 19).replace("T", " ") + "Z";
}
