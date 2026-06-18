/**
 * Typed access to the pre-baked engine snapshots under public/data/.
 * Small, cross-section payloads are imported (inlined at build); per-plot risk
 * is fetched on demand. Nothing here talks to a network at runtime beyond
 * same-origin static files.
 */
import areaDemoJson from "@/public/data/area_demo.json";
import consignmentDdsJson from "@/public/data/consignment_dds.json";
import evidenceLedgerJson from "@/public/data/evidence_ledger.json";
import manifestJson from "@/public/data/manifest.json";
import plotsIndexJson from "@/public/data/plots_index.json";
import validationShowcaseJson from "@/public/data/validation_showcase.json";

import type {
  AreaDemo,
  DueDiligenceStatement,
  EvidenceLedger,
  Manifest,
  PlotRisk,
  PlotsIndex,
  ValidationCase,
} from "./types";

export const plotsIndex = plotsIndexJson as unknown as PlotsIndex;
export const areaDemo = areaDemoJson as unknown as AreaDemo;
export const consignmentDds = consignmentDdsJson as unknown as DueDiligenceStatement;
export const evidenceLedger = evidenceLedgerJson as unknown as EvidenceLedger;
export const manifest = manifestJson as unknown as Manifest;
export const validationCases = (validationShowcaseJson as unknown as { cases: ValidationCase[] })
  .cases;

/** Static asset URLs (served from public/, unaffected by trailingSlash). */
export const PLOTS_GEOJSON_URL = "/data/plots.geojson";
export const BASEMAP_URL = "/basemap/aoi.pmtiles";

const plotRiskCache = new Map<string, PlotRisk>();

/** Fetch one plot's full risk payload (mirrors GET /plots/{id}/risk). */
export async function loadPlotRisk(plotId: string): Promise<PlotRisk> {
  const cached = plotRiskCache.get(plotId);
  if (cached) return cached;
  const res = await fetch(`/data/plot_risk/${plotId}.json`);
  if (!res.ok) throw new Error(`no risk payload for ${plotId}`);
  const data = (await res.json()) as PlotRisk;
  plotRiskCache.set(plotId, data);
  return data;
}
