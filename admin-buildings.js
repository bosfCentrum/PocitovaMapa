const authInfo = document.getElementById("admin-auth-info");
const adminContent = document.getElementById("admin-content");
const importFileForm = document.getElementById("parcel-import-file-form");
const sourceFileInput = document.getElementById("parcel-source-file");
const importFileNote = document.getElementById("parcel-import-file-note");
const deleteAllButton = document.getElementById("parcel-delete-all-btn");
const refreshCoordsButton = document.getElementById("parcel-refresh-coords-btn");
const actionsNote = document.getElementById("parcel-actions-note");
const parcelsTableBody = document.getElementById("parcels-table-body");

const client = window.AdminCommon.createClient();
const state = {
  parcels: [],
};

initialize();

async function initialize() {
  const auth = await client.requireAdmin(authInfo, adminContent);
  if (!auth.ok) {
    return;
  }
  bindImportFileForm();
  bindDeleteAllParcels();
  bindRefreshCoordinates();
  await loadParcels();
}

function bindImportFileForm() {
  importFileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = sourceFileInput.files && sourceFileInput.files[0];
    if (!file) {
      return;
    }

    const submitButton = importFileForm.querySelector("button[type='submit']");
    submitButton.disabled = true;
    importFileNote.textContent = "Nahravam a zpracuji HTML...";
    importFileNote.style.color = "#5d5d5d";
    try {
      const htmlContent = await file.text();
      const result = await client.apiRequest(`${client.API_BASE}/admin/buildings/parcels/import-html`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          html: htmlContent,
          source_url: "https://nahlizenidokn.cuzk.gov.cz/",
        }),
      });
      importFileNote.textContent = `Import hotov: ${result.imported} nalezeno, ${result.inserted} novych, ${result.updated} aktualizovanych, ${result.detail_failures || 0} detailu se nepodarilo nacist, ${result.coordinate_failures || 0} souradnic se nepodarilo doplnit.`;
      importFileNote.style.color = "#1f6f34";
      sourceFileInput.value = "";
      await loadParcels();
    } catch (error) {
      importFileNote.textContent = error?.message || "Import selhal.";
      importFileNote.style.color = "#8a2118";
    } finally {
      submitButton.disabled = false;
    }
  });
}

function bindDeleteAllParcels() {
  deleteAllButton.addEventListener("click", async () => {
    if (!window.confirm("Opravdu smazat vsechny importovane pozemky?")) {
      return;
    }
    deleteAllButton.disabled = true;
    actionsNote.textContent = "Mazani probiha...";
    actionsNote.style.color = "#5d5d5d";
    try {
      const result = await client.apiRequest(`${client.API_BASE}/admin/buildings/parcels`, {
        method: "DELETE",
      });
      actionsNote.textContent = `Smazano zaznamu: ${result.deleted}`;
      actionsNote.style.color = "#1f6f34";
      await loadParcels();
    } catch (error) {
      actionsNote.textContent = error?.message || "Mazani selhalo.";
      actionsNote.style.color = "#8a2118";
    } finally {
      deleteAllButton.disabled = false;
    }
  });
}

function bindRefreshCoordinates() {
  refreshCoordsButton.addEventListener("click", async () => {
    refreshCoordsButton.disabled = true;
    actionsNote.textContent = "Doplnuji souradnice...";
    actionsNote.style.color = "#5d5d5d";
    try {
      const result = await client.apiRequest(
        `${client.API_BASE}/admin/buildings/parcels/refresh-coordinates`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
      );
      actionsNote.textContent = `Souradnice doplneny: ${result.updated}, selhalo: ${result.failed}, preskoceno: ${result.skipped}.`;
      actionsNote.style.color = "#1f6f34";
      await loadParcels();
    } catch (error) {
      actionsNote.textContent = error?.message || "Doplneni souradnic selhalo.";
      actionsNote.style.color = "#8a2118";
    } finally {
      refreshCoordsButton.disabled = false;
    }
  });
}

async function loadParcels() {
  try {
    const payload = await client.apiRequest(`${client.API_BASE}/admin/buildings/parcels`);
    state.parcels = Array.isArray(payload.parcels) ? payload.parcels : [];
    renderParcelsTable();
  } catch (error) {
    authInfo.textContent = error?.message || "Nepodarilo se nacist pozemky.";
    parcelsTableBody.innerHTML = '<tr><td colspan="5">Nacitani selhalo.</td></tr>';
  }
}

function renderParcelsTable() {
  if (!state.parcels.length) {
    parcelsTableBody.innerHTML = '<tr><td colspan="5">Zatim nejsou ulozene zadne pozemky.</td></tr>';
    return;
  }

  parcelsTableBody.innerHTML = "";
  state.parcels.forEach((parcel) => {
    const row = document.createElement("tr");

    const labelCell = document.createElement("td");
    const label = document.createElement("div");
    label.className = "building-title";
    label.textContent = parcel.parcel_label || "Pozemek";
    labelCell.appendChild(label);

    const objectCell = document.createElement("td");
    if (parcel.building_object_url) {
      const objectLink = document.createElement("a");
      objectLink.href = parcel.building_object_url;
      objectLink.target = "_blank";
      objectLink.rel = "noopener noreferrer";
      objectLink.textContent = "Otevrit objekt";
      objectLink.title = parcel.building_object_url;
      objectCell.appendChild(objectLink);
    } else {
      objectCell.textContent = "-";
    }

    const typeCell = document.createElement("td");
    typeCell.textContent = parcel.object_type || "-";

    const addressCell = document.createElement("td");
    addressCell.textContent = parcel.address || "-";

    const coordsCell = document.createElement("td");
    if (typeof parcel.lat === "number" && typeof parcel.lng === "number") {
      const latText = parcel.lat.toLocaleString("cs-CZ", {
        minimumFractionDigits: 6,
        maximumFractionDigits: 6,
      });
      const lngText = parcel.lng.toLocaleString("cs-CZ", {
        minimumFractionDigits: 6,
        maximumFractionDigits: 6,
      });
      coordsCell.textContent = `Lat: ${latText} Lng: ${lngText}`;
    } else {
      coordsCell.textContent = "-";
    }

    row.appendChild(labelCell);
    row.appendChild(objectCell);
    row.appendChild(typeCell);
    row.appendChild(addressCell);
    row.appendChild(coordsCell);
    parcelsTableBody.appendChild(row);
  });
}
