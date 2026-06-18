"use client";

import { Reveal } from "@/components/Reveal";
import { CountUp } from "@/components/CountUp";
import { SectionHeading } from "@/components/Section";
import { areaDemo } from "@/lib/data";

export function AreaMathReveal() {
  const { measured_area_ha: geo, area_ha_ease6933: ease, area_ha_webmercator: merc, delta_webmercator_pct: deltaMerc, delta_6933_pct: delta6933, aoi_lat } = areaDemo;
  const mercDelta = deltaMerc ?? 0;

  return (
    <section id="area" className="relative z-10 border-t border-line px-5 py-24 sm:px-8 md:py-32">
      <div className="mx-auto w-full max-w-6xl">
        <SectionHeading
          index="03"
          label="Area · tripwire A"
          title="Web Mercator lies about area."
          lede={
            <>
              The same {geo.toFixed(2)} ha plot, measured three ways. Two agree to eleven decimals.
              The third — the projection most web maps default to — inflates it by{" "}
              <span className="text-hazard">+{mercDelta.toFixed(1)}%</span> at this latitude.
            </>
          }
        />

        <div className="grid gap-px border border-line bg-line md:grid-cols-3">
          <AreaCard
            tag="Authority"
            crs="ST_Area(geography)"
            value={geo}
            note="Geodesic on the WGS84 spheroid. Matches Whisp's GEE basis."
            tone="fg"
          />
          <AreaCard
            tag="Cross-check"
            crs="EPSG:6933 · EASE-Grid 2.0"
            value={ease}
            note={`Equal-area. Δ ${delta6933 >= 0 ? "+" : ""}${delta6933.toExponential(1)}% vs authority — agrees.`}
            tone="low"
          />
          <AreaCard
            tag="Wrong"
            crs="EPSG:3857 · Web Mercator"
            value={merc ?? geo}
            from={geo}
            note="Conformal, not equal-area. Area scales by ~sec²(lat) + an ellipsoid term."
            tone="hazard"
            delta={mercDelta}
          />
        </div>

        <Reveal delay={0.05}>
          <p className="mt-6 max-w-3xl text-pretty text-sm leading-relaxed text-fg-dim md:text-base">
            At the AOI&apos;s {aoi_lat.toFixed(2)}°N this is not a rounding error — it is enough to
            push a plot across the <span className="text-fg">4&nbsp;ha</span> point-vs-polygon
            submission boundary, changing what the farmer is legally required to file. The engine
            treats the geodesic figure as the only authority and keeps the others on screen purely
            as a warning.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

function AreaCard({
  tag,
  crs,
  value,
  from,
  note,
  tone,
  delta,
}: {
  tag: string;
  crs: string;
  value: number;
  from?: number;
  note: string;
  tone: "fg" | "low" | "hazard";
  delta?: number;
}) {
  const color =
    tone === "hazard" ? "text-hazard" : tone === "low" ? "text-risk-low" : "text-fg";
  return (
    <div className="flex flex-col bg-surface p-6 md:p-8">
      <div className="flex items-center justify-between">
        <span className="tele">{tag}</span>
        {delta != null && (
          <span className="border border-hazard/50 bg-hazard/5 px-2 py-0.5 font-mono text-[0.625rem] text-hazard">
            +{delta.toFixed(2)}%
          </span>
        )}
      </div>
      <div className={`mt-6 font-mono text-4xl tabular-nums md:text-5xl ${color}`}>
        <CountUp to={value} from={from ?? 0} decimals={4} duration={1.8} />
      </div>
      <div className="mt-1 font-mono text-sm text-fg-faint">hectares</div>
      <div className="mt-5 border-t border-line pt-4 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-fg-dim">
        {crs}
      </div>
      <p className="mt-2 text-[0.8125rem] leading-snug text-fg-faint">{note}</p>
    </div>
  );
}
