const API_BASE = "/api/pins";

const DEFAULT_CATEGORIES = [
  { type: "good", label: "Dobre", tone: "good" },
  { type: "bad", label: "Spatne", tone: "bad" },
  { type: "change", label: "Tady to chce zmenu", tone: "change" },
];

const initialCenter = [48.9407, 16.7376];

const state = {
  markers: new Map(),
  pendingLatLng: null,
  ignoreMapClicksUntil: 0,
  ignoreChoicePressUntil: 0,
  commentSaveTimers: new Map(),
  categories: new Map(),
  selectedFilters: new Set(),
};

const map = L.map("map").setView(initialCenter, 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

const goodIcon = L.icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png",
  shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const badIcon = L.icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
  shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const neutralIcon = L.icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png",
  shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const changeIcon = L.icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-gold.png",
  shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const clearAllButton = document.getElementById("clear-all");
const pinChoice = document.getElementById("pin-choice");
const pinChoiceOptions = document.getElementById("pin-choice-options");
const choiceCancel = document.getElementById("choice-cancel");
const filtersList = document.getElementById("filters-list");

clearAllButton.addEventListener("click", async () => {
  if (!window.confirm("Opravdu smazat vsechny piny?")) {
    return;
  }

  try {
    await apiDeleteAllPins();
    clearAllMarkers();
    updateFilterCounts();
  } catch (error) {
    showError(error);
  }
});

map.on("click", (event) => {
  if (Date.now() < state.ignoreMapClicksUntil) {
    return;
  }
  state.pendingLatLng = event.latlng;
  showPinChoice();
});

bindChoicePress(choiceCancel, hidePinChoice);
bindChoiceOptionPress();
registerDefaultCategories();
renderFilters();
renderPinChoiceOptions();

function bindChoiceOptionPress() {
  const handler = (event) => {
    const button = event.target.closest("[data-choice-type]");
    if (!button) return;

    if (Date.now() < state.ignoreChoicePressUntil) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    state.ignoreChoicePressUntil = Date.now() + 250;
    event.preventDefault();
    event.stopPropagation();
    state.ignoreMapClicksUntil = Date.now() + 500;
    createPendingPin(button.dataset.choiceType);
  };

  pinChoiceOptions.addEventListener("click", handler);
  pinChoiceOptions.addEventListener("touchend", handler, { passive: false });
  pinChoiceOptions.addEventListener("pointerup", handler);
}

function registerDefaultCategories() {
  DEFAULT_CATEGORIES.forEach((category) => {
    state.categories.set(category.type, { ...category });
    state.selectedFilters.add(category.type);
  });
}

function ensureCategory(type) {
  if (!state.categories.has(type)) {
    state.categories.set(type, {
      type,
      label: prettifyCategoryLabel(type),
      tone: "neutral",
    });
    state.selectedFilters.add(type);
    renderFilters();
    renderPinChoiceOptions();
  }

  return state.categories.get(type);
}

function prettifyCategoryLabel(type) {
  if (typeof type !== "string" || !type.trim()) return "Neznama";
  const normalized = type.replace(/[_-]+/g, " ").trim();
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function showPinChoice() {
  renderPinChoiceOptions();
  pinChoice.classList.remove("hidden");
}

function hidePinChoice() {
  pinChoice.classList.add("hidden");
  state.pendingLatLng = null;
}

async function createPendingPin(type) {
  if (!state.pendingLatLng || !type) return;
  const category = ensureCategory(type);
  if (!category) return;

  const pin = {
    id: generatePinId(),
    lat: state.pendingLatLng.lat,
    lng: state.pendingLatLng.lng,
    type: category.type,
    comment: "",
  };

  hidePinChoice();

  try {
    const saved = await apiCreatePin(pin);
    addPinToMap(saved, true);
    updateFilterCounts();
  } catch (error) {
    showError(error);
  }
}

function bindChoicePress(element, action) {
  const handler = (event) => {
    event.preventDefault();
    event.stopPropagation();
    state.ignoreMapClicksUntil = Date.now() + 500;
    action();
  };

  element.addEventListener("click", handler);
  element.addEventListener("touchend", handler, { passive: false });
  element.addEventListener("pointerup", handler);
}

function renderPinChoiceOptions() {
  pinChoiceOptions.innerHTML = "";
  state.categories.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pin-option ${category.tone}`;
    button.dataset.choiceType = category.type;
    button.textContent = category.label;
    pinChoiceOptions.appendChild(button);
  });
}

function renderFilters() {
  filtersList.innerHTML = "";

  state.categories.forEach((category) => {
    const row = document.createElement("label");
    row.className = `filter-item ${category.tone}`;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "filter-checkbox";
    checkbox.dataset.filterType = category.type;
    checkbox.checked = state.selectedFilters.has(category.type);

    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedFilters.add(category.type);
      } else {
        state.selectedFilters.delete(category.type);
      }
      applyFilterToMarkers();
    });

    const text = document.createElement("span");
    text.className = "filter-label";
    text.textContent = category.label;

    const count = document.createElement("span");
    count.className = "filter-count";
    count.dataset.countType = category.type;
    count.textContent = "(0)";

    row.appendChild(checkbox);
    row.appendChild(text);
    row.appendChild(count);
    filtersList.appendChild(row);
  });

  updateFilterCounts();
}

function generatePinId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }

  if (window.crypto && typeof window.crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  return `pin-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function markerIconForType(type) {
  if (type === "good") return goodIcon;
  if (type === "bad") return badIcon;
  if (type === "change") return changeIcon;
  return neutralIcon;
}

function addPinToMap(pin, openPopup = false) {
  if (!isValidPin(pin)) {
    return;
  }

  if (state.markers.has(pin.id)) {
    return;
  }

  ensureCategory(pin.type);

  const marker = L.marker([pin.lat, pin.lng], {
    icon: markerIconForType(pin.type),
  });

  const shouldBeVisible = matchesFilter(pin.type);
  if (shouldBeVisible) {
    marker.addTo(map);
  }

  state.markers.set(pin.id, { marker, pin });
  marker.bindPopup(buildPopupContent(pin.id));

  if (openPopup && shouldBeVisible) {
    marker.openPopup();
  }
}

function buildPopupContent(pinId) {
  const target = state.markers.get(pinId);
  if (!target) {
    return document.createElement("div");
  }

  const { pin } = target;
  const category = ensureCategory(pin.type);
  const container = document.createElement("div");

  const label = document.createElement("div");
  label.className = `pin-label ${category.tone}`;
  if (pin.type === "good") {
    label.textContent = "Citim se dobre";
  } else if (pin.type === "bad") {
    label.textContent = "Necitim se dobre";
  } else if (pin.type === "change") {
    label.textContent = "Tady to chce zmenu";
  } else {
    label.textContent = `Kategorie: ${category.label}`;
  }
  container.appendChild(label);

  const form = document.createElement("form");
  form.className = "comment-form";

  const labelEl = document.createElement("label");
  labelEl.textContent = "Komentar";

  const textarea = document.createElement("textarea");
  textarea.name = "comment";
  textarea.rows = 3;
  textarea.maxLength = 300;
  textarea.placeholder = "Proc se tady citis takto?";
  textarea.value = pin.comment || "";

  labelEl.appendChild(textarea);
  form.appendChild(labelEl);

  const syncComment = () => {
    const next = state.markers.get(pinId);
    if (!next) return;

    next.pin.comment = textarea.value.trim();
    scheduleCommentSave(pinId, next.pin.comment);
  };

  form.addEventListener("submit", (e) => e.preventDefault());
  textarea.addEventListener("input", syncComment);
  textarea.addEventListener("blur", syncComment);

  container.appendChild(form);
  return container;
}

function scheduleCommentSave(pinId, comment) {
  const existingTimer = state.commentSaveTimers.get(pinId);
  if (existingTimer) {
    clearTimeout(existingTimer);
  }

  const timer = setTimeout(async () => {
    state.commentSaveTimers.delete(pinId);
    try {
      await apiUpdatePinComment(pinId, comment);
    } catch (error) {
      showError(error);
    }
  }, 350);

  state.commentSaveTimers.set(pinId, timer);
}

function clearAllMarkers() {
  state.markers.forEach(({ marker }) => map.removeLayer(marker));
  state.markers.clear();
  state.commentSaveTimers.forEach((timerId) => clearTimeout(timerId));
  state.commentSaveTimers.clear();
}

function matchesFilter(pinType) {
  return state.selectedFilters.has(pinType);
}

function applyFilterToMarkers() {
  state.markers.forEach(({ marker, pin }) => {
    const shouldBeVisible = matchesFilter(pin.type);
    const isVisible = map.hasLayer(marker);

    if (shouldBeVisible && !isVisible) {
      marker.addTo(map);
      return;
    }

    if (!shouldBeVisible && isVisible) {
      map.removeLayer(marker);
    }
  });
}

function updateFilterCounts() {
  const counts = new Map();
  state.markers.forEach(({ pin }) => {
    counts.set(pin.type, (counts.get(pin.type) || 0) + 1);
  });

  filtersList.querySelectorAll("[data-count-type]").forEach((countEl) => {
    const type = countEl.dataset.countType;
    countEl.textContent = `(${counts.get(type) || 0})`;
  });
}

async function loadPinsFromServer() {
  try {
    const pins = await apiListPins();
    clearAllMarkers();
    pins.forEach((pin) => {
      addPinToMap(pin, false);
    });
    applyFilterToMarkers();
    updateFilterCounts();
  } catch (error) {
    showError(error);
  }
}

function isValidPin(pin) {
  return (
    typeof pin?.id === "string" &&
    typeof pin?.lat === "number" &&
    typeof pin?.lng === "number" &&
    typeof pin?.type === "string" &&
    pin.type.length > 0 &&
    typeof pin?.comment === "string"
  );
}

async function apiListPins() {
  const response = await fetch(API_BASE);
  if (!response.ok) {
    throw new Error("Nepodarilo se nacist piny ze serveru.");
  }
  const payload = await response.json();
  return Array.isArray(payload.pins) ? payload.pins : [];
}

async function apiCreatePin(pin) {
  const response = await fetch(API_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pin),
  });

  if (!response.ok) {
    throw new Error("Nepodarilo se ulozit novy pin.");
  }
  return response.json();
}

async function apiUpdatePinComment(pinId, comment) {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(pinId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment }),
  });

  if (!response.ok) {
    throw new Error("Nepodarilo se ulozit komentar.");
  }
  return response.json();
}

async function apiDeleteAllPins() {
  const response = await fetch(API_BASE, { method: "DELETE" });
  if (!response.ok) {
    throw new Error("Nepodarilo se smazat piny.");
  }
  return response.json();
}

function showError(error) {
  console.error(error);
  window.alert(error?.message || "Doslo k chybe.");
}

loadPinsFromServer();
