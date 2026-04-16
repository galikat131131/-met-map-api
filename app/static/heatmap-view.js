// Heat-map overlay: visualize aggregated transitions from /heatmap/*.
// Self-contained: owns its own fetches, legend, and toggle button.
// Exposes a small API consumed by app.js:
//   init(map, activeFloor) — wire the toggle, store the map reference.
//   onFloorChange(floor)    — re-render polygons for the new floor.
//   styleOverride(feature)  — returns a Google Maps style object (or null).
//   isActive()              — boolean.
(function () {
  let mapRef = null;
  let active = false;
  let activeFloor = "2";
  let galleryVisits = new Map();  // gallery_number → visits
  let maxVisits = 0;
  let galleryByNumber = new Map();
  let fetchedAt = 0;
  const FETCH_TTL_MS = 60_000;

  async function ensureGalleries() {
    if (galleryByNumber.size) return;
    try {
      const gs = await fetch("/galleries").then(r => r.json());
      for (const g of gs) galleryByNumber.set(g.number, g);
    } catch (err) {
      console.warn("heatmap: couldn't load galleries", err);
    }
  }

  async function fetchHeatData(force = false) {
    const fresh = Date.now() - fetchedAt < FETCH_TTL_MS;
    if (fresh && !force && galleryVisits.size) return;
    try {
      const g = await fetch("/heatmap/galleries").then(r => r.ok ? r.json() : []);
      galleryVisits = new Map();
      for (const row of (Array.isArray(g) ? g : [])) {
        galleryVisits.set(row.gallery, row.visits);
      }
      maxVisits = 0;
      for (const v of galleryVisits.values()) if (v > maxVisits) maxVisits = v;
      fetchedAt = Date.now();
    } catch (err) {
      console.warn("heatmap: fetch failed", err);
    }
  }

  function heatColor(p) {
    // p in [0,1]. Hue 220 (blue) at p=0 through green/yellow to 0 (red) at p=1.
    const h = 220 * (1 - p);
    const s = 72;
    const l = 56 - 14 * p;
    return `hsl(${h.toFixed(0)}, ${s}%, ${l}%)`;
  }

  function polygonPercentile(num) {
    const v = galleryVisits.get(num) || 0;
    if (maxVisits === 0) return 0;
    return Math.min(1, v / maxVisits);
  }

  function styleOverride(feature) {
    if (!active) return null;
    const num = feature.getProperty("number");
    const v = galleryVisits.get(num) || 0;
    if (v === 0) {
      return {
        fillColor: "#cfcfcf",
        fillOpacity: 0.15,
        strokeColor: "#9e9e9e",
        strokeWeight: 1,
        clickable: true,
      };
    }
    const p = polygonPercentile(num);
    return {
      fillColor: heatColor(p),
      fillOpacity: 0.25 + 0.45 * p,
      strokeColor: heatColor(p),
      strokeWeight: 2,
      clickable: true,
    };
  }

  function renderLegend() {
    const el = document.getElementById("heatmap-legend");
    if (!el) return;
    el.hidden = !active;
    if (!active) return;
    const count = Array.from(galleryVisits.values()).reduce((a, b) => a + b, 0);
    const countEl = el.querySelector(".heatmap-legend-count");
    if (countEl) countEl.textContent = `${count.toLocaleString()} transitions`;
  }

  function requestPolygonRestyle() {
    // app.js owns the Data layer style function; ask it to reapply so our
    // styleOverride is consulted. refreshPolygonStyle is exposed on window.
    if (typeof window.refreshPolygonStyle === "function") {
      window.refreshPolygonStyle();
    }
  }

  async function activate() {
    active = true;
    document.getElementById("heatmap-toggle").classList.add("active");
    document.getElementById("heatmap-toggle").setAttribute("aria-pressed", "true");
    await ensureGalleries();
    await fetchHeatData();
    requestPolygonRestyle();
    renderLegend();
  }

  function deactivate() {
    active = false;
    document.getElementById("heatmap-toggle").classList.remove("active");
    document.getElementById("heatmap-toggle").setAttribute("aria-pressed", "false");
    requestPolygonRestyle();
    renderLegend();
  }

  function toggle() {
    if (active) deactivate();
    else activate();
  }

  function onFloorChange(floor) {
    activeFloor = floor;
    if (!active) return;
    requestPolygonRestyle();
  }

  function init(map, initialFloor) {
    mapRef = map;
    if (initialFloor) activeFloor = initialFloor;
    const btn = document.getElementById("heatmap-toggle");
    if (btn) btn.addEventListener("click", toggle);
  }

  window.__heatmapView = {
    init,
    onFloorChange,
    styleOverride,
    isActive: () => active,
  };
})();
