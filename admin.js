const authInfo = document.getElementById("admin-auth-info");
const adminContent = document.getElementById("admin-content");

const client = window.AdminCommon.createClient();

initialize();

async function initialize() {
  await client.requireAdmin(authInfo, adminContent);
}
