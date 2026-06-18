import { Reveal } from "@/components/Reveal";
import { plotsIndex, consignmentDds } from "@/lib/data";
import { isoDate } from "@/lib/format";

export function StakesSection() {
  const c = plotsIndex.counts;
  return (
    <section
      id="stakes"
      className="relative z-10 flex min-h-dvh flex-col justify-between px-5 pb-12 pt-7 sm:px-8"
    >
      {/* Masthead */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4 font-mono text-[0.6875rem] uppercase tracking-[0.16em] text-fg-dim">
        <div className="flex items-center gap-3">
          <span className="text-fg">VERITAS</span>
          <span className="text-hazard">·</span>
          <span>EUDR</span>
        </div>
        <div className="flex items-center gap-5">
          <span className="hidden sm:inline">Reg (EU) 2023/1115</span>
          <span className="hidden md:inline">rev {plotsIndex.run_id.slice(0, 7)}</span>
          <span className="inline-flex items-center gap-2 text-phosphor">
            <span className="inline-block size-1.5 rounded-full bg-phosphor live-dot" />
            static snapshot
          </span>
        </div>
      </header>

      {/* Hero statement */}
      <div className="flex flex-1 flex-col justify-center py-16">
        <Reveal eager className="font-mono text-[0.75rem] uppercase tracking-[0.2em] text-fg-dim">
          EU Deforestation Regulation · plot-level due-diligence engine
        </Reveal>

        <Reveal eager delay={0.06}>
          <h1 className="display mt-6 text-[clamp(2.75rem,9vw,8.5rem)] text-fg">
            One wrong
            <br />
            hectare.
          </h1>
        </Reveal>

        <Reveal eager delay={0.14}>
          <p className="mt-8 max-w-3xl text-pretty text-lg leading-relaxed text-fg-dim md:text-2xl md:leading-relaxed">
            A single miscalculated area — or one blindly reprojected coordinate — can wrongly{" "}
            <span className="text-fg underline decoration-risk-more decoration-2 underline-offset-4">
              block a smallholder coffee farmer
            </span>{" "}
            from the EU market, or wrongly{" "}
            <span className="text-fg underline decoration-hazard decoration-2 underline-offset-4">
              clear real deforestation
            </span>
            . The regulation <span className="text-fg">is</span> the definition of correctness.
          </p>
        </Reveal>
      </div>

      {/* Stat ledger + scroll cue */}
      <Reveal eager delay={0.1}>
        <div className="grid grid-cols-2 gap-px border border-line bg-line md:grid-cols-4">
          <Stat k="AOI plots" v={String(plotsIndex.n_plots)} sub="Vietnam robusta belt" />
          <Stat
            k="Risk split"
            v={`${c.low ?? 0}·${c["more-info-needed"] ?? 0}·${c.high ?? 0}`}
            sub="low · more-info · high"
          />
          <Stat
            k="Deforestation cutoff"
            v={isoDate(consignmentDds.deforestation_cutoff_date)}
            sub="≠ application date"
          />
          <Stat k="Fully-compliant DDS" v="0" sub="withheld by design" accent />
        </div>
      </Reveal>

      <div className="mt-8 flex items-center gap-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-fg-faint">
        <span className="inline-block h-8 w-px animate-pulse bg-line-bright" />
        Scroll to trace one plot through the engine
      </div>
    </section>
  );
}

function Stat({
  k,
  v,
  sub,
  accent = false,
}: {
  k: string;
  v: string;
  sub: string;
  accent?: boolean;
}) {
  return (
    <div className="bg-bg px-4 py-5">
      <div className="tele">{k}</div>
      <div
        className={`mt-2 font-mono text-2xl tabular-nums md:text-3xl ${
          accent ? "text-hazard" : "text-fg"
        }`}
      >
        {v}
      </div>
      <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.1em] text-fg-faint">
        {sub}
      </div>
    </div>
  );
}
