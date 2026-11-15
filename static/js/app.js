/**
 * Priority-Based Data Storage & Telemetry Management Console
 * Full AWS-Style Single-Page Application (SPA) with RBAC and SSE Live Streaming.
 */

const state = {
  token: localStorage.getItem("auth_token") || null,
  user: null,
  activeTab: "all",
  recordOffset: 0,
  selectedRecordIds: new Set(),
  selectedUsernames: new Set(),
  systemStatus: null,
  records: [],
  users: [],
  groups: [],
  rules: [],
  events: []
};

// -----------------------------------------------------------------------------
// Debounce utility — collapses rapid repeated calls into one deferred call
// -----------------------------------------------------------------------------
function debounce(fn, delayMs) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delayMs);
  };
}
const debouncedLoadStatus = debounce(loadStatus, 800);

// -----------------------------------------------------------------------------
// API Helper with JWT Authorization
// -----------------------------------------------------------------------------
async function api(endpoint, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.token ? { "Authorization": `Bearer ${state.token}` } : {}),
    ...options.headers
  };

  const response = await fetch(endpoint, { ...options, headers });
  if (response.status === 401) {
    logout();
    openModal("loginModal");
    throw new Error("Session expired. Please log in again.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP error ${response.status}`);
  }
  return data;
}

function showToast(message, isError = false) {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.style.borderColor = isError ? "var(--danger)" : "var(--success)";
  toast.innerHTML = `<span>${isError ? "⚠️" : "✓"}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

// -----------------------------------------------------------------------------
// Role-Based Panel Visibility
// -----------------------------------------------------------------------------
function applyRoleVisibility(role) {
  const tabRules = {
    "rules":    ["SUPERADMIN", "ADMIN", "OPERATOR"],
    "hardware": ["SUPERADMIN", "ADMIN", "OPERATOR"],
    "iam":      ["SUPERADMIN", "ADMIN"],
  };

  document.querySelectorAll(".nav-item[data-tab]").forEach(item => {
    const tab = item.getAttribute("data-tab");
    if (tab === "all") return;
    const allowed = tabRules[tab];
    if (allowed && !allowed.includes(role)) {
      item.style.display = "none";
      // If user was currently on this hidden tab, navigate back to monolithic view
      if (state.activeTab === tab) {
        switchTab("all");
      }
    } else {
      item.style.display = "";
    }
  });

  // If in monolithic all-in-one console mode, enforce section visibility
  if (state.activeTab === "all") {
    Object.entries(tabRules).forEach(([tab, allowedRoles]) => {
      const viewEl = document.getElementById(`${tab}View`);
      if (viewEl) {
        viewEl.style.display = allowedRoles.includes(role) ? "block" : "none";
      }
    });
  }

  // Hide action buttons based on role
  if (role === "AUDITOR") {
    // Hide all write buttons in Records Explorer
    document.querySelectorAll(".auditor-hidden").forEach(el => el.style.display = "none");
  } else {
    document.querySelectorAll(".auditor-hidden").forEach(el => {
      // Don't re-show loadMoreBtn if it was hidden by pagination logic
      if (el.id !== "loadMoreBtn") {
        el.style.display = "";
      }
    });
  }
}

// -----------------------------------------------------------------------------
// Authentication & IAM Session
// -----------------------------------------------------------------------------
async function checkAuthSession() {
  if (!state.token) {
    openModal("loginModal");
    return;
  }
  try {
    const res = await api("/api/auth/me");
    state.user = res.user;
    updateUserBadge();
    applyRoleVisibility(state.user.role);
    closeModal("loginModal");
    loadAllData();
  } catch (e) {
    logout();
    openModal("loginModal");
  }
}

function updateUserBadge() {
  const nameEl = document.getElementById("navUserName");
  const roleEl = document.getElementById("navUserRole");
  if (state.user) {
    nameEl.textContent = state.user.username || state.user.sub || "User";
    roleEl.textContent = state.user.role || "GUEST";
  } else {
    nameEl.textContent = "Not Logged In";
    roleEl.textContent = "GUEST";
  }
}

async function login(username, password) {
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    });
    state.token = res.token;
    localStorage.setItem("auth_token", res.token);
    state.user = res.user;
    updateUserBadge();
    applyRoleVisibility(res.user.role);
    closeModal("loginModal");
    showToast(`Logged in successfully as ${res.user.username} (${res.user.role})`);
    window.telemetrySSE.connect();
    loadAllData();
  } catch (err) {
    showToast(err.message, true);
  }
}

function showSignupForm() {
  const sec = document.getElementById("signupSection");
  if (sec) sec.style.display = "block";
}

async function signup(username, password) {
  try {
    const res = await fetch("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Signup failed");
    state.token = data.token;
    localStorage.setItem("auth_token", data.token);
    state.user = data.user;
    updateUserBadge();
    applyRoleVisibility(data.user.role);
    closeModal("loginModal");
    showToast(`Account created! Logged in as ${data.user.username} (${data.user.role})`);
    window.telemetrySSE.connect();
    loadAllData();
  } catch (err) {
    showToast(err.message, true);
  }
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("auth_token");
  window.telemetrySSE.disconnect();
  updateUserBadge();
  applyRoleVisibility("GUEST");
  openModal("loginModal");
}

// -----------------------------------------------------------------------------
// Tab Navigation
// -----------------------------------------------------------------------------
function switchTab(tabId) {
  document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));

  const activeNavItem = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
  if (activeNavItem) activeNavItem.classList.add("active");

  state.activeTab = tabId;

  if (tabId === "all") {
    // Monolithic All-In-One: display all operational views together
    document.querySelectorAll(".tab-view").forEach(v => v.style.display = "flex");
    if (state.user) applyRoleVisibility(state.user.role);
    loadAllData();
  } else {
    document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");
    const targetView = document.getElementById(`${tabId}View`);
    if (targetView) targetView.style.display = "flex";

    if (tabId === "records") {
      state.recordOffset = 0;
      loadRecords();
    }
    if (tabId === "iam") {
      loadUsers();
      loadGroups();
    }
    if (tabId === "rules") loadRules();
    if (tabId === "hardware") loadPorts();
    if (tabId === "dashboard") loadStatus();
  }
}

function setupTabs() {
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", () => {
      const tabId = item.getAttribute("data-tab");
      switchTab(tabId);
    });
  });
}

// -----------------------------------------------------------------------------
// Modals Manager
// -----------------------------------------------------------------------------
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("open");
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("open");
}

// -----------------------------------------------------------------------------
// Data Fetching & Rendering
// -----------------------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/system/status");
    state.systemStatus = s;
    document.getElementById("statStorageEngine").textContent = (s.storage_engine || "JSON").toUpperCase();
    document.getElementById("statActiveWorkers").textContent = s.active_workers;
    document.getElementById("statQueueDepth").textContent = s.queue_depth;
    document.getElementById("statTotalRecords").textContent = s.total_records;

    const pd = s.priority_distribution || {};
    document.getElementById("countHigh").textContent = pd.HIGH || 0;
    document.getElementById("countMed").textContent = pd.MEDIUM || 0;
    document.getElementById("countLow").textContent = pd.LOW || 0;

    const vBtn = document.getElementById("toggleVirtualBtn");
    if (vBtn) {
      vBtn.textContent = s.virtual_hardware_active ? "Stop Virtual Telemetry" : "Start Virtual Telemetry";
      vBtn.className = s.virtual_hardware_active ? "btn btn-danger btn-sm" : "btn btn-primary btn-sm";
    }
  } catch (e) {}
}

async function loadRecords(append = false) {
  const statusFilter = document.getElementById("filterStatus")?.value || "";
  const priorityFilter = document.getElementById("filterPriority")?.value || "";
  const searchAll = document.getElementById("searchAllCheck")?.checked;
  const limit = searchAll ? 5000 : (statusFilter || priorityFilter ? 500 : 100);

  if (!append) {
    state.recordOffset = 0;
  }
  const offset = state.recordOffset || 0;

  try {
    const res = await api(`/api/records?limit=${limit}&offset=${offset}&status=${statusFilter}&priority=${priorityFilter}`);
    if (append) {
      state.records = [...state.records, ...(res.records || [])];
    } else {
      state.records = res.records || [];
    }
    renderRecordsTable();

    const total = (typeof res.total === "number") ? res.total : state.records.length;
    updatePaginationFooter(total, state.records.length, searchAll);
  } catch (e) {
    showToast(e.message, true);
  }
}

function loadMoreRecords() {
  state.recordOffset = (state.recordOffset || 0) + 100;
  loadRecords(true);
}

function updatePaginationFooter(total, currentCount, searchAll) {
  const info = document.getElementById("paginationInfo");
  const loadMoreBtn = document.getElementById("loadMoreBtn");
  if (info) {
    info.textContent = `Showing ${currentCount} of ${total} total records`;
  }
  if (loadMoreBtn) {
    if (!searchAll && currentCount < total && state.user?.role !== "AUDITOR") {
      loadMoreBtn.style.display = "inline-block";
    } else {
      loadMoreBtn.style.display = "none";
    }
  }
}

function renderRecordsTable() {
  const tbody = document.getElementById("recordsTbody");
  if (!tbody) return;

  if (state.records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color: var(--text-muted); padding: 2rem;">No telemetry records found. Start virtual hardware or ingest a demo record.</td></tr>`;
    return;
  }

  const isAuditor = state.user?.role === "AUDITOR";

  tbody.innerHTML = state.records.map(r => {
    const isChecked = state.selectedRecordIds.has(r.data_id);
    const pClass = r.priority === "HIGH" ? "badge-high" : (r.priority === "MEDIUM" ? "badge-medium" : "badge-low");
    const sClass = `badge-${(r.status || 'queued').toLowerCase().replace('_', '-')}`;
    
    return `
      <tr>
        <td><input type="checkbox" class="record-cb" data-id="${r.data_id}" ${isChecked ? "checked" : ""}></td>
        <td><strong>#${r.data_id}</strong></td>
        <td>Module ${r.module_id}</td>
        <td><code>${JSON.stringify(r.values)}</code></td>
        <td><span class="badge ${pClass}">${r.priority}</span></td>
        <td><span class="badge ${sClass}">${r.status}</span></td>
        <td>${r.attempt_count || 0}</td>
        <td style="font-size:11px; color:var(--text-muted);">${r.received_at ? r.received_at.split('T')[1].slice(0,8) : '—'}</td>
        <td>
          ${!isAuditor ? `
            <button class="btn btn-danger btn-sm auditor-hidden" onclick="deleteSingleRecord('${r.data_id}')">Del</button>
          ` : `<span style="font-size:11px; color:var(--text-muted);">Read-Only</span>`}
        </td>
      </tr>
    `;
  }).join("");

  // Attach Checkbox Handlers
  document.querySelectorAll(".record-cb").forEach(cb => {
    cb.addEventListener("change", (e) => {
      const id = e.target.getAttribute("data-id");
      if (e.target.checked) state.selectedRecordIds.add(id);
      else state.selectedRecordIds.delete(id);
      updateBatchToolbar();
    });
  });

  if (isAuditor) {
    document.querySelectorAll(".auditor-hidden").forEach(el => el.style.display = "none");
  }
}

function updateBatchToolbar() {
  const count = state.selectedRecordIds.size;
  const countEl = document.getElementById("selectedCountBadge");
  if (countEl) countEl.textContent = `${count} selected`;
}

// -----------------------------------------------------------------------------
// Single, Batch & Bulk Record Actions
// -----------------------------------------------------------------------------
async function deleteSingleRecord(id) {
  if (!confirm(`Delete record #${id}?`)) return;
  try {
    await api(`/api/records/${id}`, { method: "DELETE" });
    showToast(`Record #${id} deleted`);
    loadRecords();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function batchDeleteSelected() {
  const ids = Array.from(state.selectedRecordIds);
  if (ids.length === 0) return alert("Select at least one record to delete.");
  if (!confirm(`Batch delete ${ids.length} selected record(s)?`)) return;

  try {
    const res = await api("/api/records/batch-delete", {
      method: "POST",
      body: JSON.stringify({ data_ids: ids })
    });
    showToast(res.message);
    state.selectedRecordIds.clear();
    updateBatchToolbar();
    loadRecords();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function batchChangePriority(newPriority) {
  const ids = Array.from(state.selectedRecordIds);
  if (ids.length === 0) return alert("Select at least one record.");

  try {
    const res = await api("/api/records/batch-priority", {
      method: "POST",
      body: JSON.stringify({ data_ids: ids, priority: newPriority })
    });
    showToast(res.message);
    loadRecords();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function batchRequeueSelected() {
  const ids = Array.from(state.selectedRecordIds);
  if (ids.length === 0) return alert("Select at least one record.");

  try {
    const res = await api("/api/records/batch-requeue", {
      method: "POST",
      body: JSON.stringify({ data_ids: ids })
    });
    showToast(res.message);
    loadRecords();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function purgeRecords(status = "") {
  const label = status ? `all ${status}` : "ALL";
  if (!confirm(`Are you sure you want to PURGE ${label} records? This cannot be undone.`)) return;

  try {
    const url = status ? `/api/records/purge?status=${status}` : "/api/records/purge";
    const res = await api(url, { method: "POST" });
    showToast(res.message);
    loadRecords();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function downloadExport(format = "json") {
  try {
    showToast(`Preparing ${format.toUpperCase()} database export...`);
    const tokenParam = state.token ? `?token=${encodeURIComponent(state.token)}` : "";
    const formatParam = format === "zip" ? (tokenParam ? "&format=zip" : "?format=zip") : "";
    const url = `/api/records/export${tokenParam}${formatParam}`;
    const headers = state.token ? { "Authorization": `Bearer ${state.token}` } : {};

    const res = await fetch(url, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}: ${res.statusText}`);
    }
    const blob = await res.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = format === "zip" ? "priority_storage_export.zip" : "telemetry_full_export.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(downloadUrl);
    showToast(`✅ Downloaded ${format.toUpperCase()} export successfully`);
  } catch (err) {
    showToast(`Export error: ${err.message}`, true);
  }
}

// -----------------------------------------------------------------------------
// IAM User Management Actions
// -----------------------------------------------------------------------------
async function loadUsers() {
  try {
    const res = await api("/api/users");
    state.users = res.users;
    renderUsersTable();
  } catch (e) {
    showToast(e.message, true);
  }
}

function renderUsersTable() {
  const tbody = document.getElementById("usersTbody");
  if (!tbody) return;

  tbody.innerHTML = state.users.map(u => `
    <tr>
      <td><strong>${u.username}</strong></td>
      <td><span class="role-tag">${u.role}</span></td>
      <td><span class="badge ${u.is_active ? 'badge-completed' : 'badge-failed'}">${u.is_active ? 'ACTIVE' : 'INACTIVE'}</span></td>
      <td style="font-size:11px; color:var(--text-muted);">${u.created_at ? u.created_at.split('T')[0] : '—'}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="toggleUserPerms('${u.username}')">⚙ Permissions</button>
        ${u.username !== "admin" ? `
          <button class="btn btn-danger btn-sm" onclick="deleteUser('${u.username}')">Revoke User</button>
        ` : `<span style="font-size:11px; color:var(--text-muted); margin-left:4px;">Protected</span>`}
      </td>
    </tr>
    <tr id="perms-row-${u.username}" style="display:none">
      <td colspan="5">
        <div style="padding:10px; background:var(--panel-bg); border-radius:4px; font-size:12px; border:1px solid var(--border-color); margin: 4px 0;">
          <div><strong>Effective Permissions:</strong> <span id="perms-list-${u.username}">Loading...</span></div>
          <div style="margin-top:8px; display:flex; gap:8px; align-items:center;">
            <input type="text" id="grantPerm-${u.username}" class="form-control" placeholder="e.g. records:create" style="width:200px; font-size:12px; padding:4px 8px;">
            <button class="btn btn-primary btn-sm" onclick="grantPerm('${u.username}')">Grant</button>
            <button class="btn btn-danger btn-sm" onclick="revokePerm('${u.username}')">Revoke</button>
          </div>
        </div>
      </td>
    </tr>
  `).join("");
}

async function toggleUserPerms(username) {
  const row = document.getElementById(`perms-row-${username}`);
  if (!row) return;
  if (row.style.display === "none") {
    row.style.display = "";
    const listEl = document.getElementById(`perms-list-${username}`);
    if (listEl) listEl.textContent = "Loading...";
    try {
      const res = await api(`/api/users/${username}/permissions`);
      const perms = res.effective_permissions || res.permissions || [];
      if (listEl) {
        listEl.textContent = perms.length > 0 ? perms.join(", ") : "None";
      }
    } catch (e) {
      if (listEl) listEl.textContent = "Error loading permissions";
    }
  } else {
    row.style.display = "none";
  }
}

async function grantPerm(username) {
  const input = document.getElementById(`grantPerm-${username}`);
  const perm = input?.value.trim();
  if (!perm) return;
  try {
    await api(`/api/users/${username}/permissions/grant`, {
      method: "POST",
      body: JSON.stringify({ permission: perm })
    });
    showToast(`Granted '${perm}' to ${username}`);
    input.value = "";
    // Refresh displayed permissions
    const res = await api(`/api/users/${username}/permissions`);
    const listEl = document.getElementById(`perms-list-${username}`);
    if (listEl) {
      const perms = res.effective_permissions || res.permissions || [];
      listEl.textContent = perms.length > 0 ? perms.join(", ") : "None";
    }
  } catch (e) {
    showToast(e.message, true);
  }
}

async function revokePerm(username) {
  const input = document.getElementById(`grantPerm-${username}`);
  const perm = input?.value.trim();
  if (!perm) return;
  try {
    await api(`/api/users/${username}/permissions/revoke`, {
      method: "POST",
      body: JSON.stringify({ permission: perm })
    });
    showToast(`Revoked '${perm}' from ${username}`);
    input.value = "";
    // Refresh displayed permissions
    const res = await api(`/api/users/${username}/permissions`);
    const listEl = document.getElementById(`perms-list-${username}`);
    if (listEl) {
      const perms = res.effective_permissions || res.permissions || [];
      listEl.textContent = perms.length > 0 ? perms.join(", ") : "None";
    }
  } catch (e) {
    showToast(e.message, true);
  }
}

async function createUser(username, password, role) {
  try {
    const res = await api("/api/users", {
      method: "POST",
      body: JSON.stringify({ username, password, role })
    });
    showToast(res.message);
    closeModal("createUserModal");
    loadUsers();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function deleteUser(username) {
  if (!confirm(`Revoke and delete user '${username}'?`)) return;
  try {
    const res = await api(`/api/users/${username}`, { method: "DELETE" });
    showToast(res.message);
    loadUsers();
  } catch (e) {
    showToast(e.message, true);
  }
}

// -----------------------------------------------------------------------------
// User Groups Management Actions
// -----------------------------------------------------------------------------
async function loadGroups() {
  try {
    const res = await api("/api/groups");
    const container = document.getElementById("groupsContainer");
    if (!container) return;
    const groups = res.groups || [];
    state.groups = groups;
    if (groups.length === 0) {
      container.innerHTML = "<div style='color:var(--text-muted); padding:0.5rem 0;'>No groups created yet.</div>";
      return;
    }
    container.innerHTML = groups.map(g => `
      <div style="display:flex; align-items:center; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border-color);">
        <div>
          <strong>${g.group_name}</strong>
          <span style="color:var(--text-muted); font-size:11px; margin-left:8px;">${(g.permissions || []).length} permissions: [${(g.permissions || []).join(", ")}]</span>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteGroup('${g.group_name}')">Delete</button>
      </div>
    `).join("");
  } catch (e) {
    const container = document.getElementById("groupsContainer");
    if (container) container.innerHTML = `<div style="color:var(--text-muted)">Failed to load groups.</div>`;
  }
}

async function createGroup(name, permissionsStr) {
  try {
    const permissions = permissionsStr.split(",").map(p => p.trim()).filter(Boolean);
    const res = await api("/api/groups", {
      method: "POST",
      body: JSON.stringify({ group_name: name, permissions })
    });
    showToast(res.message || `Group '${name}' created`);
    closeModal("createGroupModal");
    loadGroups();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function deleteGroup(name) {
  if (!confirm(`Delete group '${name}'?`)) return;
  try {
    const res = await api(`/api/groups/${name}`, { method: "DELETE" });
    showToast(res.message || `Group '${name}' deleted`);
    loadGroups();
  } catch (e) {
    showToast(e.message, true);
  }
}

// -----------------------------------------------------------------------------
// Priority Rules Management
// -----------------------------------------------------------------------------
async function loadRules() {
  try {
    const res = await api("/api/rules");
    state.rules = res.rules;
    renderRulesTable();
  } catch (e) {
    showToast(e.message, true);
  }
}

function renderRulesTable() {
  const tbody = document.getElementById("rulesTbody");
  if (!tbody) return;

  tbody.innerHTML = state.rules.map(r => {
    const pClass = r.priority === "HIGH" ? "badge-high" : (r.priority === "MEDIUM" ? "badge-medium" : "badge-low");
    return `
      <tr>
        <td><strong>${r.rule_id}</strong></td>
        <td>Module ${r.module_id}</td>
        <td>Index ${r.sensor_index} (${r.sensor_name || '—'})</td>
        <td><code>${r.operator} ${r.threshold}</code></td>
        <td><span class="badge ${pClass}">${r.priority}</span></td>
        <td style="font-size:12px; color:var(--text-muted);">${r.description || '—'}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="deleteRule('${r.rule_id}')">Delete</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function saveRule(rule) {
  try {
    const res = await api("/api/rules", {
      method: "POST",
      body: JSON.stringify(rule)
    });
    showToast(res.message);
    closeModal("createRuleModal");
    loadRules();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function deleteRule(id) {
  if (!confirm(`Delete rule '${id}'?`)) return;
  try {
    const res = await api(`/api/rules/${id}`, { method: "DELETE" });
    showToast(res.message);
    loadRules();
  } catch (e) {
    showToast(e.message, true);
  }
}

// -----------------------------------------------------------------------------
// Hardware & Ports Management
// -----------------------------------------------------------------------------
async function loadPorts() {
  try {
    const res = await api("/api/ports");
    const container = document.getElementById("portListContainer");
    if (!container) return;

    if (res.ports.length === 0) {
      container.innerHTML = `<div style="color:var(--text-muted); font-size:13px; padding: 1rem 0;">No physical COM ports detected. The Sandboxed Virtual Generator can be used for zero-risk software simulation.</div>`;
    } else {
      container.innerHTML = res.ports.map(p => {
        const isConnected = res.connected.includes(p.device);
        const isSelected = res.selected.includes(p.device);
        return `
          <label style="display:flex; align-items:center; gap:10px; margin-bottom:8px; font-size:13px;">
            <input type="checkbox" class="port-cb" value="${p.device}" ${isSelected ? "checked" : ""}>
            <span><strong>${p.device}</strong> (${p.description})</span>
            <span class="badge ${isConnected ? 'badge-completed' : 'badge-pending'}">${isConnected ? 'CONNECTED' : 'DISCONNECTED'}</span>
          </label>
        `;
      }).join("");
    }
  } catch (e) {
    showToast(e.message, true);
  }
}

async function toggleVirtualHardware() {
  try {
    const res = await api("/api/virtual-hardware/toggle", { method: "POST" });
    showToast(`Virtual Hardware ${res.virtual_hardware_active ? 'Activated' : 'Deactivated'}`);
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function connectSelectedPorts() {
  const checked = [...document.querySelectorAll(".port-cb:checked")].map(cb => cb.value);
  if (checked.length === 0) {
    showToast("No ports selected. Check at least one port checkbox first.", true);
    return;
  }
  try {
    const res = await api("/api/ports/connect", {
      method: "POST",
      body: JSON.stringify({ ports: checked })
    });
    const connected = res.connected || [];
    const errors = res.errors || [];
    if (connected.length > 0) showToast(`✅ Connected: ${connected.join(", ")}`);
    if (errors.length > 0) showToast(`⚠ Port errors: ${errors.join("; ")}`, true);
    loadPorts();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

async function disconnectAllPorts() {
  try {
    await api("/api/ports/disconnect", { method: "POST" });
    showToast("All COM ports disconnected.");
    loadPorts();
    loadStatus();
  } catch (e) {
    showToast(e.message, true);
  }
}

// -----------------------------------------------------------------------------
// Real-Time Server-Sent Events (SSE) Listeners
// -----------------------------------------------------------------------------
function setupSSEListeners() {
  const sse = window.telemetrySSE;

  sse.on("connection_change", (data) => {
    const dot = document.getElementById("wsDot");
    const text = document.getElementById("wsText");
    if (data.connected) {
      dot.className = "status-dot active";
      text.textContent = "LIVE SSE CONNECTED";
    } else {
      dot.className = "status-dot";
      text.textContent = "SSE RECONNECTING...";
    }
  });

  sse.on("connected", (data) => {
    appendEventFeed("SSE_INIT", `Connected to SSE stream (Workers: ${data.workers}, Queue: ${data.queue_depth})`);
    debouncedLoadStatus();
  });

  sse.on("record_ingested", (data) => {
    appendEventFeed("INGESTION", `Record #${data.data_id} (Mod ${data.module_id}) ingested with ${data.priority} priority`);
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("record_completed", (data) => {
    appendEventFeed("COMPLETED", `Record #${data.data_id} processed (Attempt ${data.attempt || 1})`);
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("record_failed", (data) => {
    appendEventFeed("FAILED", `Record #${data.data_id} permanently failed: ${data.error}`);
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("record_updated", () => {
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("record_deleted", () => {
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("records_batch_deleted", () => {
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("records_priority_changed", () => {
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("records_purged", () => {
    debouncedLoadStatus();
    if (state.activeTab === "records") loadRecords();
  });

  sse.on("rules_updated", () => {
    if (state.activeTab === "rules") loadRules();
  });

  sse.on("system_event", (data) => {
    appendEventFeed(data.type || "SYSTEM", data.message || data.reason || JSON.stringify(data));
    debouncedLoadStatus();
  });
}

function appendEventFeed(type, message) {
  const feed = document.getElementById("liveEventFeed");
  if (!feed) return;

  const now = new Date().toTimeString().split(' ')[0];
  const item = document.createElement("div");
  item.className = "feed-item";
  item.innerHTML = `
    <span class="feed-time">${now}</span>
    <span class="feed-type" style="color:var(--aws-orange);">[${type}]</span>
    <span class="feed-msg">${message}</span>
  `;
  feed.prepend(item);

  // Keep feed trimmed to 60 items
  while (feed.children.length > 60) {
    feed.removeChild(feed.lastChild);
  }
}

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------
function loadAllData() {
  loadStatus();
  if (state.activeTab === "records") loadRecords();
  if (state.activeTab === "iam") {
    loadUsers();
    loadGroups();
  }
  if (state.activeTab === "rules") loadRules();
  if (state.activeTab === "hardware") loadPorts();
}

function applyTheme(theme) {
  const activeTheme = theme === "light" ? "light" : "dark";
  const nextTheme = activeTheme === "dark" ? "light" : "dark";

  document.documentElement.dataset.theme = activeTheme;
  document.querySelectorAll(".theme-toggle").forEach((toggle) => {
    toggle.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
    toggle.title = `Switch to ${nextTheme} mode`;
    const label = toggle.querySelector(".theme-label");
    const icon = toggle.querySelector(".theme-icon");
    if (label) label.textContent = `${nextTheme === "light" ? "Light" : "Dark"} mode`;
    if (icon) icon.textContent = nextTheme === "light" ? "☼" : "☾";
  });
}

window.addEventListener("DOMContentLoaded", () => {
  applyTheme(document.documentElement.dataset.theme);
  setupTabs();
  setupSSEListeners();
  window.telemetrySSE.connect();
  checkAuthSession();

  // Attach button triggers
  document.querySelectorAll(".theme-toggle").forEach((toggle) => toggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    localStorage.setItem("pbds-theme", nextTheme);
    applyTheme(nextTheme);
  }));

  document.getElementById("loginForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    login(document.getElementById("loginUser").value, document.getElementById("loginPass").value);
  });

  document.getElementById("signupForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    signup(document.getElementById("signupUser").value, document.getElementById("signupPass").value);
  });

  document.getElementById("logoutBtn")?.addEventListener("click", logout);
  document.getElementById("toggleVirtualBtn")?.addEventListener("click", toggleVirtualHardware);
  
  document.getElementById("burstDemoBtn")?.addEventListener("click", async () => {
    try {
      const res = await api("/api/system/burst-demo", {
        method: "POST",
        body: JSON.stringify({ count: 5 })
      });
      showToast(res.message);
    } catch (e) {
      showToast(e.message, true);
    }
  });

  document.getElementById("selectAllRecords")?.addEventListener("change", (e) => {
    const isChecked = e.target.checked;
    state.records.forEach(r => {
      if (isChecked) state.selectedRecordIds.add(r.data_id);
      else state.selectedRecordIds.delete(r.data_id);
    });
    renderRecordsTable();
    updateBatchToolbar();
  });

  document.getElementById("createUserForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    createUser(
      document.getElementById("newUsername").value,
      document.getElementById("newPassword").value,
      document.getElementById("newUserRole").value
    );
  });

  document.getElementById("createGroupForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    createGroup(
      document.getElementById("newGroupName").value,
      document.getElementById("newGroupPerms").value
    );
  });

  document.getElementById("createRuleForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    saveRule({
      module_id: parseInt(document.getElementById("ruleModuleId").value),
      sensor_index: parseInt(document.getElementById("ruleSensorIndex").value),
      sensor_name: document.getElementById("ruleSensorName").value,
      operator: document.getElementById("ruleOperator").value,
      threshold: parseFloat(document.getElementById("ruleThreshold").value),
      priority: document.getElementById("rulePriority").value,
      description: document.getElementById("ruleDesc").value
    });
  });

  // Background refresh for system stats every 10s
  setInterval(loadStatus, 10000);
});

// Expose functions globally for inline HTML event handlers
window.showSignupForm = showSignupForm;
window.toggleUserPerms = toggleUserPerms;
window.grantPerm = grantPerm;
window.revokePerm = revokePerm;
window.loadMoreRecords = loadMoreRecords;
window.openModal = openModal;
window.closeModal = closeModal;
window.deleteSingleRecord = deleteSingleRecord;
window.batchDeleteSelected = batchDeleteSelected;
window.batchChangePriority = batchChangePriority;
window.batchRequeueSelected = batchRequeueSelected;
window.purgeRecords = purgeRecords;
window.deleteUser = deleteUser;
window.deleteGroup = deleteGroup;
window.deleteRule = deleteRule;
window.loadPorts = loadPorts;
window.connectSelectedPorts = connectSelectedPorts;
window.disconnectAllPorts = disconnectAllPorts;
window.toggleVirtualHardware = toggleVirtualHardware;
window.loadRecords = loadRecords;
window.downloadExport = downloadExport;
