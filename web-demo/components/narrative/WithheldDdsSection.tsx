"use client";

import { Reveal } from "@/components/Reveal";
import { SectionHeading } from "@/components/Section";
import { Field, RiskBadge } from "@/components/ui";
import { consignmentDds } from "@/lib/data";
import { isoDate } from "@/lib/format";

export function WithheldDdsSection() {
  const dds = consignmentDds;
  return (
    <section id="dds" className="relative z-10 border-t border-line px-5 py-24 sm:px-8 md:py-32">
      <div className="mx-auto w-full max-w-6xl">
        <SectionHeading
          index="05"
          label="Due Diligence Statement"
          title="A statement that refuses to overclaim."
          lede={
            <>
              It would be trivial to print “compliant”. This engine prints the truth instead: it
              assesses one axis — deforestation — and says so loudly. Legality it cannot see, so it
              never pretends to.
            </>
          }
        />

        <div className="grid gap-px border border-line bg-line lg:grid-cols-[1.1fr_1fr]">
          {/* The document */}
          <div className="relative overflow-hidden bg-surface p-6 md:p-8">
            <div className="pointer-events-none absolute -right-6 top-8 rotate-[14deg] opacity-90">
              <span className="stamp text-xl md:text-2xl">Withheld</span>
            </div>

            <div className="tele">TRACES-shaped DDS</div>
            <div className="mt-3 flex items-center gap-3">
              <span className="display text-2xl text-fg md:text-3xl">{dds.consignment_id}</span>
              <RiskBadge risk={dds.deforestation_determination} size="sm" />
            </div>
            <p className="mt-1 text-sm text-fg-dim">{dds.operator_name}</p>

            <div className="mt-6 max-w-md">
              <Field label="commodity">{dds.commodity}</Field>
              <Field label="plots in consignment">{dds.plot_ids.length}</Field>
              <Field label="due-diligence path">{dds.due_diligence_path}</Field>
              <Field label="country risk class">{dds.country_risk_class}</Field>
              <Field label="reference">{dds.reference_number}</Field>
              <Field label="valid">
                {isoDate(dds.valid_from)} → {isoDate(dds.valid_until)}
              </Field>
              <Field label="policy version">{dds.policy_version}</Field>
            </div>
          </div>

          {/* The honesty */}
          <div className="flex flex-col justify-center gap-4 bg-surface-2 p-6 md:p-8">
            <BigFlag k="compliance_complete" v="false" />
            <BigFlag k="legality_status" v="NOT_ASSESSED" />
            <p className="mt-2 text-sm leading-relaxed text-fg-dim">
              EUDR Art. 3 conformity is <span className="text-fg">conjunctive</span>:
              deforestation-free <span className="text-fg">and</span> legal. Legality (Art. 2&apos;s
              eight documentary categories) is not derivable from public satellite rasters — so a
              complete conformity finding is unreachable, and the statement says so rather than
              fabricating one.
            </p>
            <p className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-fg-faint">
              cutoff {isoDate(dds.deforestation_cutoff_date)} · applies{" "}
              {isoDate(dds.regulation_application_date)} · two distinct dates, never conflated
            </p>
          </div>
        </div>

        <Reveal delay={0.05}>
          <p className="mt-8 max-w-3xl text-pretty text-lg leading-relaxed text-fg md:text-xl">
            Faking a legality finding would be worse than admitting the boundary of what the data
            can prove.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

function BigFlag({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between border border-hazard/40 bg-hazard/5 px-4 py-3">
      <span className="font-mono text-[0.75rem] uppercase tracking-[0.1em] text-hazard sm:text-sm">
        {k}
      </span>
      <span className="font-mono text-base font-bold text-hazard sm:text-lg">{v}</span>
    </div>
  );
}
