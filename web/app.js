/* Veritas EUDR read-only plot map.
 *
 * A deliberately small, self-contained MapLibre map:
 *   - basemap: a self-hosted PMTiles vector tileset of the AOI (roads + water),
 *     served from web/basemap/aoi.pmtiles via the vendored pmtiles protocol. No
 *     live tile server, no CDN at runtime.
 *   - plots:   web/plots.geojson (produced by scripts/export_web_plots.py),
 *     drawn as circles coloured by the convergence-of-evidence `risk` tier.
 *   - on click: a popup with the plot's validation disposition, risk tier, the
 *     band-21 / boundary_uncertain note when set, and the evidence count.
 *   - on load: the default click-through opens the popup for a band-21
 *     (boundary_uncertain) plot and flies to it -- the tripwire-B story.
 *
 * Everything (JS, CSS, basemap, data) is vendored/committed, so the page renders
 * standalone over `python -m http.server` with no network access.
 */

(function () {
  "use strict";

  // AOI centre (Vietnam Central Highlands robusta belt) -- the synthetic
  // coffee/cocoa points cluster here.
  var AOI_CENTER = [108.03, 12.66];

  // Risk colour ramp. Kept in sync with the legend in index.html and with the
  // RiskTier wire values emitted by the exporter.
  var RISK_COLORS = {
    low: "#2e7d32", // green
    high: "#c62828", // red
    "more-info-needed": "#f9a825", // amber
  };
  var RISK_NULL_COLOR = "#9e9e9e"; // grey: not risk-assessed (risk == null)

  var BASEMAP_PMTILES = "basemap/aoi.pmtiles";
  var PLOTS_GEOJSON = "plots.geojson";

  // The OpenStreetMap attribution the self-hosted basemap is derived from.
  var OSM_ATTRIBUTION = "&copy; OpenStreetMap contributors";

  // Register the vendored pmtiles protocol so MapLibre can read pmtiles:// URLs.
  var protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  // A fully inline style (no remote style JSON). A light land background, then
  // water and roads drawn from the single `osm` source-layer of the PMTiles.
  var style = {
    version: 8,
    // glyphs intentionally omitted: this basemap carries no label layers, so no
    // font fetching happens at runtime (keeps the zero-network promise honest).
    sources: {
      basemap: {
        type: "vector",
        url: "pmtiles://" + BASEMAP_PMTILES,
        attribution: OSM_ATTRIBUTION,
      },
    },
    layers: [
      {
        id: "land",
        type: "background",
        paint: { "background-color": "#f4f2ec" },
      },
      {
        id: "water",
        type: "line",
        source: "basemap",
        "source-layer": "osm",
        filter: ["==", ["get", "layer"], "water"],
        paint: {
          "line-color": "#9ec7e8",
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.6, 13, 2.5],
        },
      },
      {
        id: "roads",
        type: "line",
        source: "basemap",
        "source-layer": "osm",
        filter: ["==", ["get", "layer"], "road"],
        paint: {
          "line-color": "#d8cdb8",
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            7,
            [
              "match",
              ["get", "kind"],
              ["motorway", "trunk", "primary"],
              1.2,
              0.4,
            ],
            14,
            [
              "match",
              ["get", "kind"],
              ["motorway", "trunk", "primary"],
              4,
              1.2,
            ],
          ],
        },
      },
    ],
  };

  var map = new maplibregl.Map({
    container: "map",
    style: style,
    center: AOI_CENTER,
    zoom: 12.5,
    maxBounds: [
      [106.8, 11.6],
      [109.3, 14.8],
    ],
    attributionControl: false, // added explicitly below so OSM credit is visible
  });

  map.addControl(
    new maplibregl.NavigationControl({ showCompass: false }),
    "top-right",
  );
  map.addControl(
    new maplibregl.AttributionControl({
      compact: false,
      customAttribution: OSM_ATTRIBUTION,
    }),
    "bottom-right",
  );

  // A data-driven colour expression mapping the `risk` property to a colour,
  // with grey for a null/absent risk (an unsampleable plot).
  var riskColorExpr = [
    "match",
    ["get", "risk"],
    "low",
    RISK_COLORS.low,
    "high",
    RISK_COLORS.high,
    "more-info-needed",
    RISK_COLORS["more-info-needed"],
    RISK_NULL_COLOR, // fallback: null / unknown
  ];

  map.on("load", function () {
    map.addSource("plots", { type: "geojson", data: PLOTS_GEOJSON });

    // Base circle, coloured by risk tier.
    map.addLayer({
      id: "plots-circles",
      type: "circle",
      source: "plots",
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          10,
          4,
          14,
          8,
          16,
          12,
        ],
        "circle-color": riskColorExpr,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
        "circle-opacity": 0.9,
      },
    });

    // A highlight ring for band-21 boundary-uncertain plots (the tripwire-B
    // story). Drawn on top so it is visible regardless of risk colour.
    map.addLayer({
      id: "plots-band21-ring",
      type: "circle",
      source: "plots",
      filter: ["==", ["get", "boundary_uncertain"], true],
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          10,
          7,
          14,
          12,
          16,
          17,
        ],
        "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-color": "#1565c0",
        "circle-stroke-width": 2.5,
      },
    });

    // Pointer affordance on hover.
    map.on("mouseenter", "plots-circles", function () {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "plots-circles", function () {
      map.getCanvas().style.cursor = "";
    });

    // Click -> popup with disposition / risk / band-21 note / evidence count.
    map.on("click", "plots-circles", function (e) {
      if (!e.features || !e.features.length) return;
      var f = e.features[0];
      openPlotPopup(f.geometry.coordinates.slice(), f.properties);
    });

    // Default click-through: open the popup for a band-21 plot and fly to it.
    openDefaultBand21Plot();
  });

  // Escape user-facing strings before injecting into popup HTML.
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  function riskLabel(risk) {
    return risk == null || risk === "" ? "not assessed" : risk;
  }

  function popupHtml(props) {
    var band21 =
      props.boundary_uncertain === true || props.boundary_uncertain === "true";
    var html =
      '<div class="plot-popup">' +
      "<h2>Plot " +
      esc(props.plot_id) +
      "</h2>" +
      "<dl>" +
      "<dt>Risk</dt><dd>" +
      esc(riskLabel(props.risk)) +
      "</dd>" +
      "<dt>Validation disposition</dt><dd>" +
      esc(props.disposition) +
      "</dd>" +
      "<dt>Evidence records</dt><dd>" +
      esc(props.n_evidence) +
      "</dd>" +
      "<dt>Rationale</dt><dd>" +
      esc(props.rationale) +
      "</dd>" +
      "</dl>";
    if (band21) {
      html +=
        '<div class="band21"><strong>Band-21 boundary-uncertain.</strong> ' +
        "Post-cutoff Hansen loss is only in band 21 (calendar 2021), the first " +
        "post-cutoff annual band. Year-of-first-detection latency means a " +
        "2019&ndash;2020 clearing can surface in 2021, so this is downgraded from " +
        "a would-be HIGH to more-info-needed.</div>";
    }
    return html + "</div>";
  }

  function openPlotPopup(coordinates, props) {
    new maplibregl.Popup({ maxWidth: "320px" })
      .setLngLat(coordinates)
      .setHTML(popupHtml(props))
      .addTo(map);
  }

  // Fetch the plots data directly to find the default band-21 plot. We re-fetch
  // (rather than query rendered features) so the default works deterministically
  // even before the source has finished tiling on first paint.
  function openDefaultBand21Plot() {
    fetch(PLOTS_GEOJSON)
      .then(function (r) {
        return r.json();
      })
      .then(function (fc) {
        var feats = (fc && fc.features) || [];
        // Deterministic pick: the first feature (source order) flagged
        // boundary_uncertain. The exporter preserves submission order, so this
        // is stable across runs.
        var target = null;
        for (var i = 0; i < feats.length; i++) {
          if (
            feats[i].properties &&
            feats[i].properties.boundary_uncertain === true
          ) {
            target = feats[i];
            break;
          }
        }
        if (!target) return;
        var coords = target.geometry.coordinates.slice();
        map.flyTo({ center: coords, zoom: 15, speed: 0.8 });
        // Open the popup once the camera has settled, so it anchors correctly.
        map.once("moveend", function () {
          openPlotPopup(coords, target.properties);
        });
      })
      .catch(function () {
        /* If the fetch fails the map still renders; the default popup is best-effort. */
      });
  }
})();
