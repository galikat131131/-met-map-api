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
  const isResolved = feature.getProperty("number") === resolvedGalleryNumber;
  return {
    fillColor: isResolved ? "#0f9d58" : "#1a73e8",
    fillOpacity: isResolved ? 0.45 : 0.25,
    strokeColor: isResolved ? "#0f9d58" : "#1a73e8",
    strokeWeight: 2,
    clickable: true,
  };
}

function refreshPolygonStyle() {
  map.data.setStyle(polygonStyle);
}

function onPolygonClick(e) {
  const num = e.feature.getProperty("number");
  if (awaitingCorrection) {
    const g = ALL_GALLERIES.find((x) => x.number === num);
    if (g) {
      setResolvedGallery(g, "user-tap");
      awaitingCorrection = false;
      hideCorrectionPrompt();
    }
    return;
  }
  const name = e.feature.getProperty("name");
  const summary = e.feature.getProperty("summary");
  const img = e.feature.getProperty("image_url");
  const imgHtml = img ? `<img src="${img}" alt="">` : "";
  infoWindow.setContent(`
    <div class="iw">
      <div class="iw-title">${num} — ${escapeHtml(name)}</div>
      ${imgHtml}
      <div class="iw-summary">${escapeHtml(summary || "")}</div>
    </div>
  `);
  infoWindow.setPosition(e.latLng);
  infoWindow.open(map);
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
