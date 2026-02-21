
const API_BASE = "/api";
const AUTH_STORAGE_KEY = "pocitovaMapaAuthToken";
const FEELINGS_LAYER_KEY = "feelings";
const HEX_OVERLAY_LAYER_KEY = "north_hex_grid";
const HUSTOPECE_OVERLAY_BOUNDS = {
  south: 48.928,
  north: 48.956,
  west: 16.713,
  east: 16.758,
};

const HEX_OVERLAY_STYLE = {
  radiusMeters: 56,
  color: "#7b7b7b",
  fillColor: "#8a8a8a",
  weight: 0.5,
  opacity: 0.08,
  fillOpacity: 0.12,
};

const HEX_NEUTRAL_STYLE = {
  color: "#7b7b7b",
  fillColor: "#8a8a8a",
  opacity: 0.08,
  fillOpacity: 0.12,
};

const DEFAULT_LAYERS = [
  {
    key: HEX_OVERLAY_LAYER_KEY,
    name: "Hex overlay Hustopece",
    kind: "overlay",
    allow_user_points: false,
    is_enabled: true,
    sort_order: 5,
  },
  {
    key: FEELINGS_LAYER_KEY,
    name: "Pocitova mapa",
    kind: "interactive",
    allow_user_points: true,
    is_enabled: true,
    sort_order: 10,
  },
  {
    key: "city_buildings",
    name: "Mestske budovy",
    kind: "static",
    allow_user_points: false,
    is_enabled: true,
    sort_order: 20,
  },
];

const DEFAULT_CATEGORIES = [
  { type: "good", label: "Dobre", tone: "good" },
  { type: "bad", label: "Spatne", tone: "bad" },
  { type: "change", label: "Tady to chce zmenu", tone: "change" },
];

const initialCenter = [48.9407, 16.7376];

const state = {
  markers: new Map(),
  staticLayerMarkers: new Map(),
  layerGroups: new Map(),
  availableLayers: new Map(),
  selectedLayers: new Set(),
  pendingLatLng: null,
  ignoreMapClicksUntil: 0,
  ignoreChoicePressUntil: 0,
  commentSaveTimers: new Map(),
  categories: new Map(),
  selectedFilters: new Set(),
  hexCellCount: 0,
  authToken: null,
  authUser: null,
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
const layersList = document.getElementById("layers-list");
const layerToggleButton = document.getElementById("layer-toggle-btn");
const layerMenu = document.getElementById("layer-menu");
const filtersList = document.getElementById("filters-list");
const panel = document.querySelector(".panel");
const mobilePanelToggle = document.getElementById("mobile-panel-toggle");
const mobilePanelBackdrop = document.getElementById("mobile-panel-backdrop");
const authForm = document.getElementById("auth-form");
const authEmailInput = document.getElementById("auth-email");
const authNameInput = document.getElementById("auth-name");
const authRegisterButton = document.getElementById("auth-register");
const authCurrent = document.getElementById("auth-current");
const authCurrentText = document.getElementById("auth-current-text");
const authLogoutButton = document.getElementById("auth-logout");
const authNote = document.getElementById("auth-note");

clearAllButton.addEventListener("click", async () => {
  if (!state.authUser || state.authUser.role !== "admin") {
    window.alert("Mazani vsech pinu je jen pro admina.");
    return;
  }

  if (!window.confirm("Opravdu smazat vsechny piny?")) {
    return;
  }

  try {
    await apiDeleteAllPins();
    clearAllMarkers();
    updateFilterCounts();
    updateLayerCounts();
  } catch (error) {
    showError(error);
  }
});

map.on("click", (event) => {
  if (closeMobilePanelIfOpen()) {
    return;
  }
  if (Date.now() < state.ignoreMapClicksUntil) {
    return;
  }
  if (!isLayerVisible(FEELINGS_LAYER_KEY)) {
    window.alert("Pro pridani pinu zapni vrstvu Pocitova mapa.");
    return;
  }
  if (!state.authUser) {
    window.alert("Pro pridani pinu se prihlas (email + jmeno).");
    return;
  }
  state.pendingLatLng = event.latlng;
  showPinChoice();
});

map.on("moveend", () => {
  refreshHexOverlay();
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = authEmailInput.value.trim();
  const name = authNameInput.value.trim();
  if (!email || !name) return;

  try {
    const payload = await apiLogin(email, name);
    setAuth(payload.token, payload.user);
    refreshAuthUi();
    await loadDataForAllLayers();
  } catch (error) {
    showError(error);
  }
});

authRegisterButton.addEventListener("click", async () => {
  const email = authEmailInput.value.trim();
  const name = authNameInput.value.trim();
  if (!email || !name) return;

  try {
    const payload = await apiRegister(email, name);
    setAuth(payload.token, payload.user);
    refreshAuthUi();
    await loadDataForAllLayers();
  } catch (error) {
    showError(error);
  }
});

authLogoutButton.addEventListener("click", async () => {
  try {
    await apiLogout();
  } catch {
    // Ignore logout request errors and clear local state anyway.
  } finally {
    setAuth(null, null);
    refreshAuthUi();
    loadDataForAllLayers();
  }
});

bindChoicePress(choiceCancel, hidePinChoice);
bindChoiceOptionPress();
bindLayerMenu();
bindMobilePanel();
registerDefaultCategories();
registerDefaultLayers();
renderFilters();
renderPinChoiceOptions();
loadSession();
refreshAuthUi();
initialize();

async function initialize() {
  if (state.authToken) {
    try {
      const mePayload = await apiMe();
      setAuth(state.authToken, mePayload.user || null);
    } catch {
      setAuth(null, null);
    }
    refreshAuthUi();
  }

  await loadLayersFromServer();
  renderLayerFilters();
  await loadDataForAllLayers();
}

function loadSession() {
  const token = localStorage.getItem(AUTH_STORAGE_KEY);
  if (token) {
    state.authToken = token;
  }
}

function setAuth(token, user) {
  state.authToken = token || null;
  state.authUser = user || null;

  if (state.authToken) {
    localStorage.setItem(AUTH_STORAGE_KEY, state.authToken);
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
}

function refreshAuthUi() {
  const isLoggedIn = Boolean(state.authUser);
  authForm.classList.toggle("hidden", isLoggedIn);
  authCurrent.classList.toggle("hidden", !isLoggedIn);

  if (!isLoggedIn) {
    authCurrentText.textContent = "";
    authNote.textContent = "Neprihlaseny uzivatel muze mapu jen prohlizet.";
    clearAllButton.disabled = true;
    return;
  }

  authCurrentText.textContent = `${state.authUser.name} (${state.authUser.email}) - role: ${state.authUser.role}`;
  authNote.textContent = "Prihlaseny uzivatel muze pridavat piny. Editace je dle opravneni.";
  clearAllButton.disabled = state.authUser.role !== "admin";
}

function registerDefaultLayers() {
  state.selectedLayers.clear();
  state.selectedLayers.add(FEELINGS_LAYER_KEY);

  DEFAULT_LAYERS.forEach((layer) => {
    registerOrUpdateLayer(layer);
  });
}

function registerOrUpdateLayer(layer) {
  if (!layer || typeof layer.key !== "string" || !layer.key.trim()) {
    return;
  }

  const existing = state.availableLayers.get(layer.key) || {};
  const next = {
    ...existing,
    key: layer.key,
    name: typeof layer.name === "string" && layer.name.trim() ? layer.name : layer.key,
    kind: typeof layer.kind === "string" && layer.kind.trim() ? layer.kind : "static",
    allow_user_points: Boolean(layer.allow_user_points),
    is_enabled: typeof layer.is_enabled === "boolean" ? layer.is_enabled : true,
    sort_order: Number.isFinite(layer.sort_order) ? Number(layer.sort_order) : 100,
  };
  state.availableLayers.set(layer.key, next);
  ensureLayerGroup(layer.key);

}

function bindLayerMenu() {
  if (!layerToggleButton || !layerMenu) {
    return;
  }

  layerToggleButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const shouldOpen = layerMenu.classList.contains("hidden");
    layerMenu.classList.toggle("hidden", !shouldOpen);
    layerToggleButton.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  });

  layerMenu.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  document.addEventListener("click", () => {
    layerMenu.classList.add("hidden");
    layerToggleButton.setAttribute("aria-expanded", "false");
  });
}

function bindMobilePanel() {
  if (!panel || !mobilePanelToggle || !mobilePanelBackdrop) {
    return;
  }

  mobilePanelToggle.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const shouldOpen = !panel.classList.contains("mobile-open");
    setMobilePanelOpen(shouldOpen);
  });

  mobilePanelBackdrop.addEventListener("click", () => {
    setMobilePanelOpen(false);
  });

  window.addEventListener("resize", () => {
    if (!isMobileViewport()) {
      setMobilePanelOpen(false);
    }
  });
}

function isMobileViewport() {
  return window.matchMedia("(max-width: 860px)").matches;
}

function setMobilePanelOpen(isOpen) {
  if (!panel || !mobilePanelBackdrop || !mobilePanelToggle) {
    return;
  }
  panel.classList.toggle("mobile-open", isOpen && isMobileViewport());
  mobilePanelBackdrop.classList.toggle("active", isOpen && isMobileViewport());
}

function closeMobilePanelIfOpen() {
  if (!panel || !isMobileViewport()) {
    return false;
  }
  if (!panel.classList.contains("mobile-open")) {
    return false;
  }
  setMobilePanelOpen(false);
  return true;
}

function ensureLayerGroup(layerKey) {
  if (!state.layerGroups.has(layerKey)) {
    state.layerGroups.set(layerKey, L.layerGroup());
  }
  if (
    layerKey !== FEELINGS_LAYER_KEY &&
    layerKey !== HEX_OVERLAY_LAYER_KEY &&
    !state.staticLayerMarkers.has(layerKey)
  ) {
    state.staticLayerMarkers.set(layerKey, new Map());
  }
  return state.layerGroups.get(layerKey);
}

function isLayerVisible(layerKey) {
  return state.selectedLayers.has(layerKey);
}

function applyLayerVisibility() {
  state.layerGroups.forEach((group, layerKey) => {
    const shouldBeVisible = isLayerVisible(layerKey);
    const isVisible = map.hasLayer(group);

    if (shouldBeVisible && !isVisible) {
      group.addTo(map);
      return;
    }

    if (!shouldBeVisible && isVisible) {
      map.removeLayer(group);
    }
  });

  if (!isLayerVisible(FEELINGS_LAYER_KEY)) {
    hidePinChoice();
  }

  refreshHexOverlay();
}

function renderLayerFilters() {
  layersList.innerHTML = "";

  const rows = Array.from(state.availableLayers.values())
    .filter((layer) => layer.is_enabled)
    .sort((a, b) => a.sort_order - b.sort_order || a.key.localeCompare(b.key));

  rows.forEach((layer) => {
    const row = document.createElement("label");
    row.className = "layer-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "filter-checkbox";
    checkbox.checked = state.selectedLayers.has(layer.key);

    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedLayers.add(layer.key);
      } else {
        state.selectedLayers.delete(layer.key);
      }
      applyLayerVisibility();
    });

    const text = document.createElement("span");
    text.className = "layer-label";
    text.textContent = layer.name;

    const count = document.createElement("span");
    count.className = "layer-count";
    count.dataset.layerCountKey = layer.key;
    count.textContent = "(0)";

    row.appendChild(checkbox);
    row.appendChild(text);
    row.appendChild(count);
    layersList.appendChild(row);
  });

  updateLayerCounts();
}

function updateLayerCounts() {
  layersList.querySelectorAll("[data-layer-count-key]").forEach((countEl) => {
    const key = countEl.dataset.layerCountKey;
    let count = 0;
    if (key === FEELINGS_LAYER_KEY) {
      count = state.markers.size;
    } else if (key === HEX_OVERLAY_LAYER_KEY) {
      count = state.hexCellCount;
    } else {
      count = state.staticLayerMarkers.get(key)?.size || 0;
    }
    countEl.textContent = `(${count})`;
  });
}

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
  if (!state.pendingLatLng || !type || !state.authUser) return;
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
    updateLayerCounts();
    refreshHexOverlay();
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

function markerIconForPin(pin) {
  if (!pin.is_owner) {
    return markerIconForType(pin.type);
  }

  const baseIcon = markerIconForType(pin.type);
  const iconUrl = baseIcon.options.iconUrl;
  return L.divIcon({
    className: "own-pin-wrap",
    html: `<img class="own-pin-img" src="${iconUrl}" alt="" /><span class="own-pin-dot" aria-hidden="true"></span>`,
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
  });
}

function addPinToMap(pin, openPopup = false) {
  if (!isValidPin(pin)) {
    return;
  }

  if (state.markers.has(pin.id)) {
    return;
  }

  ensureCategory(pin.type);
  const feelingsGroup = ensureLayerGroup(FEELINGS_LAYER_KEY);

  const marker = L.marker([pin.lat, pin.lng], {
    icon: markerIconForPin(pin),
  });

  const shouldBeVisible = matchesFilter(pin.type);
  if (shouldBeVisible) {
    marker.addTo(feelingsGroup);
  }

  state.markers.set(pin.id, { marker, pin });
  marker.bindPopup(buildPopupContent(pin.id));

  if (openPopup && shouldBeVisible && isLayerVisible(FEELINGS_LAYER_KEY)) {
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

  const author = document.createElement("div");
  author.className = "pin-author";
  author.textContent = `Autor: ${pin.created_by_name || "Neznamy"}`;
  container.appendChild(author);

  const createdAt = document.createElement("div");
  createdAt.className = "pin-author";
  createdAt.textContent = `Vytvoreno: ${formatDateTime(pin.created_at)}`;
  container.appendChild(createdAt);

  if (typeof pin.created_from_ip === "string" && pin.created_from_ip.trim()) {
    const sourceIp = document.createElement("div");
    sourceIp.className = "pin-author";
    sourceIp.textContent = `Verejna IP: ${pin.created_from_ip}`;
    container.appendChild(sourceIp);
  }

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

  if (!pin.can_edit) {
    textarea.readOnly = true;
    textarea.classList.add("readonly");
  }

  labelEl.appendChild(textarea);
  form.appendChild(labelEl);

  const syncComment = () => {
    if (!pin.can_edit) return;
    const next = state.markers.get(pinId);
    if (!next) return;

    next.pin.comment = textarea.value.trim();
    scheduleCommentSave(pinId, next.pin.comment);
  };

  form.addEventListener("submit", (e) => e.preventDefault());
  textarea.addEventListener("input", syncComment);
  textarea.addEventListener("blur", syncComment);

  container.appendChild(form);

  if (pin.can_delete) {
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger-btn";
    deleteBtn.textContent = "Smazat pin";
    deleteBtn.addEventListener("click", async () => {
      if (!window.confirm("Opravdu smazat tento pin?")) return;
      try {
        await apiDeletePin(pin.id);
        removePinFromMap(pin.id);
        updateFilterCounts();
        updateLayerCounts();
      } catch (error) {
        showError(error);
      }
    });
    container.appendChild(deleteBtn);
  }

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
      const updated = await apiUpdatePinComment(pinId, comment);
      const target = state.markers.get(pinId);
      if (target) {
        target.pin.comment = updated.comment;
        target.pin.can_edit = updated.can_edit;
      }
    } catch (error) {
      showError(error);
      await loadDataForAllLayers();
    }
  }, 350);

  state.commentSaveTimers.set(pinId, timer);
}

function clearAllMarkers() {
  const feelingsGroup = ensureLayerGroup(FEELINGS_LAYER_KEY);
  state.markers.forEach(({ marker }) => feelingsGroup.removeLayer(marker));
  state.markers.clear();
  state.commentSaveTimers.forEach((timerId) => clearTimeout(timerId));
  state.commentSaveTimers.clear();
  refreshHexOverlay();
}

function removePinFromMap(pinId) {
  const target = state.markers.get(pinId);
  if (!target) return;
  const feelingsGroup = ensureLayerGroup(FEELINGS_LAYER_KEY);
  feelingsGroup.removeLayer(target.marker);
  state.markers.delete(pinId);
  const timer = state.commentSaveTimers.get(pinId);
  if (timer) {
    clearTimeout(timer);
    state.commentSaveTimers.delete(pinId);
  }
  refreshHexOverlay();
}

function matchesFilter(pinType) {
  return state.selectedFilters.has(pinType);
}

function applyFilterToMarkers() {
  const feelingsGroup = ensureLayerGroup(FEELINGS_LAYER_KEY);

  state.markers.forEach(({ marker, pin }) => {
    const shouldBeVisible = matchesFilter(pin.type);
    const isVisible = feelingsGroup.hasLayer(marker);

    if (shouldBeVisible && !isVisible) {
      marker.addTo(feelingsGroup);
      return;
    }

    if (!shouldBeVisible && isVisible) {
      feelingsGroup.removeLayer(marker);
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

function refreshHexOverlay() {
  const hexGroup = ensureLayerGroup(HEX_OVERLAY_LAYER_KEY);
  hexGroup.clearLayers();
  state.hexCellCount = 0;

  if (!isLayerVisible(HEX_OVERLAY_LAYER_KEY)) {
    updateLayerCounts();
    return;
  }

  const north = HUSTOPECE_OVERLAY_BOUNDS.north;
  const south = HUSTOPECE_OVERLAY_BOUNDS.south;
  const west = HUSTOPECE_OVERLAY_BOUNDS.west;
  const east = HUSTOPECE_OVERLAY_BOUNDS.east;

  if (south >= north) {
    updateLayerCounts();
    return;
  }

  const radius = HEX_OVERLAY_STYLE.radiusMeters;
  const latStep = metersToLatDegrees(Math.sqrt(3) * radius);
  const centerLat = (south + north) / 2;
  const lngStep = metersToLngDegrees(1.5 * radius, centerLat);

  if (!Number.isFinite(latStep) || !Number.isFinite(lngStep) || latStep <= 0 || lngStep <= 0) {
    updateLayerCounts();
    return;
  }

  const hexCells = [];
  let col = 0;
  for (let lng = west - lngStep; lng <= east + lngStep; lng += lngStep) {
    const colLatOffset = col % 2 === 0 ? 0 : latStep / 2;
    for (let lat = south - latStep; lat <= north + latStep; lat += latStep) {
      const centerLat = lat + colLatOffset;
      const vertices = buildHexagonLatLng(centerLat, lng, radius);
      const polygon = L.polygon(vertices, {
        color: HEX_NEUTRAL_STYLE.color,
        fillColor: HEX_NEUTRAL_STYLE.fillColor,
        weight: HEX_OVERLAY_STYLE.weight,
        opacity: HEX_NEUTRAL_STYLE.opacity,
        fillOpacity: HEX_NEUTRAL_STYLE.fillOpacity,
        interactive: false,
        bubblingMouseEvents: false,
      });
      polygon.addTo(hexGroup);
      state.hexCellCount += 1;
      hexCells.push({
        polygon,
        vertices,
        score: 0,
      });
    }
    col += 1;
  }

  applyFeelingIntensityToHexCells(hexCells);
  updateLayerCounts();
}

function applyFeelingIntensityToHexCells(hexCells) {
  if (!Array.isArray(hexCells) || hexCells.length === 0) {
    return;
  }

  state.markers.forEach(({ pin }) => {
    const type = pin.type;
    const point = [pin.lat, pin.lng];
    for (const cell of hexCells) {
      if (pointInPolygon(point, cell.vertices)) {
        if (type === "good") {
          cell.goodCount = (cell.goodCount || 0) + 1;
        } else if (type === "bad") {
          cell.badCount = (cell.badCount || 0) + 1;
        } else if (type === "change") {
          cell.changeCount = (cell.changeCount || 0) + 1;
        }
        break;
      }
    }
  });

  let maxPositive = 0;
  let maxNegative = 0;
  let maxChange = 0;
  hexCells.forEach((cell) => {
    const goodCount = cell.goodCount || 0;
    const badCount = cell.badCount || 0;
    const changeCount = cell.changeCount || 0;
    cell.positiveStrength = Math.max(goodCount - badCount, 0);
    cell.negativeStrength = Math.max(badCount - goodCount, 0);
    cell.changeStrength = changeCount;

    maxPositive = Math.max(maxPositive, cell.positiveStrength);
    maxNegative = Math.max(maxNegative, cell.negativeStrength);
    maxChange = Math.max(maxChange, cell.changeStrength);
  });

  hexCells.forEach((cell) => {
    const positiveStrength = cell.positiveStrength || 0;
    const negativeStrength = cell.negativeStrength || 0;
    const changeStrength = cell.changeStrength || 0;

    if (positiveStrength >= negativeStrength && positiveStrength >= changeStrength && positiveStrength > 0 && maxPositive > 0) {
      const t = positiveStrength / maxPositive;
      const boosted = 0.35 + 0.65 * t;
      cell.polygon.setStyle({
        color: interpolateColor("#5f8f6a", "#2e9f50", boosted),
        fillColor: interpolateColor("#bcd9c4", "#2e9f50", boosted),
        opacity: 0.14 + 0.18 * boosted,
        fillOpacity: 0.22 + 0.30 * boosted,
      });
      return;
    }

    if (negativeStrength >= positiveStrength && negativeStrength >= changeStrength && negativeStrength > 0 && maxNegative > 0) {
      const t = negativeStrength / maxNegative;
      cell.polygon.setStyle({
        color: interpolateColor("#b79590", "#c0392b", t),
        fillColor: interpolateColor("#f0d9d6", "#c0392b", t),
        opacity: 0.08 + 0.16 * t,
        fillOpacity: 0.12 + 0.34 * t,
      });
      return;
    }

    if (changeStrength > 0 && maxChange > 0) {
      const t = changeStrength / maxChange;
      cell.polygon.setStyle({
        color: interpolateColor("#c6b576", "#b08b00", t),
        fillColor: interpolateColor("#f6efd1", "#d8b21a", t),
        opacity: 0.08 + 0.16 * t,
        fillOpacity: 0.12 + 0.34 * t,
      });
      return;
    }

    cell.polygon.setStyle({
      color: HEX_NEUTRAL_STYLE.color,
      fillColor: HEX_NEUTRAL_STYLE.fillColor,
      opacity: HEX_NEUTRAL_STYLE.opacity,
      fillOpacity: HEX_NEUTRAL_STYLE.fillOpacity,
    });
  });
}

function pointInPolygon(point, vertices) {
  const y = point[0];
  const x = point[1];
  let inside = false;

  for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i, i += 1) {
    const yi = vertices[i][0];
    const xi = vertices[i][1];
    const yj = vertices[j][0];
    const xj = vertices[j][1];

    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (intersects) {
      inside = !inside;
    }
  }

  return inside;
}

function interpolateColor(startHex, endHex, t) {
  const start = hexToRgb(startHex);
  const end = hexToRgb(endHex);
  const clamped = Math.max(0, Math.min(1, t));
  const r = Math.round(start.r + (end.r - start.r) * clamped);
  const g = Math.round(start.g + (end.g - start.g) * clamped);
  const b = Math.round(start.b + (end.b - start.b) * clamped);
  return `rgb(${r}, ${g}, ${b})`;
}

function hexToRgb(hex) {
  const clean = hex.replace("#", "");
  return {
    r: Number.parseInt(clean.slice(0, 2), 16),
    g: Number.parseInt(clean.slice(2, 4), 16),
    b: Number.parseInt(clean.slice(4, 6), 16),
  };
}

function buildHexagonLatLng(centerLat, centerLng, radiusMeters) {
  const points = [];
  for (let i = 0; i < 6; i += 1) {
    const angleRad = ((60 * i) * Math.PI) / 180;
    const dx = radiusMeters * Math.cos(angleRad);
    const dy = radiusMeters * Math.sin(angleRad);
    points.push([
      centerLat + metersToLatDegrees(dy),
      centerLng + metersToLngDegrees(dx, centerLat),
    ]);
  }
  return points;
}

function metersToLatDegrees(meters) {
  return meters / 111320;
}

function metersToLngDegrees(meters, lat) {
  const cosLat = Math.cos((lat * Math.PI) / 180);
  if (Math.abs(cosLat) < 1e-6) {
    return 0;
  }
  return meters / (111320 * cosLat);
}

function clearAllStaticLayerMarkers() {
  state.staticLayerMarkers.forEach((layerMap, layerKey) => {
    const group = ensureLayerGroup(layerKey);
    layerMap.forEach(({ marker }) => group.removeLayer(marker));
    layerMap.clear();
  });
}

function addStaticPointToLayer(layerKey, point) {
  if (!isValidStaticPoint(point)) {
    return;
  }

  const group = ensureLayerGroup(layerKey);
  const layerMap = state.staticLayerMarkers.get(layerKey);
  if (!layerMap || layerMap.has(point.id)) {
    return;
  }

  const marker = createStaticMarker(layerKey, point);
  marker.addTo(group);
  marker.bindPopup(buildStaticPopupContent(layerKey, point));

  layerMap.set(point.id, { marker, point });
}

function createStaticMarker(layerKey, point) {
  if (layerKey === "city_buildings") {
    return L.circleMarker([point.lat, point.lng], {
      radius: 7,
      color: "#2f5678",
      weight: 2,
      fillColor: "#5f90bb",
      fillOpacity: 0.82,
    });
  }

  return L.circleMarker([point.lat, point.lng], {
    radius: 6,
    color: "#45545f",
    weight: 2,
    fillColor: "#7f95a7",
    fillOpacity: 0.78,
  });
}

function buildStaticPopupContent(layerKey, point) {
  const layer = state.availableLayers.get(layerKey);
  const container = document.createElement("div");

  const title = document.createElement("div");
  title.className = "pin-label neutral";
  title.textContent = point.title || layer?.name || "Bod vrstvy";
  container.appendChild(title);

  if (point.description) {
    const desc = document.createElement("div");
    desc.className = "pin-author";
    desc.textContent = point.description;
    container.appendChild(desc);
  }

  if (point.data && typeof point.data === "object") {
    Object.entries(point.data).forEach(([key, value]) => {
      if (value == null || value === "") return;
      const row = document.createElement("div");
      row.className = "pin-author";
      row.textContent = `${prettifyCategoryLabel(key)}: ${String(value)}`;
      container.appendChild(row);
    });
  }

  return container;
}

async function loadLayersFromServer() {
  try {
    const layers = await apiListLayers();
    layers.forEach((layer) => registerOrUpdateLayer(layer));

    const enabledKeys = new Set(
      Array.from(state.availableLayers.values())
        .filter((layer) => layer.is_enabled)
        .map((layer) => layer.key)
    );
    state.selectedLayers.forEach((key) => {
      if (!enabledKeys.has(key)) {
        state.selectedLayers.delete(key);
      }
    });
  } catch (error) {
    showError(error);
  }
}

async function loadDataForAllLayers() {
  try {
    const pins = await apiListPins();
    clearAllMarkers();
    pins.forEach((pin) => {
      addPinToMap(pin, false);
    });

    clearAllStaticLayerMarkers();
    const staticLayerKeys = Array.from(state.availableLayers.keys()).filter(
      (key) => key !== FEELINGS_LAYER_KEY && key !== HEX_OVERLAY_LAYER_KEY
    );

    const staticResults = await Promise.all(
      staticLayerKeys.map(async (layerKey) => {
        try {
          return {
            layerKey,
            points: await apiListLayerPoints(layerKey),
          };
        } catch {
          return {
            layerKey,
            points: [],
          };
        }
      })
    );

    staticResults.forEach(({ layerKey, points }) => {
      points.forEach((point) => addStaticPointToLayer(layerKey, point));
    });

    applyFilterToMarkers();
    applyLayerVisibility();
    updateFilterCounts();
    updateLayerCounts();
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
    typeof pin?.comment === "string" &&
    typeof pin?.created_at === "string"
  );
}

function isValidStaticPoint(point) {
  return (
    typeof point?.id === "string" &&
    typeof point?.lat === "number" &&
    typeof point?.lng === "number"
  );
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "-";
  }
  return date.toLocaleString("cs-CZ");
}

async function apiRequest(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };
  if (state.authToken) {
    headers["X-Auth-Token"] = state.authToken;
  }

  const response = await fetch(path, {
    ...options,
    headers,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const message = payload?.error || "Server error";
    throw new Error(message);
  }

  return payload;
}

async function apiListPins() {
  const payload = await apiRequest(`${API_BASE}/pins`);
  return Array.isArray(payload.pins) ? payload.pins : [];
}

async function apiListLayers() {
  const payload = await apiRequest(`${API_BASE}/layers`);
  return Array.isArray(payload.layers) ? payload.layers : [];
}

async function apiListLayerPoints(layerKey) {
  const payload = await apiRequest(`${API_BASE}/layers/${encodeURIComponent(layerKey)}/points`);
  return Array.isArray(payload.points) ? payload.points : [];
}

async function apiCreatePin(pin) {
  return apiRequest(`${API_BASE}/pins`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pin),
  });
}

async function apiUpdatePinComment(pinId, comment) {
  return apiRequest(`${API_BASE}/pins/${encodeURIComponent(pinId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment }),
  });
}

async function apiDeleteAllPins() {
  return apiRequest(`${API_BASE}/pins`, { method: "DELETE" });
}

async function apiDeletePin(pinId) {
  return apiRequest(`${API_BASE}/pins/${encodeURIComponent(pinId)}`, {
    method: "DELETE",
  });
}

async function apiLogin(email, name) {
  return apiRequest(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name }),
  });
}

async function apiRegister(email, name) {
  return apiRequest(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name }),
  });
}

async function apiMe() {
  return apiRequest(`${API_BASE}/auth/me`);
}

async function apiLogout() {
  return apiRequest(`${API_BASE}/auth/logout`, { method: "POST" });
}

function showError(error) {
  console.error(error);
  window.alert(error?.message || "Doslo k chybe.");
}
