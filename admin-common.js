(function () {
  const API_BASE = "/api";
  const AUTH_STORAGE_KEY = "pocitovaMapaAuthToken";

  function loadToken() {
    return localStorage.getItem(AUTH_STORAGE_KEY);
  }

  function createClient() {
    const authToken = loadToken();

    async function apiRequest(path, options = {}) {
      const headers = {
        ...(options.headers || {}),
      };
      if (authToken) {
        headers["X-Auth-Token"] = authToken;
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
        throw new Error(payload?.error || "Server error");
      }

      return payload;
    }

    async function requireAdmin(authInfoElement, contentElement) {
      if (!authToken) {
        authInfoElement.textContent =
          "Pro pristup do administrace je nutne prihlaseni jako admin.";
        contentElement.classList.add("hidden");
        return { ok: false, user: null };
      }

      let user = null;
      try {
        const mePayload = await apiRequest(`${API_BASE}/auth/me`);
        user = mePayload.user || null;
      } catch {
        user = null;
      }

      if (!user || user.role !== "admin") {
        authInfoElement.textContent = "Pristup povolen pouze pro administratory.";
        contentElement.classList.add("hidden");
        return { ok: false, user: null };
      }

      authInfoElement.textContent = `Prihlasen: ${user.name} (${user.email})`;
      contentElement.classList.remove("hidden");
      return { ok: true, user };
    }

    return {
      API_BASE,
      apiRequest,
      requireAdmin,
    };
  }

  window.AdminCommon = {
    createClient,
  };
})();
