const CENTER = { lat: 40.779448, lng: -73.963517 };
const DEFAULT_ZOOM = 19;
const AUTO_CORRECT_THRESHOLD_M = 30;

let map, infoWindow;
let ALL_GALLERIES = [];
let activeFloor = "2";
let centroidMarkers = [];

let userMarker = null;
let accuracyCircle = null;
let resolvedGalleryNumber = null;
let awaitingCorrection = false;

let routeState = null;
let routePolyline = null;
let pickingMode = null;
let pendingRouteTarget = null;
// Curated tours
let ALL_TOURS = [];
let activeTour = null;
let tourPolylines = [];
let tourMarkers = [];

window.initMap = async function () {
  map = new google.maps.Map(document.getElementById("map"), {
    center: CENTER,
    zoom: DEFAULT_ZOOM,
    mapTypeId: "roadmap",
    disableDefaultUI: true,
    zoomControl: true,
    clickableIcons: false,
    gestureHandling: "greedy",
  });

  infoWindow = new google.maps.InfoWindow();

  map.data.setStyle(polygonStyle);

  map.data.addListener("click", onPolygonClick);

  document.querySelectorAll("#floor-picker button").forEach((btn) => {
    btn.addEventListener("click", () => setFloor(btn.dataset.floor));
  });

  document.getElementById("locate-btn").addEventListener("click", locate);
  document.getElementById("correction-dismiss").addEventListener("click", () => {
    awaitingCorrection = false;
    hideCorrectionPrompt();
  });

  document.getElementById("route-close").addEventListener("click", closeRouteSheet);
  document.querySelectorAll(".route-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".route-chip").forEach((c) => c.classList.remove("picking"));
      chip.classList.add("picking");
      pickingMode = chip.dataset.role === "from" ? "route-from" : "route-to";
      setStatus(`Tap a gallery to set ${chip.dataset.role}`);
    });
  });
  document.getElementById("tours-btn").addEventListener("click", openTourDrawer);
  document.getElementById("tour-drawer-close").addEventListener("click", closeTourDrawer);
  document.getElementById("tour-backdrop").addEventListener("click", closeTourDrawer);
  document.getElementById("tour-bar-close").addEventListener("click", clearActiveTour);
  document.getElementById("tour-sheet-collapse").addEventListener("click", () => {
    document.getElementById("tour-sheet").classList.toggle("collapsed");
  });

  setStatus("Loading galleries…");
  try {
    const res = await fetch("/galleries");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    ALL_GALLERIES = await res.json();
  } catch (err) {
    console.error("Failed to load galleries", err);
    setStatus("Couldn't load galleries. Check your connection.", 5000);
    return;
  }

  renderFloor(activeFloor);
  setStatus("");

  loadTours();
};

function polygonStyle(feature) {
  const num = feature.getProperty("number");
  const isResolved = num === resolvedGalleryNumber;
  const isRouteTo = routeState && routeState.to.number === num;
  const isRouteFrom = routeState && routeState.from.number === num && !isResolved;
  const inTour = !!(activeTour && activeTour.stops.some((s) => s.gallery === num));
  if (isRouteTo) {
    return { fillColor: "#ea4335", fillOpacity: 0.45, strokeColor: "#ea4335", strokeWeight: 2, clickable: true };
  }
  if (isResolved || isRouteFrom) {
    return { fillColor: "#0f9d58", fillOpacity: 0.45, strokeColor: "#0f9d58", strokeWeight: 2, clickable: true };
  }
  if (inTour) {
    return { fillColor: "#b8321c", fillOpacity: 0.32, strokeColor: "#b8321c", strokeWeight: 2, clickable: true };
  }
  return { fillColor: "#1a73e8", fillOpacity: 0.25, strokeColor: "#1a73e8", strokeWeight: 2, clickable: true };
}

function refreshPolygonStyle() {
  map.data.setStyle(polygonStyle);
}

function onPolygonClick(e) {
  const num = e.feature.getProperty("number");
  const gallery = ALL_GALLERIES.find((x) => x.number === num);
  if (!gallery) return;

  if (awaitingCorrection) {
    setResolvedGallery(gallery, "user-tap");
    if (gallery.floor !== activeFloor) setFloor(gallery.floor);
    awaitingCorrection = false;
    hideCorrectionPrompt();
    infoWindow.close();
    setStatus(`Set to Gallery ${gallery.number}: ${gallery.name}`, 2000);
    return;
  }

  if (pickingMode === "route-from" && routeState) {
    clearPickingUI();
    computeRoute(gallery, routeState.to);
    return;
  }
  if (pickingMode === "route-to" && routeState) {
    clearPickingUI();
    computeRoute(routeState.from, gallery);
    return;
  }
  if (pickingMode === "route-new-from" && pendingRouteTarget) {
    const target = pendingRouteTarget;
    pendingRouteTarget = null;
    clearPickingUI();
    setResolvedGallery(gallery, "user-tap");
    computeRoute(gallery, target);
    return;
  }

  const name = e.feature.getProperty("name");
  const summary = e.feature.getProperty("summary");
  const img = e.feature.getProperty("image_url");
  const imgHtml = img ? `<img src="${img}" alt="">` : "";
  const isHere = resolvedGalleryNumber === num;
  const isRouteEndpoint =
    routeState && (routeState.from.number === num || routeState.to.number === num);
  const actionHtml = isHere
    ? `<div class="iw-note">You're here</div>`
    : isRouteEndpoint
    ? `<div class="iw-note">Already on the route</div>`
    : `<button class="iw-route-btn" onclick="window.routeToGallery(${num})">Route here</button>`;
  infoWindow.setContent(`
    <div class="iw">
      <div class="iw-title">${num} — ${escapeHtml(name)}</div>
      ${imgHtml}
      <div class="iw-summary">${escapeHtml(summary || "")}</div>
      ${actionHtml}
    </div>
  `);
  infoWindow.setPosition(e.latLng);
  infoWindow.open(map);
}

function clearPickingUI() {
  pickingMode = null;
  document.querySelectorAll(".route-chip").forEach((c) => c.classList.remove("picking"));
  setStatus("");
}

window.routeToGallery = async function (targetNum) {
  infoWindow.close();
  const target = ALL_GALLERIES.find((g) => g.number === targetNum);
  if (!target) return;

  if (!resolvedGalleryNumber) {
    pendingRouteTarget = target;
    pickingMode = "route-new-from";
    setStatus("Tap your starting gallery");
    return;
  }
  const from = ALL_GALLERIES.find((g) => g.number === resolvedGalleryNumber);
  await computeRoute(from, target);
};

async function computeRoute(from, to) {
  if (from.number === to.number) {
    setStatus("Start and destination are the same gallery.", 3000);
    return;
  }
  setStatus("Finding route…");
  try {
    const res = await fetch("/route", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ from_gallery: from.number, to_gallery: to.number }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const route = await res.json();
    routeState = {
      from,
      to,
      distance_m: route.distance_m,
      steps: route.steps || [],
      upstream: route.upstream,
    };
    drawRoutePolyline();
    renderRouteSheet();
    setStatus("");

    if (from.floor !== activeFloor && from.floor === routeState.from.floor) {
      setFloor(from.floor);
    }
  } catch (err) {
    console.error("Route failed", err);
    setStatus("Couldn't compute route.", 4000);
  }
}

function drawRoutePolyline() {
  if (routePolyline) {
    routePolyline.setMap(null);
    routePolyline = null;
  }
  if (!routeState) return;
  const path = extractRoutePath(routeState);
  if (path.length < 2) return;
  routePolyline = new google.maps.Polyline({
    path,
    geodesic: false,
    strokeColor: "#1a73e8",
    strokeOpacity: 0.9,
    strokeWeight: 5,
    map,
  });
}

function extractRoutePath(rs) {
  if (rs.upstream && typeof rs.upstream === "object") {
    const pts = extractFromUpstream(rs.upstream);
    if (pts.length >= 2) return pts;
  }
  return (rs.steps || [])
    .filter((s) => s.lat != null && s.lon != null && s.floor === activeFloor)
    .map((s) => ({ lat: s.lat, lng: s.lon }));
}

function extractFromUpstream(up) {
  const pts = [];
  const visit = (obj) => {
    if (!obj || typeof obj !== "object") return;
    if (obj.type === "LineString" && Array.isArray(obj.coordinates)) {
      for (const [lon, lat] of obj.coordinates) pts.push({ lat, lng: lon });
      return;
    }
    if (obj.type === "MultiLineString" && Array.isArray(obj.coordinates)) {
      for (const line of obj.coordinates) {
        for (const [lon, lat] of line) pts.push({ lat, lng: lon });
      }
      return;
    }
    for (const k of Object.keys(obj)) visit(obj[k]);
  };
  visit(up);
  return pts;
}

function renderRouteSheet() {
  const sheet = document.getElementById("route-sheet");
  const fromText = document.querySelector("#route-from .route-chip-text");
  const toText = document.querySelector("#route-to .route-chip-text");
  const dist = document.getElementById("route-distance");
  const stepsEl = document.getElementById("route-steps");

  fromText.textContent = `${routeState.from.number} — ${routeState.from.name}`;
  toText.textContent = `${routeState.to.number} — ${routeState.to.name}`;

  const crossFloor = routeState.from.floor !== routeState.to.floor;
  dist.textContent = crossFloor
    ? `${Math.round(routeState.distance_m)} m · Floors ${routeState.from.floor} → ${routeState.to.floor}`
    : `${Math.round(routeState.distance_m)} m`;

  stepsEl.innerHTML = routeState.steps
    .map((s) => `<li>${escapeHtml(s.instruction)}</li>`)
    .join("");

  sheet.hidden = false;
  document.getElementById("you-chip").classList.remove("visible");
  refreshPolygonStyle();
}

function closeRouteSheet() {
  const sheet = document.getElementById("route-sheet");
  sheet.hidden = true;
  if (routePolyline) {
    routePolyline.setMap(null);
    routePolyline = null;
  }
  routeState = null;
  clearPickingUI();
  if (resolvedGalleryNumber) {
    document.getElementById("you-chip").classList.add("visible");
  }
}

function setFloor(floor) {
  if (floor === activeFloor) return;
  activeFloor = floor;
  document.querySelectorAll("#floor-picker button").forEach((b) => {
    const on = b.dataset.floor === floor;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  renderFloor(floor);
  infoWindow.close();
  if (routeState) drawRoutePolyline();
}

function renderFloor(floor) {
  map.data.forEach((f) => map.data.remove(f));
  centroidMarkers.forEach((m) => m.setMap(null));
  centroidMarkers = [];

  const onFloor = ALL_GALLERIES.filter((g) => g.floor === floor);
  const withPoly = onFloor.filter((g) => g.polygon);

  map.data.addGeoJson({
    type: "FeatureCollection",
    features: withPoly.map((g) => ({
      type: "Feature",
      geometry: g.polygon,
      properties: {
        number: g.number,
        name: g.name,
        summary: g.summary,
        image_url: g.image_url,
      },
    })),
  });

  for (const g of onFloor) {
    const marker = new google.maps.Marker({
      position: { lat: g.lat, lng: g.lon },
      map,
      clickable: false,
      icon: {
        path: google.maps.SymbolPath.CIRCLE,
        scale: 3,
        fillColor: "#ea4335",
        fillOpacity: 1,
        strokeWeight: 0,
      },
      title: `${g.number} — ${g.name}`,
    });
    centroidMarkers.push(marker);
  }

  renderTourOverlays();
  if (activeTour) renderTourSheet();
}

function getMockCoords() {
  const p = new URLSearchParams(location.search).get("mock");
  if (!p) return null;
  const [lat, lng] = p.split(",").map(Number);
  if (Number.isFinite(lat) && Number.isFinite(lng)) return { lat, lng, accuracy: 5 };
  return null;
}

async function locate() {
  const btn = document.getElementById("locate-btn");
  const mock = getMockCoords();

  if (!mock && !("geolocation" in navigator)) {
    setStatus("Geolocation isn't available on this device.", 4000);
    return;
  }

  btn.classList.add("loading");
  setStatus(mock ? "Using mock coords…" : "Finding you…");

  let lat, lng, accuracy;
  if (mock) {
    ({ lat, lng, accuracy } = mock);
  } else {
    let pos;
    try {
      pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 12000,
          maximumAge: 10000,
        });
      });
    } catch (err) {
      btn.classList.remove("loading");
      const msg = err.code === 1
        ? "Location permission denied."
        : err.code === 3
        ? "Location timed out. Try again outdoors or near a window."
        : "Couldn't get your location.";
      setStatus(msg, 5000);
      return;
    }
    ({ latitude: lat, longitude: lng, accuracy } = pos.coords);
  }
  drawUserPosition(lat, lng, accuracy);

  let winner;
  try {
    const [r2, r3] = await Promise.all([
      fetch(`/locate?lat=${lat}&lon=${lng}&floor=2`).then((r) => r.json()),
      fetch(`/locate?lat=${lat}&lon=${lng}&floor=3`).then((r) => r.json()),
    ]);
    winner = pickWinner(r2, r3);
  } catch (err) {
    btn.classList.remove("loading");
    console.error("Failed to resolve gallery", err);
    setStatus("Couldn't match you to a gallery.", 4000);
    return;
  }

  btn.classList.remove("loading");
  const resolvedFloor = winner.gallery.floor;
  if (resolvedFloor !== activeFloor) {
    setFloor(resolvedFloor);
  }

  setResolvedGallery(winner.gallery, winner.method);
  map.panTo({ lat, lng });

  if (winner.method !== "polygon" && winner.gallery.distance_m > AUTO_CORRECT_THRESHOLD_M) {
    showCorrectionPrompt();
  } else {
    hideCorrectionPrompt();
  }
  setStatus("");
}

function pickWinner(r2, r3) {
  const byMethod = [r2, r3].find((r) => r.method === "polygon");
  if (byMethod) return byMethod;
  return r2.gallery.distance_m <= r3.gallery.distance_m ? r2 : r3;
}

function drawUserPosition(lat, lng, accuracy) {
  const pos = { lat, lng };
  if (userMarker) userMarker.setMap(null);
  if (accuracyCircle) accuracyCircle.setMap(null);

  accuracyCircle = new google.maps.Circle({
    center: pos,
    radius: Math.max(accuracy || 15, 5),
    fillColor: "#4285F4",
    fillOpacity: 0.12,
    strokeColor: "#4285F4",
    strokeOpacity: 0.4,
    strokeWeight: 1,
    clickable: false,
    map,
  });

  userMarker = new google.maps.Marker({
    position: pos,
    map,
    zIndex: 999,
    icon: {
      path: google.maps.SymbolPath.CIRCLE,
      scale: 8,
      fillColor: "#4285F4",
      fillOpacity: 1,
      strokeColor: "white",
      strokeWeight: 2,
    },
    title: "You are here",
  });
}

function setResolvedGallery(gallery, method) {
  resolvedGalleryNumber = gallery.number;
  refreshPolygonStyle();
  renderYouChip(gallery, method);
}

function renderYouChip(gallery, method) {
  const chip = document.getElementById("you-chip");
  const label =
    method === "polygon"
      ? "You're in this gallery"
      : method === "user-tap"
      ? "Set by you"
      : `Best guess · ${Math.round(gallery.distance_m)} m from centroid`;
  const methodClass = method === "polygon" || method === "user-tap" ? "" : "best-guess";
  chip.innerHTML = `
    <span class="you-chip-dot"></span>
    <div class="you-chip-body">
      <div class="you-chip-gallery">${gallery.number} — ${escapeHtml(gallery.name)}</div>
      <div class="you-chip-method ${methodClass}">${label}</div>
    </div>
  `;
  chip.classList.add("visible");
}

function showCorrectionPrompt() {
  awaitingCorrection = true;
  const el = document.getElementById("correction-prompt");
  el.hidden = false;
}

function hideCorrectionPrompt() {
  const el = document.getElementById("correction-prompt");
  el.hidden = true;
}

let statusTimer;
function setStatus(text, autoHideMs = 0) {
  const el = document.getElementById("status");
  clearTimeout(statusTimer);
  if (!text) {
    el.classList.remove("visible");
    return;
  }
  el.textContent = text;
  el.classList.add("visible");
  if (autoHideMs) {
    statusTimer = setTimeout(() => el.classList.remove("visible"), autoHideMs);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------- Curated tours ----------

async function loadTours() {
  try {
    const res = await fetch("tours.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    ALL_TOURS = await res.json();
    renderTourList();
  } catch (err) {
    console.warn("tours.json not available", err);
    document.getElementById("tours-btn").style.display = "none";
  }
}

function renderTourList() {
  const container = document.getElementById("tour-list");
  container.innerHTML = ALL_TOURS.map((t) => `
    <button class="tour-card" data-tour-id="${escapeHtml(t.id)}" type="button">
      <div class="tour-card-meta">
        <span class="tour-card-duration">${t.duration_min} min</span>
        <span class="tour-card-stops">${t.stops.length} stops</span>
      </div>
      <div class="tour-card-title">${escapeHtml(t.title)}</div>
      <div class="tour-card-summary">${escapeHtml(t.summary || "")}</div>
    </button>
  `).join("");
  container.querySelectorAll(".tour-card").forEach((el) => {
    el.addEventListener("click", () => activateTour(el.dataset.tourId));
  });
}

function openTourDrawer() {
  const drawer = document.getElementById("tour-drawer");
  const backdrop = document.getElementById("tour-backdrop");
  drawer.hidden = false;
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    drawer.classList.add("open");
    backdrop.classList.add("visible");
  });
}

function closeTourDrawer() {
  const drawer = document.getElementById("tour-drawer");
  const backdrop = document.getElementById("tour-backdrop");
  drawer.classList.remove("open");
  backdrop.classList.remove("visible");
  setTimeout(() => {
    drawer.hidden = true;
    backdrop.hidden = true;
  }, 220);
}

async function activateTour(id) {
  const t = ALL_TOURS.find((x) => x.id === id);
  if (!t) return;
  activeTour = t;
  closeTourDrawer();

  // Fetch /route for every consecutive stop pair in parallel (once per tour).
  // The upstream field holds Living Map's real polyline; extractRoutePath
  // (shared with the gallery-to-gallery route feature) pulls it out of the
  // nested GeoJSON LineString so the path follows corridors instead of
  // crossing walls.
  if (!t._resolvedSegments) {
    setStatus("Building tour route…");
    const pairs = [];
    for (let i = 0; i < t.stops.length - 1; i++) {
      pairs.push([t.stops[i].gallery, t.stops[i + 1].gallery]);
    }
    t._resolvedSegments = await Promise.all(
      pairs.map(([a, b]) =>
        fetch("/route", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ from_gallery: a, to_gallery: b }),
        })
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    );
    setStatus("");
    if (activeTour !== t) return; // user cleared the tour mid-fetch
  }

  const galleries = new Map(ALL_GALLERIES.map((g) => [g.number, g]));
  const firstStop = t.stops[0] && galleries.get(t.stops[0].gallery);
  if (firstStop && firstStop.floor !== activeFloor) {
    setFloor(firstStop.floor);
  } else {
    renderTourOverlays();
  }
  refreshPolygonStyle();
  renderTourBar();
  renderTourSheet();
  if (firstStop) map.panTo({ lat: firstStop.lat, lng: firstStop.lon });
}

function clearActiveTour() {
  activeTour = null;
  clearTourOverlays();
  refreshPolygonStyle();
  document.getElementById("tour-bar").hidden = true;
  const sheet = document.getElementById("tour-sheet");
  sheet.hidden = true;
  sheet.classList.remove("collapsed");
  infoWindow.close();
}

function clearTourOverlays() {
  tourPolylines.forEach((p) => p.setMap(null));
  tourPolylines = [];
  tourMarkers.forEach((m) => m.setMap(null));
  tourMarkers = [];
}

function renderTourOverlays() {
  clearTourOverlays();
  if (!activeTour) return;

  const galleries = new Map(ALL_GALLERIES.map((g) => [g.number, g]));
  const resolved = activeTour._resolvedSegments || [];

  // Draw a polyline per segment. Only same-floor segments are drawn —
  // cross-floor hops are represented by markers appearing on each floor as
  // the user toggles the floor picker, not by a line across walls.
  //
  // Each underlying GeoJSON LineString inside the upstream response is
  // drawn as its own polyline. Concatenating MultiLineStrings into one
  // flat point list creates "jumps" between disjoint pieces that look
  // like scribbles on the map.
  for (let i = 0; i < activeTour.stops.length - 1; i++) {
    const segRes = resolved[i];
    if (!segRes) continue;
    const fromG = galleries.get(activeTour.stops[i].gallery);
    const toG = galleries.get(activeTour.stops[i + 1].gallery);
    if (!fromG || !toG) continue;
    if (fromG.floor !== activeFloor || toG.floor !== activeFloor) continue;

    let lineStrings = extractTourLineStrings(segRes);
    if (!lineStrings.length) {
      // Fallback: steps filtered to current floor (same shape as
      // extractRoutePath's fallback). Gives at least a straight line.
      const stepPath = (segRes.steps || [])
        .filter((s) => s.lat != null && s.lon != null && s.floor === activeFloor)
        .map((s) => ({ lat: s.lat, lng: s.lon }));
      if (stepPath.length >= 2) lineStrings = [stepPath];
    }

    for (const ls of lineStrings) {
      if (ls.length < 2) continue;
      const pl = new google.maps.Polyline({
        path: ls,
        geodesic: false,
        strokeColor: "#b8321c",
        strokeOpacity: 0.85,
        strokeWeight: 4,
        map,
        zIndex: 2,
      });
      tourPolylines.push(pl);
    }
  }

  // Numbered stop markers (only stops on the current floor).
  activeTour.stops.forEach((stop, i) => {
    const g = galleries.get(stop.gallery);
    if (!g || g.floor !== activeFloor) return;
    const marker = new google.maps.Marker({
      position: { lat: g.lat, lng: g.lon },
      map,
      label: { text: String(i + 1), color: "white", fontSize: "12px", fontWeight: "700" },
      icon: {
        path: google.maps.SymbolPath.CIRCLE,
        scale: 14,
        fillColor: "#b8321c",
        fillOpacity: 1,
        strokeColor: "white",
        strokeWeight: 2,
      },
      zIndex: 10,
      title: `${i + 1}. ${stop.artwork.title}`,
    });
    marker.addListener("click", () => openStopInfoWindow(stop, i + 1));
    tourMarkers.push(marker);
  });
}

// Walks a /route upstream response and returns one point-array per
// GeoJSON LineString. Unlike extractFromUpstream (which flattens everything
// into a single continuous list and joins disjoint pieces), this keeps each
// piece separate so we can draw them as independent polylines.
function extractTourLineStrings(segRes) {
  if (!segRes || !segRes.upstream || typeof segRes.upstream !== "object") return [];
  const out = [];
  const visit = (obj) => {
    if (!obj || typeof obj !== "object") return;
    if (obj.type === "LineString" && Array.isArray(obj.coordinates)) {
      out.push(obj.coordinates.map(([lon, lat]) => ({ lat, lng: lon })));
      return;
    }
    if (obj.type === "MultiLineString" && Array.isArray(obj.coordinates)) {
      for (const line of obj.coordinates) {
        out.push(line.map(([lon, lat]) => ({ lat, lng: lon })));
      }
      return;
    }
    for (const k of Object.keys(obj)) visit(obj[k]);
  };
  visit(segRes.upstream);
  return out;
}

function openStopInfoWindow(stop, num) {
  const a = stop.artwork;
  const imgHtml = a.image_url ? `<img src="${escapeHtml(a.image_url)}" alt="">` : "";
  const metaBits = [a.date, a.medium].filter(Boolean).map((x) => escapeHtml(x)).join(" · ");
  const link = a.object_url
    ? `<a class="iw-link" href="${escapeHtml(a.object_url)}" target="_blank" rel="noopener">View on metmuseum.org →</a>`
    : "";
  const noteHtml = stop.note ? `<div class="iw-note">${escapeHtml(stop.note)}</div>` : "";
  infoWindow.setContent(`
    <div class="iw iw-stop">
      <div class="iw-stop-header">
        <span class="iw-stop-num">${num}</span>
        <span class="iw-stop-gallery">Gallery ${stop.gallery}</span>
      </div>
      <div class="iw-title">${escapeHtml(a.title)}</div>
      <div class="iw-artist">${escapeHtml(a.artist)}</div>
      ${imgHtml}
      ${metaBits ? `<div class="iw-meta">${metaBits}</div>` : ""}
      ${noteHtml}
      ${link}
    </div>
  `);
  const g = ALL_GALLERIES.find((x) => x.number === stop.gallery);
  if (g) {
    infoWindow.setPosition({ lat: g.lat, lng: g.lon });
    infoWindow.open(map);
  }
}

function renderTourSheet() {
  const sheet = document.getElementById("tour-sheet");
  if (!activeTour) { sheet.hidden = true; return; }

  const galleries = new Map(ALL_GALLERIES.map((g) => [g.number, g]));
  const floors = new Set(activeTour.stops.map((s) => {
    const g = galleries.get(s.gallery);
    return g ? g.floor : null;
  }).filter(Boolean));

  sheet.querySelector(".tour-sheet-title").textContent = activeTour.title;
  sheet.querySelector(".tour-sheet-meta").textContent =
    `${activeTour.stops.length} stops · ${activeTour.duration_min} min` +
    (floors.size > 1 ? ` · Floors ${[...floors].sort().join(" & ")}` : "");

  const stopsEl = document.getElementById("tour-sheet-stops");
  const resolved = activeTour._resolvedSegments || [];
  const parts = [];
  activeTour.stops.forEach((stop, i) => {
    parts.push(renderStopLi(stop, i, galleries));
    if (i < activeTour.stops.length - 1) {
      parts.push(renderSegmentLi(resolved[i]));
    }
  });
  stopsEl.innerHTML = parts.join("");
  stopsEl.querySelectorAll(".tour-sheet-stop").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = parseInt(el.dataset.stopIndex, 10);
      goToStop(idx);
    });
  });

  sheet.hidden = false;
}

function renderStopLi(stop, i, galleries) {
  const g = galleries.get(stop.gallery);
  const offFloor = g && g.floor !== activeFloor;
  const a = stop.artwork;
  const thumb = a.image_url
    ? `<img class="tour-sheet-stop-thumb" src="${escapeHtml(a.image_url)}" alt="" loading="lazy">`
    : `<div class="tour-sheet-stop-thumb"></div>`;
  const floorHint = offFloor && g ? `<div class="tour-sheet-floor-hint">Floor ${g.floor}</div>` : "";
  return `
    <li class="tour-sheet-stop ${offFloor ? "off-floor" : ""}" data-stop-index="${i}">
      <span class="tour-sheet-stop-num">${i + 1}</span>
      ${thumb}
      <div class="tour-sheet-stop-body">
        <div class="tour-sheet-stop-title">${escapeHtml(a.title)}</div>
        <div class="tour-sheet-stop-sub">G${stop.gallery} · ${escapeHtml(a.artist)}</div>
        ${floorHint}
      </div>
    </li>
  `;
}

// Connector between two stop cards — shows walking distance plus any
// lift/stairs instruction from the cached /route response. "Start at …" and
// "Arrive at …" are filtered out because those endpoints are the adjacent
// stop cards.
function renderSegmentLi(segRes) {
  if (!segRes) return `<li class="tour-sheet-segment"><div class="tour-sheet-seg-connector"></div><div class="tour-sheet-seg-body"><span class="tour-sheet-seg-muted">Directions unavailable</span></div></li>`;

  const dist = Math.round(segRes.distance_m || 0);
  const extraSteps = (segRes.steps || [])
    .map((s) => s && s.instruction)
    .filter((ins) => ins && !/^(Start at|Arrive at) /i.test(ins));
  const extraHtml = extraSteps
    .map((ins) => `<div class="tour-sheet-seg-extra">${escapeHtml(ins)}</div>`)
    .join("");

  return `
    <li class="tour-sheet-segment">
      <div class="tour-sheet-seg-connector"></div>
      <div class="tour-sheet-seg-body">
        <div class="tour-sheet-seg-walk">↓ Walk ~${dist} m</div>
        ${extraHtml}
      </div>
    </li>
  `;
}

function goToStop(idx) {
  if (!activeTour) return;
  const stop = activeTour.stops[idx];
  if (!stop) return;
  const g = ALL_GALLERIES.find((x) => x.number === stop.gallery);
  if (!g) return;
  if (g.floor !== activeFloor) setFloor(g.floor);
  map.panTo({ lat: g.lat, lng: g.lon });
  openStopInfoWindow(stop, idx + 1);
}

function renderTourBar() {
  const bar = document.getElementById("tour-bar");
  if (!activeTour) { bar.hidden = true; return; }
  const floorMix = new Set(activeTour.stops.map((s) => {
    const g = ALL_GALLERIES.find((x) => x.number === s.gallery);
    return g ? g.floor : null;
  }));
  const spansFloors = floorMix.size > 1;
  bar.querySelector(".tour-bar-title").textContent = activeTour.title;
  bar.querySelector(".tour-bar-progress").textContent =
    `${activeTour.stops.length} stops · ${activeTour.duration_min} min${spansFloors ? " · includes a lift ride" : ""}`;
  bar.hidden = false;
}
