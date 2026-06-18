"use client";

import { motion, useScroll, useTransform } from "motion/react";
import { useRef } from "react";
import { Reveal } from "@/components/Reveal";
import { SectionHeading } from "@/components/Section";
import { DispositionBadge } from "@/components/ui";
import { validationCases } from "@/lib/data";
import type { Disposition } from "@/lib/types";

interface Stage {
  no: string;
  name: string;
  desc: string;
}

const STAGES: Stage[] = [
  { no: "01", name: "ingest", desc: "Canonicalize to EPSG:4326 [lon, lat]; SHA-256 geometry hash; idempotent on re-submission." },
  { no: "02", name: "validate", desc: "Typed findings roll up to one disposition. The judgment is what NOT to auto-fix." },
  { no: "03", name: "area", desc: "Geodesic ST_Area(geography) is the authority; EPSG:6933 cross-checks it; Web Mercator exists only to prove it wrong." },
  { no: "04", name: "deforestation", desc: "exactextract fractional coverage over Hansen · JRC · WorldCover. Convergence of evidence — never a single layer." },
  { no: "05", name: "risk", desc: "low · high · more-info-needed. The tri-state mirrors Whisp's Risk_PCrop one-to-one." },
  { no: "06", name: "DDS", desc: "TRACES-shaped statement. Legality NOT_ASSESSED; Art. 3 is conjunctive, so it is never fully compliant." },
];

const DISPOSITION_ORDER: Disposition[] = ["AUTO_VALID", "AUTO_FIXED", "NEEDS_REVIEW"];

export function PipelineSection() {
  const railRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: railRef,
    offset: ["start 75%", "end 60%"],
  });
  const fillScale = useTransform(scrollYProgress, [0, 1], [0, 1]);

  return (
    <section id="pipeline" className="relative z-10 border-t border-line px-5 py-24 sm:px-8 md:py-32">
      <div className="mx-auto w-full max-w-6xl">
        <SectionHeading
          index="01"
          label="Pipeline"
          title="Six stages. No shortcuts."
          lede="A messy customer submission becomes a per-plot risk verdict and a consignment statement. Every stage is a frozen module with one job — and an opinion about what it refuses to guess."
        />

        <div ref={railRef} className="relative mt-4 pl-10 sm:pl-14">
          {/* Rail track + scrubbed fill */}
          <div className="absolute left-[7px] top-2 bottom-2 w-px bg-line sm:left-[11px]" />
          <motion.div
            className="absolute left-[7px] top-2 w-px origin-top bg-hazard sm:left-[11px]"
            style={{ bottom: 8, scaleY: fillScale }}
          />

          <ol className="space-y-12 md:space-y-16">
            {STAGES.map((stage) => (
              <li key={stage.no} className="relative">
                {/* Node */}
                <span className="absolute -left-10 top-1 flex size-4 items-center justify-center sm:-left-14 sm:size-6">
                  <span className="size-2 bg-fg-faint sm:size-2.5" />
                </span>
                <Reveal>
                  <div className="flex flex-col gap-2 md:flex-row md:items-baseline md:gap-6">
                    <div className="flex shrink-0 items-baseline gap-3">
                      <span className="font-mono text-xs text-hazard">{stage.no}</span>
                      <h3 className="display text-2xl text-fg md:text-3xl">{stage.name}</h3>
                    </div>
                    <p className="max-w-xl text-pretty text-sm leading-relaxed text-fg-dim md:text-base">
                      {stage.desc}
                    </p>
                  </div>

                  {stage.name === "validate" && <ValidationShowcase />}
                </Reveal>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function ValidationShowcase() {
  return (
    <div className="mt-5 grid gap-px border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
      {DISPOSITION_ORDER.flatMap((disp) =>
        validationCases
          .filter((c) => c.disposition === disp)
          .map((c) => (
            <div key={c.scenario} className="flex flex-col gap-2 bg-surface px-3.5 py-3">
              <DispositionBadge disposition={c.disposition} />
              <div className="font-mono text-[0.8125rem] text-fg">{c.title}</div>
              <p className="text-[0.75rem] leading-snug text-fg-faint">{c.blurb}</p>
            </div>
          )),
      )}
    </div>
  );
}
