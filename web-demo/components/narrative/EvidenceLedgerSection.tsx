"use client";

import { Reveal } from "@/components/Reveal";
import { SectionHeading } from "@/components/Section";
import { evidenceLedger } from "@/lib/data";
import { frac } from "@/lib/format";
import { riskVar } from "@/lib/risk";

// A representative slice spanning all three verdicts (low / high / more-info).
const SAMPLE_PLOTS = ["pt-000", "pt-007", "pt-014"];
const SAMPLE = SAMPLE_PLOTS.flatMap((id) =>
  evidenceLedger.evidence.filter((r) => r.plot_id === id),
);

function verdictTone(verdict: string): string {
  if (verdict.startsWith("high")) return riskVar("high");
  if (verdict.startsWith("more-info")) return riskVar("more-info-needed");
  return riskVar("low");
}

export function EvidenceLedgerSection() {
  return (
    <section id="ledger" className="relative z-10 border-t border-line px-5 py-24 sm:px-8 md:py-32">
      <div className="mx-auto w-full max-w-6xl">
        <SectionHeading
          index="04"
          label="Evidence ledger"
          title="Every verdict has a paper trail."
          lede={
            <>
              Each risk tier is written to an <span className="text-fg">append-only</span> ledger,
              one row per dataset sampled. Bump a raster version and the verdict that changes is
              attributable to exactly these rows — the property the replay / mutation test asserts.
            </>
          }
        />

        <Reveal>
          <div className="border border-line bg-surface font-mono text-[0.6875rem] sm:text-[0.75rem]">
            <div className="flex items-center justify-between border-b border-line px-4 py-2 text-fg-faint">
              <span className="uppercase tracking-[0.14em]">append-only ledger · sample</span>
              <span>
                {evidenceLedger.evidence.length} rows · run {evidenceLedger.run_id}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <tbody>
                  {SAMPLE.map((r) => (
                    <tr
                      key={r.id}
                      className="border-b border-line/60 last:border-b-0 [&>td]:whitespace-nowrap [&>td]:px-4 [&>td]:py-1.5"
                    >
                      <td className="text-fg-faint tabular-nums">{String(r.id).padStart(3, "0")}</td>
                      <td className="text-fg">{r.plot_id}</td>
                      <td className="text-fg-dim">{r.dataset_version}</td>
                      <td className="text-fg-dim">{r.rule_id}</td>
                      <td className="text-right tabular-nums text-fg-dim">
                        {r.covered_fraction != null ? frac(r.covered_fraction, 0) : "—"}
                      </td>
                      <td style={{ color: verdictTone(r.verdict) }}>{r.verdict}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-line px-4 py-2 text-fg-faint">
              ⌁ rows never updated or deleted · GET /runs/{evidenceLedger.run_id}/replay
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
