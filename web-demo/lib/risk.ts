import type { RiskTier } from "./types";

/**
 * Risk-tier encoding. Kept in sync with the @theme tokens in globals.css and the
 * RiskTier wire values the engine emits. Hex literals are needed for the MapLibre
 * paint expressions (which cannot read CSS variables).
 */
export const RISK_HEX: Record<string, string> = {
  low: "#3fb950",
  high: "#ff3b30",
  "more-info-needed": "#f5a623",
};
export const RISK_NULL_HEX = "#6b7280";
export const BAND21_HEX = "#3b82f6";

export const RISK_LABEL: Record<RiskTier, string> = {
  low: "LOW",
  high: "HIGH",
  "more-info-needed": "MORE INFO NEEDED",
};

/** Worst-case ordering: HIGH > MORE_INFO_NEEDED > LOW. */
export const RISK_ORDER: Record<RiskTier, number> = {
  low: 0,
  "more-info-needed": 1,
  high: 2,
};

export function riskHex(risk: RiskTier | null | undefined): string {
  if (!risk) return RISK_NULL_HEX;
  return RISK_HEX[risk] ?? RISK_NULL_HEX;
}

export function riskLabel(risk: RiskTier | null | undefined): string {
  if (!risk) return "NOT ASSESSED";
  return RISK_LABEL[risk] ?? String(risk).toUpperCase();
}

/** CSS custom-property name for a tier — for styling chrome that follows risk. */
export function riskVar(risk: RiskTier | null | undefined): string {
  switch (risk) {
    case "low":
      return "var(--color-risk-low)";
    case "high":
      return "var(--color-risk-high)";
    case "more-info-needed":
      return "var(--color-risk-more)";
    default:
      return "var(--color-risk-unassessed)";
  }
}
