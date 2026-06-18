import type { ReactNode } from "react";
import type { Disposition, RiskTier } from "@/lib/types";
import { riskLabel, riskVar } from "@/lib/risk";

/** Small uppercase mono telemetry label. */
export function Tele({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <span className={`tele ${className}`}>{children}</span>;
}

/** ASCII-bracketed section marker: [ LABEL ]. */
export function Bracket({ children }: { children: ReactNode }) {
  return (
    <span className="mono text-[0.6875rem] tracking-[0.18em] uppercase text-fg-dim">
      <span className="text-hazard">[</span>
      <span className="px-2">{children}</span>
      <span className="text-hazard">]</span>
    </span>
  );
}

/** A risk-tier badge: emissive dot + uppercase label, colored by tier. */
export function RiskBadge({
  risk,
  size = "md",
}: {
  risk: RiskTier | null;
  size?: "sm" | "md";
}) {
  const color = riskVar(risk);
  return (
    <span
      className={`inline-flex items-center gap-2 font-mono uppercase tracking-[0.12em] ${
        size === "sm" ? "text-[0.6875rem]" : "text-xs"
      }`}
      style={{ color }}
    >
      <span
        className="inline-block size-2.5 shrink-0"
        style={{ background: color, boxShadow: `0 0 8px ${color}` }}
      />
      {riskLabel(risk)}
    </span>
  );
}

const DISPOSITION_VAR: Record<Disposition, string> = {
  AUTO_VALID: "var(--color-risk-low)",
  AUTO_FIXED: "var(--color-risk-more)",
  NEEDS_REVIEW: "var(--color-hazard)",
};

/** A validation-disposition pill. Square corners, hairline border, tier color. */
export function DispositionBadge({ disposition }: { disposition: Disposition }) {
  const color = DISPOSITION_VAR[disposition];
  return (
    <span
      className="inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.14em]"
      style={{ color, borderColor: color, background: `color-mix(in srgb, ${color} 9%, transparent)` }}
    >
      {disposition.replace("_", " ")}
    </span>
  );
}

/** A label/value field row in the inspector (mono, hairline separated). */
export function Field({
  label,
  children,
  mono = true,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-1.5 last:border-b-0">
      <span className="tele shrink-0">{label}</span>
      <span
        className={`text-right text-[0.8125rem] text-fg ${mono ? "font-mono tabular-nums" : ""}`}
      >
        {children}
      </span>
    </div>
  );
}

/** A faint crosshair for grid intersections. */
export function Crosshair({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`pointer-events-none text-line-bright ${className}`}
      width="11"
      height="11"
      viewBox="0 0 11 11"
      aria-hidden
    >
      <path d="M5.5 0V11M0 5.5H11" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}
