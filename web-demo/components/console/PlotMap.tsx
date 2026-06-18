"use client";

import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";

import { AOI_BOUNDS, AOI_CENTER, buildMapStyle } from "@/lib/mapStyle";
import { PLOTS_GEOJSON_URL, plotsIndex } from "@/lib/data";
import { useConsole } from "./ConsoleContext";

export function PlotMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const coordsRef = useRef<Map<string, [number, number]>>(new Map());
  const prevSelected = useRef<string | null>(null);
  const loadedRef = useRef(false);
  // The map needs WebGL. If it is unavailable (hardened browsers, old GPUs),
  // creation throws — we degrade gracefully instead of crashing the whole page.
  const [failed, setFailed] = useState(false);

  const { selectedId, select, hover } = useConsole();

  // Keep imperative handlers reading the latest setters/state without re-init.
  const selectRef = useRef(select);
  const hoverRef = useRef(hover);
  const selectedIdRef = useRef(selectedId);
  useEffect(() => {
    selectRef.current = select;
    hoverRef.current = hover;
    selectedIdRef.current = selectedId;
  });

  // Apply a selection: feature-state highlight + fly-to.
  function applySelection(id: string | null) {
    const map = mapRef.current;
    if (!map || !loadedRef.current) return;
    const prev = prevSelected.current;
    if (prev && prev !== id) {
      map.setFeatureState({ source: "plots", id: prev }, { selected: false });
    }
    if (id) {
      try {
        map.setFeatureState({ source: "plots", id }, { selected: true });
      } catch {
        /* source not ready yet; the load handler re-applies */
      }
      const coords = coordsRef.current.get(id);
      if (coords) {
        map.flyTo({ center: coords, zoom: 15, speed: 0.7, curve: 1.5, essential: true });
      }
    }
    prevSelected.current = id;
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container,
        style: buildMapStyle(),
        center: AOI_CENTER,
        zoom: 11.4,
        maxBounds: AOI_BOUNDS,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        fadeDuration: 0,
      });
    } catch {
      // WebGL unavailable / context creation failed. Surface the fallback; the
      // inspector, ledger and DDS stay fully usable (plots selectable via ledger).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFailed(true);
      return;
    }
    mapRef.current = map;
    map.on("error", () => {});
    if (process.env.NODE_ENV !== "production") {
      (window as unknown as { __vmap?: maplibregl.Map }).__vmap = map;
    }

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

    // Keep the canvas sized to its (responsive / vh) container.
    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(container);

    // Interaction handlers (the referenced layers live in the initial style).
    let hoverId: string | null = null;
    const clearHover = () => {
      if (hoverId != null) {
        map.setFeatureState({ source: "plots", id: hoverId }, { hover: false });
        hoverId = null;
      }
    };
    map.on("mouseenter", "plots-circles", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mousemove", "plots-circles", (e) => {
      const f = e.features?.[0];
      if (f?.id == null) return;
      const id = String(f.id);
      if (id !== hoverId) {
        clearHover();
        hoverId = id;
        map.setFeatureState({ source: "plots", id }, { hover: true });
        hoverRef.current(id);
      }
    });
    map.on("mouseleave", "plots-circles", () => {
      map.getCanvas().style.cursor = "";
      clearHover();
      hoverRef.current(null);
    });
    map.on("click", "plots-circles", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const id = f.properties?.plot_id ?? f.id;
      if (id != null) selectRef.current(String(id));
    });

    map.on("load", () => {
      loadedRef.current = true;
      // Build the id -> coords index for fly-to choreography.
      fetch(PLOTS_GEOJSON_URL)
        .then((r) => r.json())
        .then(
          (fc: {
            features: Array<{ id: string; geometry: { coordinates: [number, number] } }>;
          }) => {
            for (const feat of fc.features ?? []) {
              if (feat.geometry?.coordinates) {
                coordsRef.current.set(String(feat.id), feat.geometry.coordinates);
              }
            }
            applySelection(selectedIdRef.current);
          },
        )
        .catch(() => applySelection(selectedIdRef.current));
    });

    return () => {
      resizeObserver.disconnect();
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
    };
  }, []);

  useEffect(() => {
    applySelection(selectedId);
  }, [selectedId]);

  return (
    <div className="relative size-full">
      <div ref={containerRef} className="size-full [&_canvas]:outline-none" />
      {failed && <MapFallback />}
    </div>
  );
}

function MapFallback() {
  const c = plotsIndex.counts;
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 bg-bg/96 px-8 text-center">
      <span className="font-mono text-[0.6875rem] uppercase tracking-[0.2em] text-risk-more">
        ⚠ WebGL unavailable
      </span>
      <p className="max-w-sm text-sm leading-relaxed text-fg-dim">
        The interactive AOI map needs WebGL, which this browser has disabled. Every plot is still
        fully inspectable — select one from the{" "}
        <span className="text-fg">evidence ledger below</span>, or enable WebGL to explore the map.
      </p>
      <div className="mt-1 flex items-center gap-4 font-mono text-[0.625rem] uppercase tracking-[0.12em] text-fg-faint">
        <span>{plotsIndex.n_plots} plots</span>
        <span className="text-risk-low">{c.low ?? 0} low</span>
        <span className="text-risk-high">{c.high ?? 0} high</span>
        <span className="text-risk-more">{c["more-info-needed"] ?? 0} more-info</span>
      </div>
    </div>
  );
}
