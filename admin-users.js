const authInfo = document.getElementById("admin-auth-info");
const adminContent = document.getElementById("admin-content");
const usersTableBody = document.getElementById("users-table-body");
const usersNote = document.getElementById("users-note");

const client = window.AdminCommon.createClient();
const state = {
  users: [],
};

initialize();

async function initialize() {
  const auth = await client.requireAdmin(authInfo, adminContent);
  if (!auth.ok) {
    return;
  }
  await loadUsers();
}

function formatDateTime(value) {
  if (!value || typeof value !== "string") return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("cs-CZ");
}

function setUsersNote(message, isError = false) {
  usersNote.textContent = message || "";
  usersNote.style.color = isError ? "#8a2118" : "#1f6f34";
}

async function loadUsers() {
  try {
    const payload = await client.apiRequest(`${client.API_BASE}/admin/users`);
    state.users = Array.isArray(payload.users) ? payload.users : [];
    renderUsersTable();
  } catch (error) {
    authInfo.textContent = error?.message || "Nepodarilo se nacist uzivatele.";
    usersTableBody.innerHTML = '<tr><td colspan="6">Nacitani uzivatelu selhalo.</td></tr>';
  }
}

function renderUsersTable() {
  if (!state.users.length) {
    usersTableBody.innerHTML = '<tr><td colspan="6">Zatim nejsou registrovani zadni uzivatele.</td></tr>';
    return;
  }

  usersTableBody.innerHTML = "";
  state.users.forEach((user) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    nameCell.textContent = user.is_current_user ? `${user.name} (ty)` : user.name;

    const emailCell = document.createElement("td");
    emailCell.textContent = user.email;

    const roleCell = document.createElement("td");
    const roleSelect = document.createElement("select");
    roleSelect.className = "admin-import-input";
    roleSelect.style.minWidth = "140px";
    [
      { value: "admin", label: "admin" },
      { value: "moderator", label: "moderator" },
      { value: "user", label: "user" },
    ].forEach((option) => {
      const opt = document.createElement("option");
      opt.value = option.value;
      opt.textContent = option.label;
      if (user.role === option.value) opt.selected = true;
      roleSelect.appendChild(opt);
    });
    roleCell.appendChild(roleSelect);

    const loginCell = document.createElement("td");
    loginCell.textContent = formatDateTime(user.last_login_at);

    const sessionCell = document.createElement("td");
    sessionCell.textContent = user.has_active_session ? "Aktivni" : "Neaktivni";

    const actionsCell = document.createElement("td");
    actionsCell.style.whiteSpace = "nowrap";

    const saveRoleButton = document.createElement("button");
    saveRoleButton.type = "button";
    saveRoleButton.className = "auth-btn";
    saveRoleButton.textContent = "Ulozit roli";

    const logoutButton = document.createElement("button");
    logoutButton.type = "button";
    logoutButton.className = "danger-btn";
    logoutButton.style.marginTop = "0";
    logoutButton.style.marginLeft = "0.4rem";
    logoutButton.style.width = "auto";
    logoutButton.textContent = "Odhlasit";
    logoutButton.disabled = Boolean(user.is_current_user);
    if (user.is_current_user) {
      logoutButton.title = "Aktualniho uzivatele odhlas cez tlacitko Odhlasit na mape.";
    }

    saveRoleButton.addEventListener("click", async () => {
      const nextRole = roleSelect.value;
      if (nextRole === user.role) {
        setUsersNote(`Uzivatel ${user.email} uz ma roli ${nextRole}.`);
        return;
      }
      saveRoleButton.disabled = true;
      logoutButton.disabled = true;
      setUsersNote("Ukladam roli uzivatele...");
      try {
        await client.apiRequest(`${client.API_BASE}/admin/users/${encodeURIComponent(user.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: nextRole }),
        });
        setUsersNote(`Role uzivatele ${user.email} byla ulozena.`);
        await loadUsers();
      } catch (error) {
        setUsersNote(error?.message || "Ulozeni role selhalo.", true);
      } finally {
        saveRoleButton.disabled = false;
        logoutButton.disabled = Boolean(user.is_current_user);
      }
    });

    logoutButton.addEventListener("click", async () => {
      if (!window.confirm(`Opravdu odhlasit uzivatele ${user.email}?`)) {
        return;
      }
      saveRoleButton.disabled = true;
      logoutButton.disabled = true;
      setUsersNote("Odhlasuji uzivatele...");
      try {
        await client.apiRequest(`${client.API_BASE}/admin/users/${encodeURIComponent(user.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revoke_token: true }),
        });
        setUsersNote(`Uzivatel ${user.email} byl odhlasen.`);
        await loadUsers();
      } catch (error) {
        setUsersNote(error?.message || "Odhlaseni selhalo.", true);
      } finally {
        saveRoleButton.disabled = false;
        logoutButton.disabled = Boolean(user.is_current_user);
      }
    });

    actionsCell.appendChild(saveRoleButton);
    actionsCell.appendChild(logoutButton);

    row.appendChild(nameCell);
    row.appendChild(emailCell);
    row.appendChild(roleCell);
    row.appendChild(loginCell);
    row.appendChild(sessionCell);
    row.appendChild(actionsCell);
    usersTableBody.appendChild(row);
  });
}
