import type { StyleSpecification, ExpressionSpecification } from "maplibre-gl";
import { BAND21_HEX, RISK_HEX, RISK_NULL_HEX } from "./risk";
import { PLOTS_GEOJSON_URL } from "./data";

/** AOI camera — Vietnam Central Highlands robusta belt. Ported from web/app.js. */
export const AOI_CENTER: [number, number] = [108.03, 12.66];
export const AOI_BOUNDS: [[number, number], [number, number]] = [
  [107.4, 12.0],
  [108.7, 13.3],
];

/** Esri World Imagery — real satellite tiles. For a deforestation engine the
 * imagery IS the point: the actual forest canopy and cleared patches sit under
 * the risk dots. Muted dark via raster paint to fit the forensic aesthetic. */
const SATELLITE_TILES = [
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
];
export const SATELLITE_ATTRIBUTION =
  "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community";

/** Inline lon/lat graticule — a faint "sensor grid" overlay for the tactical read. */
function makeGraticule(): GeoJSONFeatureCollection {
  const features: GraticuleFeature[] = [];
  const [minLon, minLat] = [107.8, 12.35];
  const [maxLon, maxLat] = [108.3, 12.95];
  const step = 0.02;
  for (let lon = minLon; lon <= maxLon + 1e-9; lon += step) {
    features.push(line([[Number(lon.toFixed(3)), minLat], [Number(lon.toFixed(3)), maxLat]]));
  }
  for (let lat = minLat; lat <= maxLat + 1e-9; lat += step) {
    features.push(line([[minLon, Number(lat.toFixed(3))], [maxLon, Number(lat.toFixed(3))]]));
  }
  return { type: "FeatureCollection", features };
}

interface GraticuleFeature {
  type: "Feature";
  properties: Record<string, never>;
  geometry: { type: "LineString"; coordinates: number[][] };
}
interface GeoJSONFeatureCollection {
  type: "FeatureCollection";
  features: GraticuleFeature[];
}
function line(coordinates: number[][]): GraticuleFeature {
  return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates } };
}

/** Data-driven colour by `risk` tier, grey for null/unknown. */
export const riskColorExpr: ExpressionSpecification = [
  "match",
  ["get", "risk"],
  "low",
  RISK_HEX.low,
  "high",
  RISK_HEX.high,
  "more-info-needed",
  RISK_HEX["more-info-needed"],
  RISK_NULL_HEX,
];

export const BAND21_RING_COLOR = BAND21_HEX;

/** The full map style: dark base, muted satellite, faint graticule, plot layers.
 * Everything is in the initial style so rendering never waits on an async source;
 * if the imagery CDN is unreachable the dark base + plots still render. */
export function buildMapStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      satellite: {
        type: "raster",
        tiles: SATELLITE_TILES,
        tileSize: 256,
        maxzoom: 19,
        attribution: SATELLITE_ATTRIBUTION,
      },
      grid: { type: "geojson", data: makeGraticule() as unknown as GeoJSON.GeoJSON },
      // promoteId: MapLibre ignores string top-level GeoJSON ids (it wants numeric
      // and silently falls back to the feature index), which broke click selection
      // and feature-state. Promote plot_id to the feature id so it's the real id
      // everywhere — clicks, hover, and the selected highlight.
      plots: { type: "geojson", data: PLOTS_GEOJSON_URL, promoteId: "plot_id" },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#05060a" } },
      {
        id: "satellite",
        type: "raster",
        source: "satellite",
        paint: {
          // Clearly visible (the imagery IS the point) but slightly muted so the
          // glowing risk dots stay legible and the forensic mood holds.
          "raster-opacity": 0.95,
          "raster-saturation": -0.18,
          "raster-contrast": -0.03,
          "raster-brightness-max": 0.85,
        },
      },
      {
        id: "graticule",
        type: "line",
        source: "grid",
        paint: {
          "line-color": "#7fb2c8",
          "line-width": 1,
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0.06, 15, 0.12],
        },
      },
      // Glow underlay for selected / hovered plots.
      {
        id: "plots-glow",
        type: "circle",
        source: "plots",
        paint: {
          "circle-radius": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            24,
            ["boolean", ["feature-state", "hover"], false],
            17,
            0,
          ],
          "circle-color": riskColorExpr,
          "circle-blur": 1,
          "circle-opacity": 0.55,
        },
      },
      // Band-21 boundary-uncertain ring (tripwire B).
      {
        id: "plots-band21-ring",
        type: "circle",
        source: "plots",
        filter: ["==", ["get", "boundary_uncertain"], true],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 9, 14, 15, 16, 21],
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-color": BAND21_RING_COLOR,
          "circle-stroke-width": 1.8,
          "circle-stroke-opacity": 0.95,
        },
      },
      // Risk-coloured plot dots.
      {
        id: "plots-circles",
        type: "circle",
        source: "plots",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 4.5, 14, 8, 16, 12],
          "circle-color": riskColorExpr,
          "circle-stroke-color": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            "#ffffff",
            "#05070a",
          ],
          "circle-stroke-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            2.5,
            1.25,
          ],
          "circle-opacity": 0.98,
        },
      },
    ],
  };
}
