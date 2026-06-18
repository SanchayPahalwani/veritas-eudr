import { RISK_HEX, RISK_NULL_HEX, BAND21_HEX } from "@/lib/risk";

const ITEMS: Array<{ label: string; color: string }> = [
  { label: "low", color: RISK_HEX.low },
  { label: "high", color: RISK_HEX.high },
  { label: "more-info", color: RISK_HEX["more-info-needed"] },
  { label: "n/a", color: RISK_NULL_HEX },
];

export function RiskLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 font-mono text-[0.625rem] uppercase tracking-[0.1em] text-fg-dim">
      {ITEMS.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block size-2.5"
            style={{ background: it.color, boxShadow: `0 0 6px ${it.color}` }}
          />
          {it.label}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block size-3 rounded-full border-2 bg-transparent"
          style={{ borderColor: BAND21_HEX }}
        />
        band-21 ring
      </span>
    </div>
  );
}
