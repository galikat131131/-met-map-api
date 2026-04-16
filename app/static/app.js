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
};

function polygonStyle(feature) {
  const num = feature.getProperty("number");
  const isResolved = num === resolvedGalleryNumber;
  const isRouteTo = routeState && routeState.to.number === num;
  const isRouteFrom = routeState && routeState.from.number === num && !isResolved;
  if (isRouteTo) {
    return {
      fillColor: "#ea4335",
      fillOpacity: 0.45,
      strokeColor: "#ea4335",
      strokeWeight: 2,
      clickable: true,
    };
  }
  if (isResolved || isRouteFrom) {
    return {
      fillColor: "#0f9d58",
      fillOpacity: 0.45,
      strokeColor: "#0f9d58",
      strokeWeight: 2,
      clickable: true,
    };
  }
  return {
    fillColor: "#1a73e8",
    fillOpacity: 0.25,
    strokeColor: "#1a73e8",
    strokeWeight: 2,
    clickable: true,
  };
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
