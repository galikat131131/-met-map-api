// Anonymous route heat-map tracker.
// Session-scoped UUID in sessionStorage (no cross-visit identity).
// Opt-in banner gated on localStorage.heatmapOptIn.
// Records gallery-to-gallery transitions via POST /track/transition.
(function () {
  const WATCH_THROTTLE_MS = 5000;

  let lastTrackedGalleryNumber = null;
  let lastTrackedFloor = null;
  let watchId = null;
  let lastFixMs = 0;

  function optedIn()  { return localStorage.getItem("heatmapOptIn") === "yes"; }
  function optedOut() { return localStorage.getItem("heatmapOptIn") === "no"; }

  function getSessionId() {
    let id = sessionStorage.getItem("heatmap.sessionId");
    if (!id) {
      id = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : (Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
      sessionStorage.setItem("heatmap.sessionId", id);
    }
    return id;
  }

  function showConsent() {
    const el = document.getElementById("consent-banner");
    if (!el) return;
    el.hidden = false;
    document.getElementById("consent-yes").onclick = () => {
      localStorage.setItem("heatmapOptIn", "yes");
      el.hidden = true;
      maybeStartWatch();
    };
    document.getElementById("consent-no").onclick = () => {
      localStorage.setItem("heatmapOptIn", "no");
      el.hidden = true;
    };
  }

  function pickWinner(r2, r3) {
    if (r2 && r2.method === "polygon") return r2;
    if (r3 && r3.method === "polygon") return r3;
    if (r2 && r3) return r2.gallery.distance_m <= r3.gallery.distance_m ? r2 : r3;
    return r2 || r3 || null;
  }

  async function onWatchFix(pos) {
    const now = Date.now();
    if (now - lastFixMs < WATCH_THROTTLE_MS) return;
    lastFixMs = now;

    const { latitude: lat, longitude: lon } = pos.coords;
    let winner;
    try {
      const [r2, r3] = await Promise.all([
        fetch(`/locate?lat=${lat}&lon=${lon}&floor=2`).then(r => r.ok ? r.json() : null),
        fetch(`/locate?lat=${lat}&lon=${lon}&floor=3`).then(r => r.ok ? r.json() : null),
      ]);
      winner = pickWinner(r2, r3);
    } catch {
      return;
    }
    if (!winner || !winner.gallery) return;

    const num = winner.gallery.number;
    const floor = winner.gallery.floor;

    if (lastTrackedGalleryNumber == null) {
      lastTrackedGalleryNumber = num;
      lastTrackedFloor = floor;
      return;
    }
    if (num === lastTrackedGalleryNumber) return;

    // TODO (see TODO.md "heat-map transition smoothing"): require N consecutive
    // hits in the new gallery or a minimum dwell time before committing, to
    // filter GPS flicker across polygon boundaries.
    postTransition({
      session_id: getSessionId(),
      from_gallery: lastTrackedGalleryNumber,
      to_gallery: num,
      floor_from: lastTrackedFloor,
      floor_to: floor,
      client_ts: Date.now(),
      locate_method: winner.method,
    });
    lastTrackedGalleryNumber = num;
    lastTrackedFloor = floor;
  }

  function postTransition(body) {
    fetch("/track/transition", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  }

  function maybeStartWatch() {
    if (!optedIn()) return;
    if (watchId !== null) return;
    if (!("geolocation" in navigator)) return;
    watchId = navigator.geolocation.watchPosition(
      onWatchFix,
      (err) => console.warn("heatmap watchPosition error", err),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
    );
  }

  function onLocate(winner) {
    if (lastTrackedGalleryNumber == null && winner && winner.gallery) {
      lastTrackedGalleryNumber = winner.gallery.number;
      lastTrackedFloor = winner.gallery.floor;
    }
    maybeStartWatch();
  }

  function init() {
    getSessionId();
    if (!optedIn() && !optedOut()) showConsent();
    else if (optedIn()) maybeStartWatch();
  }

  window.__heatmap = { init, onLocate };
})();
