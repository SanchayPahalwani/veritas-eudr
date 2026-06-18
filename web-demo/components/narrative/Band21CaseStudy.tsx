"use client";

import { motion, useInView } from "motion/react";
import { useRef } from "react";
import { CountUp } from "@/components/CountUp";
import { SectionHeading } from "@/components/Section";
import heroJson from "@/public/data/plot_risk/pt-014.json";
import type { PlotRisk } from "@/lib/types";

const hero = heroJson as unknown as PlotRisk;
const axes = hero.risk?.axes ?? [];
const hansen = axes.find((a) => a.layer === "lossyear");
const coverage = hansen?.covered_fraction ?? 1;
const groundHa = hansen?.covered_ha ?? 0.7827;

const EASE = [0.22, 1, 0.36, 1] as const;
const TOTAL_BANDS = 25; // Hansen lossyear bands 1..25 == calendar 2001..2025
const CUTOFF_BAND = 20; // band 20 == 2020; the cutoff is 31 Dec 2020
const ACTIVE_BAND = 21; // pt-014's only post-cutoff loss band == calendar 2021

export function Band21CaseStudy() {
  return (
    <section id="band21" className="relative z-10 border-t border-line px-5 py-24 sm:px-8 md:py-32">
      <div className="mx-auto w-full max-w-6xl">
        <SectionHeading
          index="02"
          label="Band-21 · tripwire B"
          title="It looks exactly like deforestation."
          lede={
            <>
              Plot <span className="font-mono text-fg">pt-014</span> trips every signal a reviewer
              checks for a clearing. The engine still refuses to call it HIGH — and that restraint
              is the whole point.
            </>
          }
        />

        <div className="grid gap-px border border-line bg-line lg:grid-cols-2">
          {/* The case FOR high */}
          <div className="bg-surface p-6 md:p-8">
            <div className="tele mb-5">The case for HIGH</div>
            <ul className="space-y-4">
              <EvidenceCheck
                label="Inside the 2020 forest baseline"
                detail="JRC GFC2020 V3 = 1 · forest_fraction 1.00"
              />
              <EvidenceCheck
                label={
                  <>
                    Post-cutoff loss over{" "}
                    <CountUp to={coverage * 100} decimals={1} suffix="%" className="text-fg" /> of
                    the plot
                  </>
                }
                detail={`Hansen lossyear · ${groundHa.toFixed(4)} ground ha cleared`}
              />
              <EvidenceCheck
                label="No cropland context"
                detail="ESA WorldCover ≠ 40 · not an established farm"
              />
            </ul>
            <p className="mt-6 border-t border-line pt-5 text-sm leading-relaxed text-fg-dim">
              Three independent layers agree. By the textbook, this is a corroborated post-cutoff
              clearing on intact forest. A naive engine flags it{" "}
              <span className="font-mono text-hazard">HIGH</span>.
            </p>
          </div>

          {/* The twist */}
          <div className="bg-surface-2 p-6 md:p-8">
            <div className="tele mb-5 text-band21">But — every loss pixel is in band 21</div>
            <HansenStrip />
            <p className="mt-6 text-sm leading-relaxed text-fg-dim">
              Hansen encodes loss as the <span className="text-fg">year of first detection</span>,
              and band 21 (calendar 2021) is the <span className="text-fg">first</span> annual band
              after the 31 Dec 2020 cutoff. Detection lags clearing: a late-2019 or 2020 cut
              routinely surfaces in 2021. A band-21-only signal sits exactly on the boundary the
              annual resolution cannot resolve.
            </p>
          </div>
        </div>

        {/* The verdict flip */}
        <VerdictFlip />
      </div>
    </section>
  );
}

function EvidenceCheck({
  label,
  detail,
}: {
  label: React.ReactNode;
  detail: string;
}) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center border border-risk-low/60 text-risk-low">
        <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden>
          <path d="M2 6.5 5 9.5 10 3" stroke="currentColor" strokeWidth="1.6" fill="none" />
        </svg>
      </span>
      <div>
        <div className="text-sm text-fg md:text-base">{label}</div>
        <div className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-fg-faint">
          {detail}
        </div>
      </div>
    </li>
  );
}

function HansenStrip() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-15%" });
  return (
    <div ref={ref}>
      <div className="flex items-end gap-[3px]" aria-hidden>
        {Array.from({ length: TOTAL_BANDS }, (_, i) => {
          const band = i + 1;
          const isActive = band === ACTIVE_BAND;
          const isPost = band > CUTOFF_BAND;
          return (
            <motion.div
              key={band}
              className="relative flex-1"
              initial={{ scaleY: 0.25, opacity: 0 }}
              animate={inView ? { scaleY: 1, opacity: 1 } : {}}
              transition={{ duration: 0.4, delay: 0.012 * i, ease: EASE }}
              style={{ originY: 1 }}
            >
              <div
                className={`w-full ${isActive ? "live-dot" : ""}`}
                style={{
                  height: isActive ? 56 : isPost ? 30 : 22,
                  background: isActive
                    ? "var(--color-risk-more)"
                    : isPost
                      ? "color-mix(in srgb, var(--color-risk-more) 22%, transparent)"
                      : "var(--color-line-bright)",
                  boxShadow: isActive ? "0 0 14px var(--color-risk-more)" : "none",
                }}
              />
            </motion.div>
          );
        })}
      </div>
      {/* Axis */}
      <div className="mt-2 flex justify-between font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-fg-faint">
        <span>2001</span>
        <span className="text-band21">↑ cutoff 31 dec 2020</span>
        <span>2025</span>
      </div>
      <div className="mt-3 inline-flex items-center gap-2 border border-risk-more/40 bg-risk-more/5 px-2.5 py-1">
        <span className="size-2 bg-risk-more" style={{ boxShadow: "0 0 8px var(--color-risk-more)" }} />
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-risk-more">
          band 21 · calendar 2021 · the only loss band
        </span>
      </div>
    </div>
  );
}

function VerdictFlip() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-25%" });
  return (
    <div
      ref={ref}
      className="mt-px flex flex-col items-center gap-4 border border-line bg-bg px-6 py-10 text-center sm:flex-row sm:justify-center sm:gap-8"
    >
      <div className="relative">
        <span className="display text-3xl text-fg-faint md:text-5xl">HIGH</span>
        <motion.span
          className="absolute left-0 top-1/2 h-[3px] bg-hazard"
          initial={{ width: 0 }}
          animate={inView ? { width: "100%" } : {}}
          transition={{ duration: 0.5, delay: 0.3, ease: EASE }}
        />
      </div>

      <span className="font-mono text-2xl text-fg-faint">→</span>

      <motion.div
        className="flex flex-col items-center gap-2 sm:flex-row sm:gap-3"
        initial={{ opacity: 0, y: 10 }}
        animate={inView ? { opacity: 1, y: 0 } : {}}
        transition={{ duration: 0.5, delay: 0.7, ease: EASE }}
      >
        <span className="size-3 bg-risk-more" style={{ boxShadow: "0 0 10px var(--color-risk-more)" }} />
        <span className="display text-3xl text-risk-more md:text-5xl">more-info-needed</span>
      </motion.div>
    </div>
  );
}
