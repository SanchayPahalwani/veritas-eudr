"use client";

import { useInView } from "motion/react";
import { useEffect, useRef } from "react";
import { plotsIndex } from "@/lib/data";
import { Bracket } from "@/components/ui";
import { ConsoleProvider, useConsole } from "./ConsoleContext";
import { PlotMap } from "./PlotMap";
import { PlotInspector } from "./PlotInspector";
import { DdsCard } from "./DdsCard";
import { EvidenceLedgerViewer } from "./EvidenceLedgerViewer";
import { RiskLegend } from "./RiskLegend";

function ConsoleInner() {
  const { select } = useConsole();
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.2 });
  const initialized = useRef(false);

  // Deep-link: ?plot=pt-014 selects immediately on mount.
  useEffect(() => {
    if (initialized.current) return;
    const param = new URLSearchParams(window.location.search).get("plot");
    if (param) {
      initialized.current = true;
      select(param);
    }
  }, [select]);

  // Otherwise auto-select the hero (band-21) plot as the console scrolls into
  // view — the narrative → tool handoff (map flies to pt-014 on arrival).
  useEffect(() => {
    if (initialized.current || !inView) return;
    initialized.current = true;
    select(plotsIndex.hero_plot_id);
  }, [inView, select]);

  return (
    <div ref={ref}>
      {/* Header strip */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-5 py-3 sm:px-8">
        <div className="flex items-center gap-4">
          <Bracket>Interactive console</Bracket>
          <span className="inline-flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.1em] text-phosphor">
            <span className="inline-block size-1.5 rounded-full bg-phosphor live-dot" />
            live · static snapshot
          </span>
        </div>
        <div className="flex items-center gap-5">
          <RiskLegend />
          <span className="hidden font-mono text-[0.625rem] text-fg-faint md:inline">
            run {plotsIndex.run_id}
          </span>
        </div>
      </div>

      {/* Map + inspector */}
      <div className="grid lg:grid-cols-[1fr_minmax(360px,400px)]">
        <div className="relative h-[58vh] min-h-[440px] border-line lg:h-[68vh] lg:border-r">
          <PlotMap />
          <div className="pointer-events-none absolute left-3 top-3 z-10 border border-line bg-bg/80 px-3 py-2 font-mono text-[0.625rem] uppercase tracking-[0.12em] backdrop-blur">
            <div className="text-[0.6875rem] text-fg">◎ Đắk Lắk, Vietnam</div>
            <div className="mt-0.5 text-fg-dim">Buôn Ma Thuột · Central Highlands robusta belt</div>
            <div className="mt-0.5 text-fg-faint">
              {plotsIndex.aoi_center[1].toFixed(2)}°N {plotsIndex.aoi_center[0].toFixed(2)}°E
            </div>
          </div>
        </div>
        <div className="h-[58vh] min-h-[440px] border-t border-line bg-surface-2 lg:h-[68vh] lg:border-t-0">
          <PlotInspector />
        </div>
      </div>

      {/* Ledger + DDS */}
      <div className="grid border-t border-line lg:grid-cols-[1fr_minmax(360px,400px)]">
        <div className="h-[280px] border-line lg:border-r">
          <EvidenceLedgerViewer />
        </div>
        <div className="h-[280px] overflow-y-auto bg-surface-2 p-3">
          <DdsCard />
        </div>
      </div>
    </div>
  );
}

export function ConsoleSection() {
  return (
    <section id="console" className="relative z-10 border-t border-line bg-surface/40">
      <ConsoleProvider>
        <ConsoleInner />
      </ConsoleProvider>
    </section>
  );
}
