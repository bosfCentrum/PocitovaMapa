const API_BASE = "/api";
const AUTH_STORAGE_KEY = "pocitovaMapaAuthToken";

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
const filtersList = document.getElementById("filters-list");
const authForm = document.getElementById("auth-form");
const authEmailInput = document.getElementById("auth-email");
const authNameInput = document.getElementById("auth-name");
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
  } catch (error) {
    showError(error);
  }
});

map.on("click", (event) => {
  if (Date.now() < state.ignoreMapClicksUntil) {
    return;
  }
  if (!state.authUser) {
    window.alert("Pro pridani pinu se prihlas (email + jmeno).");
    return;
  }
  state.pendingLatLng = event.latlng;
  showPinChoice();
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
    await loadPinsFromServer();
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
    loadPinsFromServer();
  }
});

bindChoicePress(choiceCancel, hidePinChoice);
bindChoiceOptionPress();
registerDefaultCategories();
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

  await loadPinsFromServer();
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

  const marker = L.marker([pin.lat, pin.lng], {
    icon: markerIconForPin(pin),
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

  const author = document.createElement("div");
  author.className = "pin-author";
  author.textContent = `Autor: ${pin.created_by_name || "Neznamy"}`;
  container.appendChild(author);

  const createdAt = document.createElement("div");
  createdAt.className = "pin-author";
  createdAt.textContent = `Vytvoreno: ${formatDateTime(pin.created_at)}`;
  container.appendChild(createdAt);

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
      await loadPinsFromServer();
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

function removePinFromMap(pinId) {
  const target = state.markers.get(pinId);
  if (!target) return;
  map.removeLayer(target.marker);
  state.markers.delete(pinId);
  const timer = state.commentSaveTimers.get(pinId);
  if (timer) {
    clearTimeout(timer);
    state.commentSaveTimers.delete(pinId);
  }
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
    typeof pin?.comment === "string" &&
    typeof pin?.created_at === "string"
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
