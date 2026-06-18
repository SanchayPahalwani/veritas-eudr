"use client";

import { consignmentDds } from "@/lib/data";
import { Field, RiskBadge, Tele } from "@/components/ui";
import { isoDate } from "@/lib/format";

export function DdsCard() {
  const dds = consignmentDds;
  return (
    <div className="border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <Tele>Due Diligence Statement</Tele>
        <span className="stamp text-[0.625rem]">Withheld</span>
      </div>

      <div className="px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[0.8125rem] text-fg">{dds.consignment_id}</span>
          <RiskBadge risk={dds.deforestation_determination} size="sm" />
        </div>
        <p className="mt-1 text-[0.75rem] leading-snug text-fg-dim">{dds.operator_name}</p>

        {/* The honesty banner. */}
        <div className="mt-3 border border-hazard/40 bg-hazard/5 px-3 py-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-hazard">
              compliance_complete
            </span>
            <span className="font-mono text-[0.75rem] font-bold text-hazard">false</span>
          </div>
          <div className="mt-1 flex items-baseline justify-between gap-2">
            <span className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-hazard">
              legality_status
            </span>
            <span className="font-mono text-[0.75rem] font-bold text-hazard">NOT_ASSESSED</span>
          </div>
          <p className="mt-2 font-mono text-[0.625rem] leading-relaxed text-fg-faint">
            Art. 3 is conjunctive (deforestation-free AND legal). Legality is not derivable
            from rasters — so this statement is never a complete conformity finding, by design.
          </p>
        </div>

        <div className="mt-3">
          <Field label="due diligence">{dds.due_diligence_path}</Field>
          <Field label="country risk">{dds.country_risk_class}</Field>
          <Field label="plots">{dds.plot_ids.length}</Field>
          <Field label="reference">{dds.reference_number}</Field>
          <Field label="valid">
            {isoDate(dds.valid_from)} → {isoDate(dds.valid_until)}
          </Field>
          <Field label="cutoff / applies">
            {isoDate(dds.deforestation_cutoff_date)} / {isoDate(dds.regulation_application_date)}
          </Field>
          <Field label="policy">{dds.policy_version}</Field>
        </div>
      </div>
    </div>
  );
}
