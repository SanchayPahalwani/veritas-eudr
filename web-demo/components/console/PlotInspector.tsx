"use client";

import { useConsole } from "./ConsoleContext";
import { DispositionBadge, Field, RiskBadge, Tele } from "@/components/ui";
import { frac, ha, pct } from "@/lib/format";
import type { LayerSample } from "@/lib/types";

function AxisRow({ axis }: { axis: LayerSample }) {
  return (
    <div className="border border-line bg-surface px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-[0.75rem] text-fg">{axis.dataset_name}</span>
        <span className="tele shrink-0">{axis.strategy.replace(/_/g, " ")}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[0.6875rem] text-fg-dim">
        <span>
          <span className="text-fg-faint">layer </span>
          {axis.layer}
        </span>
        {axis.value != null && (
          <span>
            <span className="text-fg-faint">value </span>
            {axis.value}
          </span>
        )}
        {axis.covered_fraction != null && (
          <span>
            <span className="text-fg-faint">cover </span>
            {frac(axis.covered_fraction)}
          </span>
        )}
        {axis.covered_ha != null && (
          <span>
            <span className="text-fg-faint">ground </span>
            {ha(axis.covered_ha)}
          </span>
        )}
      </div>
      {axis.note && (
        <p className="mt-1.5 font-mono text-[0.6875rem] leading-relaxed text-fg-faint">
          {axis.note}
        </p>
      )}
    </div>
  );
}

export function PlotInspector() {
  const { detail, loading, selectedId } = useConsole();

  if (!selectedId) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <p className="max-w-[26ch] font-mono text-xs leading-relaxed text-fg-faint">
          ◍ Select any plot on the map to read its validation, area, convergence-of-evidence
          risk, and evidence ledger.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <span className="font-mono text-xs text-fg-faint live-dot">LOADING {selectedId}…</span>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <p className="max-w-[26ch] font-mono text-xs leading-relaxed text-fg-faint">
          No risk payload for {selectedId}. Pick another plot.
        </p>
      </div>
    );
  }

  const { validation, area, risk } = detail;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 z-10 border-b border-line bg-surface-2/95 px-4 py-3 backdrop-blur">
        <div className="flex items-center justify-between">
          <span className="display text-lg tracking-tight text-fg">{detail.plot_id}</span>
          <RiskBadge risk={risk?.risk ?? null} />
        </div>
        {risk?.boundary_uncertain && (
          <div className="mt-2 border-l-2 border-band21 bg-band21/10 px-2.5 py-1.5">
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.1em] text-band21">
              ⚑ Band-21 boundary-uncertain
            </span>
          </div>
        )}
      </div>

      <div className="space-y-5 px-4 py-4">
        {/* VALIDATION */}
        <section>
          <div className="mb-2 flex items-center justify-between">
            <Tele>Validation</Tele>
            <DispositionBadge disposition={validation.disposition} />
          </div>
          <Field label="geometry">{validation.source_geometry_type}</Field>
          <Field label="findings">{validation.findings.length || "none"}</Field>
          {validation.findings.map((f, i) => (
            <p
              key={i}
              className="mt-2 border-l-2 border-line-bright pl-2.5 font-mono text-[0.6875rem] leading-relaxed text-fg-dim"
            >
              <span className="text-fg-faint">{f.rule_id} · </span>
              {f.human_reason}
            </p>
          ))}
          {validation.findings.length === 0 && (
            <p className="mt-1 font-mono text-[0.6875rem] text-fg-faint">
              Passed as-is — no repair, no escalation.
            </p>
          )}
        </section>

        {/* AREA */}
        <section>
          <Tele>Area · 4 ha format</Tele>
          <div className="mt-2">
            {area == null ? (
              <p className="font-mono text-[0.6875rem] text-fg-faint">
                Not measured — location flagged for review.
              </p>
            ) : (
              <>
                <Field label="required format">{area.required_geometry_format}</Field>
                {area.measured_area_ha > 0 ? (
                  <>
                    <Field label="geodesic (authority)">{ha(area.measured_area_ha)}</Field>
                    <Field label="EASE-6933 Δ">{pct(area.delta_6933_pct, 4)}</Field>
                    {area.delta_webmercator_pct != null && (
                      <Field label="web-mercator Δ">{pct(area.delta_webmercator_pct)}</Field>
                    )}
                  </>
                ) : (
                  <p className="mt-1 font-mono text-[0.6875rem] text-fg-faint">
                    Sub-4 ha point submission — a single point is a valid EUDR format; no
                    polygon to measure.
                  </p>
                )}
              </>
            )}
          </div>
        </section>

        {/* RISK */}
        {risk && (
          <section>
            <Tele>Convergence of evidence</Tele>
            <p className="mt-2 text-[0.8125rem] leading-relaxed text-fg-dim">{risk.rationale}</p>
            <div className="mt-3 space-y-1.5">
              {risk.axes.map((axis, i) => (
                <AxisRow key={i} axis={axis} />
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-line pt-2">
              <Tele>Whisp Risk_PCrop</Tele>
              <span className="font-mono text-[0.75rem] text-fg">{risk.whisp_risk_pcrop}</span>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
