/* ═══════════════════════════════════════════════════════════
   cURL Generator · 星轨控制台 前端逻辑
   通过本地 HTTP API（window.API_BASE）与 Python 后端通信
   Postman 式交互：多标签请求 / 上下分栏 / 前后置脚本
   ═══════════════════════════════════════════════════════════ */
"use strict";

/* ─────────── 状态 ─────────── */
const state = {
  // ── 编辑缓冲区（= 当前活动标签的内容） ──
  method: "GET",
  url: "",
  env: "(不选择环境)",
  headers: [],   // [k, v, enabled]
  params: [],    // [k, v, enabled]
  bodyType: "JSON",
  body: "",
  authType: "None",
  authValue: "",
  authUser: "",
  authPass: "",
  authHeader: "X-API-Key",
  ssl: false,
  silent: false,
  proxy: "",
  timeout: "",
  preScript: "",
  postScript: "",
  curl: "",
  // ── 标签页 ──
  tabs: [],        // [{id, snap, saved:{name,group}|null, dirty}]
  activeTab: null,
  // ── 全局 ──
  envs: [],
  sshTargets: [],
  saved: { groups: [] },
  history: [],
  ssh: {},
  ai: {},
  savedFilter: "",
  vars: {},          // 脚本会话变量
  executing: false,
  eventCursor: 0,
  // ── 响应 ──
  respRaw: "",
  respCode: null,
  respElapsed: 0,
  respStartTs: 0,
  respPretty: true,
};

const $ = (id) => document.getElementById(id);
const METHOD_COLORS = {
  GET: "#16b981", POST: "#3b82f6", PUT: "#e8930c",
  DELETE: "#e23d5a", PATCH: "#8b5cf6", HEAD: "#0aa2b8", OPTIONS: "#7c8aa0",
};

/* ─────────── HTTP 传输 ─────────── */
async function apiGet(path) {
  try {
    const r = await fetch(window.API_BASE + path);
    return await r.json();
  } catch (e) {
    return { error: "后端通信失败: " + e.message };
  }
}

async function api(path, body) {
  const opt = {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body: typeof body === "string" ? body : JSON.stringify(body ?? {}),
  };
  try {
    const r = await fetch(window.API_BASE + path, opt);
    return await r.json();
  } catch (e) {
    return { error: "后端通信失败: " + e.message };
  }
}

/* ─────────── 工具 ─────────── */
function shade(hex, pct) {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const out = ch.map((c) => {
    const v = pct < 0 ? c * (1 + pct / 100) : c + (255 - c) * (pct / 100);
    return Math.max(0, Math.min(255, Math.round(v)));
  });
  return "#" + out.map((c) => c.toString(16).padStart(2, "0")).join("");
}

function setMethodChip(method) {
  const btn = $("method-btn");
  const base = METHOD_COLORS[method] || "#5b8cff";
  btn.style.setProperty("--mc", base);
  btn.style.setProperty("--mc-dark", shade(base, -22));
  btn.innerHTML = `${method} <i>▾</i>`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("toast-root").appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 300);
  }, 2600);
}

function setStatus(text, kind = "") {
  const pill = $("status-pill");
  pill.textContent = text;
  pill.className = `status-pill ${kind}`;
}

/* ─────────── cURL 语法高亮 ─────────── */
function highlightCurl(cmd) {
  const RE = /(\\\r?\n\s*)|("[^"]*")|('[^']*')|(\s-{1,2}[a-zA-Z][\w-]*)/g;
  let out = "";
  let last = 0;
  let m;
  while ((m = RE.exec(cmd))) {
    out += escapeHtml(cmd.slice(last, m.index));
    const tok = m[0];
    if (m[1]) out += `<span class="tok-flag">${escapeHtml(tok)}</span>`;
    else if (m[2] || m[3]) {
      const inner = tok.slice(1, -1);
      if (/^https?:\/\//.test(inner)) {
        const q = m[2] ? '"' : "'";
        out += `${q}<span class="tok-url">${escapeHtml(inner)}</span>${q}`;
      } else {
        out += `<span class="tok-str">${escapeHtml(tok)}</span>`;
      }
    } else out += `<span class="tok-flag">${escapeHtml(tok)}</span>`;
    last = m.index + tok.length;
  }
  out += escapeHtml(cmd.slice(last));
  if (/^curl\b/.test(cmd)) out = out.replace(/^curl/, '<span class="tok-cmd">curl</span>');
  return out;
}

/* ─────────── 环境变量引用 {{ref}} 高亮 ─────────── */
function envVarMap() {
  const map = {};
  const env = state.envs.find((x) => x.alias === state.env);
  if (env) (env.custom_params || []).forEach((p) => { if (p[0]) map[p[0]] = p[1]; });
  return map;
}

function syncBodyHighlight() {
  const ta = $("body-input");
  const hl = $("body-hl");
  if (!ta || !hl) return;
  let text = ta.value;
  if (text.endsWith("\n")) text += " ";
  hl.innerHTML = escapeHtml(text).replace(/\{\{[^{}]*\}\}/g, (m) => {
    const key = m.slice(2, -2);
    return '<span class="env-ref" data-ref="' + escapeHtml(key) + '">' + m + '</span>';
  });
  hl.scrollTop = ta.scrollTop;
  hl.scrollLeft = ta.scrollLeft;
  hideRefTip();
}

/* ─────────── 引用悬浮提示：显示解析后的实际值 ─────────── */
let refTipEl = null;
function ensureRefTip() {
  if (!refTipEl) {
    refTipEl = document.createElement("div");
    refTipEl.className = "ref-tip";
    document.body.appendChild(refTipEl);
  }
  return refTipEl;
}
function hideRefTip() {
  if (refTipEl) refTipEl.classList.remove("show");
}
function showRefTip(key, value, hasVal, x, y) {
  const tip = ensureRefTip();
  tip.textContent = hasVal ? key + " = " + value : key + " = （未定义）";
  tip.classList.toggle("ref-tip-miss", !hasVal);
  tip.classList.add("show");
  tip.style.left = "0px";
  tip.style.top = "0px";
  const r = tip.getBoundingClientRect();
  let nx = x + 14;
  let ny = y + 18;
  if (nx + r.width > window.innerWidth - 8) nx = Math.max(8, x - r.width - 14);
  if (ny + r.height > window.innerHeight - 8) ny = Math.max(8, y - r.height - 18);
  tip.style.left = nx + "px";
  tip.style.top = ny + "px";
}
function handleRefHover(e) {
  const ta = $("body-input");
  const wrap = ta && ta.closest(".editor-wrap");
  if (!wrap) return;
  const spans = wrap.querySelectorAll(".env-ref");
  let hit = null;
  for (let i = 0; i < spans.length; i++) {
    const r = spans[i].getBoundingClientRect();
    if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
      hit = spans[i];
      break;
    }
  }
  if (!hit) { hideRefTip(); return; }
  const key = hit.getAttribute("data-ref") || "";
  const map = envVarMap();
  const hasVal = Object.prototype.hasOwnProperty.call(map, key);
  showRefTip(key, hasVal ? map[key] : "", hasVal, e.clientX, e.clientY);
}

/* ═══════════ 标签页（多请求编辑） ═══════════ */
const SNAP_FIELDS = ["method", "env", "bodyType", "authType", "authValue", "authUser",
  "authPass", "authHeader", "ssl", "silent", "preScript", "postScript"];
let tabSeq = 0;

function emptySnapshot() {
  return {
    method: "GET", url: "", env: "(不选择环境)", headers: [], params: [],
    bodyType: "JSON", body: "", authType: "None", authValue: "", authUser: "",
    authPass: "", authHeader: "X-API-Key", ssl: false, silent: false,
    proxy: "", timeout: "", preScript: "", postScript: "", curl: "",
  };
}

function snapshotFromState() {
  const s = {};
  SNAP_FIELDS.forEach((f) => { s[f] = state[f]; });
  s.url = $("url-input").value;
  s.body = $("body-input").value;
  s.proxy = $("opt-proxy").value;
  s.timeout = $("opt-timeout").value;
  s.headers = state.headers.map((r) => [...r]);
  s.params = state.params.map((r) => [...r]);
  s.curl = state.curl;
  return s;
}

function snapshotFromRequest(d) {
  const s = emptySnapshot();
  s.method = d.method || "GET";
  s.url = d.url || "";
  s.env = d.env || "(不选择环境)";
  s.headers = (d.headers || []).map((p) => [p[0], p[1], p[2] !== false]);
  s.params = (d.params || []).map((p) => [p[0], p[1], p[2] !== false]);
  s.bodyType = d.body_type || "JSON";
  s.body = d.body || "";
  s.authType = d.auth_type || "None";
  s.authValue = d.auth_value || "";
  s.authUser = d.auth_user || "";
  s.authPass = d.auth_pass || "";
  s.authHeader = d.auth_header_name || "X-API-Key";
  s.ssl = !!d.ssl;
  s.silent = !!d.silent;
  s.proxy = d.proxy || "";
  s.timeout = d.timeout || "";
  s.preScript = d.pre_script || "";
  s.postScript = d.post_script || "";
  return s;
}

function loadSnapshot(s) {
  SNAP_FIELDS.forEach((f) => { state[f] = s[f]; });
  state.url = s.url || "";
  state.body = s.body || "";
  state.proxy = s.proxy || "";
  state.timeout = s.timeout || "";
  state.headers = (s.headers || []).map((r) => [r[0], r[1], r[2] !== false]);
  state.params = (s.params || []).map((r) => [r[0], r[1], r[2] !== false]);
  state.curl = s.curl || "";
}

function activeTabRef() {
  return state.tabs.find((t) => t.id === state.activeTab) || null;
}
function stashActive() {
  const t = activeTabRef();
  if (t) t.snap = snapshotFromState();
}
function hostOf(url) {
  if (!url) return "";
  const h = String(url).replace(/^[a-z]+:\/\//i, "").split(/[/?#]/)[0] || String(url);
  return h.length > 22 ? h.slice(0, 22) + "…" : h;
}
function tabTitle(t) {
  return (t.saved && t.saved.name) || hostOf(t.snap.url) || "新请求";
}

function newTab(snap, saved) {
  stashActive();
  const t = { id: ++tabSeq, snap: snap || emptySnapshot(), saved: saved || null, dirty: false };
  state.tabs.push(t);
  state.activeTab = t.id;
  loadSnapshot(t.snap);
  syncControlsFromState();
  syncEditorTabForMethod();
  renderTabs();
  renderSaved();
  return t;
}

function activateTab(id) {
  if (id === state.activeTab) return;
  stashActive();
  state.activeTab = id;
  const t = activeTabRef();
  loadSnapshot(t.snap);
  syncControlsFromState();
  syncEditorTabForMethod();
  renderTabs();
  renderSaved();
}

async function closeTab(id) {
  const idx = state.tabs.findIndex((t) => t.id === id);
  if (idx < 0) return;
  const t = state.tabs[idx];
  if (t.dirty) {
    const ok = await askConfirm(`「${tabTitle(t)}」有未保存的修改，关闭后将丢失。确定关闭？`);
    if (!ok) return;
  }
  state.tabs.splice(idx, 1);
  if (state.activeTab === id) {
    state.activeTab = null;
    let next = state.tabs[Math.min(idx, state.tabs.length - 1)];
    if (!next) {
      next = { id: ++tabSeq, snap: emptySnapshot(), saved: null, dirty: false };
      state.tabs.push(next);
    }
    state.activeTab = next.id;
    loadSnapshot(next.snap);
    syncControlsFromState();
    syncEditorTabForMethod();
  }
  renderTabs();
  renderSaved();
}

function markDirty() {
  const t = activeTabRef();
  if (!t) return;
  if (!t.dirty) {
    t.dirty = true;
    renderTabs();
    return;
  }
  if (!t.saved) {
    // 未保存标签：标题跟随 URL
    t.snap = snapshotFromState();
    const el = document.querySelector(`.req-tab[data-tab-id="${t.id}"] .rt-title`);
    if (el) {
      el.textContent = tabTitle(t);
      el.parentElement.title = t.snap.url || "未保存的请求";
    }
    const m = document.querySelector(`.req-tab[data-tab-id="${t.id}"] .rt-method`);
    if (m) { m.textContent = t.snap.method; m.style.color = METHOD_COLORS[t.snap.method] || "var(--accent)"; }
  }
}

function renderTabs() {
  const bar = $("req-tabs");
  bar.innerHTML = "";
  state.tabs.forEach((t) => {
    const el = document.createElement("div");
    el.className = "req-tab" + (t.id === state.activeTab ? " active" : "");
    el.dataset.tabId = t.id;
    el.title = t.snap.url || "未保存的请求";
    const mc = METHOD_COLORS[t.snap.method] || "var(--accent)";
    el.innerHTML = `<span class="rt-method" style="color:${mc}">${escapeHtml(t.snap.method || "GET")}</span>` +
      `<span class="rt-title">${escapeHtml(tabTitle(t))}</span>` +
      (t.dirty ? '<span class="rt-dot" title="未保存">●</span>' : "");
    const close = document.createElement("button");
    close.className = "rt-close";
    close.textContent = "×";
    close.title = "关闭标签";
    close.addEventListener("click", (e) => { e.stopPropagation(); closeTab(t.id); });
    el.appendChild(close);
    el.addEventListener("click", () => activateTab(t.id));
    bar.appendChild(el);
  });
  const add = document.createElement("button");
  add.className = "req-tab-add";
  add.textContent = "＋";
  add.title = "新建请求 (Ctrl+T)";
  add.addEventListener("click", () => { newTab(); toast("已新建空白请求"); });
  bar.appendChild(add);
}

/* ─────────── 表单采集 / 回填 ─────────── */
function collectForm() {
  // 生成/执行用：只含启用行的 [k, v]
  const clean = (list) => list
    .filter((r) => r[2] !== false)
    .map(([k, v]) => [String(k || "").trim(), String(v || "").trim()])
    .filter(([k, v]) => k || v);
  const data = {
    method: state.method,
    url: $("url-input").value.trim(),
    env: state.env,
    headers: clean(state.headers),
    params: clean(state.params),
    body_type: state.bodyType,
    body: $("body-input").value,
    auth_type: state.authType,
    ssl: state.ssl,
    silent: state.silent,
    proxy: $("opt-proxy").value.trim(),
    timeout: $("opt-timeout").value.trim(),
  };
  if (state.authType === "Bearer Token" || state.authType === "API Key") {
    data.auth_value = state.authValue;
  }
  if (state.authType === "Basic Auth") {
    data.auth_user = state.authUser;
    data.auth_pass = state.authPass;
  }
  if (state.authType === "API Key") data.auth_header_name = state.authHeader;
  return data;
}

function collectSaveData() {
  // 保存用：保留禁用行（三元组）与脚本
  const data = collectForm();
  const keep = (list) => list
    .map(([k, v, en]) => [String(k || "").trim(), String(v || "").trim(), en !== false])
    .filter(([k, v]) => k || v);
  data.headers = keep(state.headers);
  data.params = keep(state.params);
  data.pre_script = state.preScript;
  data.post_script = state.postScript;
  return data;
}

function syncControlsFromState() {
  // 方法
  setMethodChip(state.method);
  // URL / 环境
  $("url-input").value = state.url;
  renderEnvSelect();
  // KV
  renderKV($("headers-table"), state.headers, "headers");
  renderKV($("params-table"), state.params, "params");
  // Body
  $("body-type").value = state.bodyType;
  $("body-input").value = state.body;
  syncBodyHighlight();
  // Auth
  $("auth-type").value = state.authType;
  renderAuthFields();
  // Options
  $("opt-ssl").checked = state.ssl;
  $("opt-silent").checked = state.silent;
  $("opt-proxy").value = state.proxy;
  $("opt-timeout").value = state.timeout;
  // 脚本
  $("pre-script").value = state.preScript || "";
  $("post-script").value = state.postScript || "";
  // cURL 预览
  $("curl-preview").innerHTML = state.curl
    ? highlightCurl(state.curl)
    : '<span class="placeholder">点击「生成 cURL」或按 Ctrl+Enter</span>';
}

/* ─────────── KV 编辑器（含启用勾选） ─────────── */
function renderKV(container, list, key) {
  container.innerHTML = "";
  if (!list.length) {
    container.innerHTML = '<div class="kv-empty">暂无数据，点击「+ 添加」新增一行</div>';
    return;
  }
  list.forEach((row, i) => {
    const [k, v] = row;
    const en = row[2] !== false;
    const rowEl = document.createElement("div");
    rowEl.className = "kv-row" + (en ? "" : " off");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "kv-check";
    cb.checked = en;
    cb.title = "启用 / 禁用此行（禁用后不进入生成的命令）";
    cb.addEventListener("change", () => {
      state[key][i][2] = cb.checked;
      rowEl.classList.toggle("off", !cb.checked);
      markDirty();
    });
    const ki = document.createElement("input");
    ki.placeholder = "Key";
    ki.value = k || "";
    ki.addEventListener("input", () => { state[key][i][0] = ki.value; markDirty(); });
    const vi = document.createElement("input");
    vi.placeholder = "Value";
    vi.value = v || "";
    vi.addEventListener("input", () => { state[key][i][1] = vi.value; markDirty(); });
    const del = document.createElement("button");
    del.className = "row-del";
    del.title = "删除";
    del.textContent = "×";
    del.addEventListener("click", () => {
      state[key].splice(i, 1);
      markDirty();
      renderKV(container, list, key);
    });
    rowEl.append(cb, ki, vi, del);
    container.appendChild(rowEl);
  });
}

/* ─────────── Auth 字段 ─────────── */
function renderAuthFields() {
  const box = $("auth-fields");
  box.innerHTML = "";
  const mk = (label, value, ph, onInput, password) => {
    const row = document.createElement("div");
    row.className = "field-row";
    const lb = document.createElement("label");
    lb.textContent = label;
    const inp = document.createElement("input");
    inp.value = value || "";
    if (ph) inp.placeholder = ph;
    if (password) inp.type = "password";
    inp.addEventListener("input", () => { onInput(inp.value); markDirty(); });
    row.append(lb, inp);
    box.appendChild(row);
    return inp;
  };
  if (state.authType === "Bearer Token") {
    mk("Token", state.authValue, "eyJhbGciOiJIUzI1NiIs...", (v) => state.authValue = v);
  } else if (state.authType === "Basic Auth") {
    mk("用户", state.authUser, "username", (v) => state.authUser = v);
    mk("密码", state.authPass, "password", (v) => state.authPass = v, true);
  } else if (state.authType === "API Key") {
    mk("Header", state.authHeader, "X-API-Key", (v) => state.authHeader = v);
    mk("Key", state.authValue, "your-api-key-here", (v) => state.authValue = v, true);
  }
}

/* ─────────── 环境选择 ─────────── */
function renderEnvSelect() {
  const sel = $("env-select");
  const cur = state.env;
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "(不选择环境)";
  opt0.textContent = "环境: 不选择";
  sel.appendChild(opt0);
  state.envs.forEach((e) => {
    const o = document.createElement("option");
    o.value = e.alias;
    o.textContent = `环境: ${e.alias}`;
    sel.appendChild(o);
  });
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "(不选择环境)";
  state.env = sel.value;
}
$("env-select").addEventListener("change", (e) => {
  state.env = e.target.value;
  markDirty();
  updateConnBadge();
});

/* 底部连接徽标：显示当前环境的 SSH 绑定 */
function env_ssh_label() {
  const env = state.envs.find((x) => x.alias === state.env);
  return env && env.use_ssh ? (env.ssh_target || "") : "";
}

function updateConnBadge() {
  const env = state.envs.find((x) => x.alias === state.env);
  const badge = $("conn-badge");
  if (env && env.use_ssh) {
    badge.textContent = `● ${state.env} → SSH:${env.ssh_target || "未选目标机"}`;
  } else if (state.ssh_ready) {
    badge.textContent = "● SSH 已配置";
  } else {
    badge.textContent = "● 本地模式";
  }
}

/* ─────────── 方法下拉 ─────────── */
function switchEditorTab(name) {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  document.querySelectorAll(".tab-page").forEach((x) => x.classList.toggle("active", x.dataset.page === name));
}
function syncEditorTabForMethod() {
  switchEditorTab(["POST", "PUT", "PATCH", "DELETE"].includes(state.method) ? "body" : "params");
}
function renderMethodMenu() {
  const menu = $("method-menu");
  menu.innerHTML = "";
  Object.keys(METHOD_COLORS).forEach((m) => {
    const b = document.createElement("button");
    b.innerHTML = `<span class="dot" style="background:${METHOD_COLORS[m]}"></span>${m}`;
    b.addEventListener("click", () => {
      state.method = m;
      setMethodChip(m);
      markDirty();
      // 方法联动：带 Body 的方法跳 Body 页，其余跳 Query Params
      syncEditorTabForMethod();
      menu.classList.remove("open");
    });
    menu.appendChild(b);
  });
}
$("method-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("method-menu");
  const willOpen = !menu.classList.contains("open");
  menu.classList.toggle("open");
  $("method-btn").classList.toggle("active", willOpen);
});
document.addEventListener("click", () => {
  $("method-menu").classList.remove("open");
  $("method-btn").classList.remove("active");
});

/* ─────────── 侧边栏：快捷请求（搜索 / 激活高亮 / 右键） ─────────── */
function renderSaved() {
  const box = $("saved-list");
  box.innerHTML = "";
  const groups = state.saved.groups || [];
  const kw = (state.savedFilter || "").trim().toLowerCase();
  const active = activeTabRef();
  let anyShown = false;

  groups.forEach((g) => {
    let reqs = g.requests || [];
    if (kw) reqs = reqs.filter((r) => (r.name || "").toLowerCase().includes(kw));
    if (kw && !reqs.length) return;
    anyShown = true;

    const wrap = document.createElement("div");
    wrap.className = "saved-group" + ((g.collapsed && !kw) ? " collapsed" : "");
    const head = document.createElement("button");
    head.className = "saved-group-head";
    head.innerHTML = `<span class="arrow">▾</span>${escapeHtml(g.name)}<span class="cnt">${(g.requests || []).length}</span>`;
    head.addEventListener("click", () => {
      g.collapsed = !g.collapsed;
      wrap.classList.toggle("collapsed");
    });
    head.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      showCtxMenu(e.clientX, e.clientY, [["＋ 新建请求", () => newTab()]]);
    });

    const body = document.createElement("div");
    body.className = "saved-group-body";
    reqs.forEach((r) => {
      const item = document.createElement("button");
      item.className = "saved-item";
      if (active && active.saved && active.saved.group === g.name && active.saved.name === r.name) {
        item.classList.add("active");
      }
      item.style.setProperty("--tag-c", METHOD_COLORS[r.method] || "var(--accent)");
      item.innerHTML = `<span class="method-tag">${escapeHtml(r.method || "GET")}</span>` +
        `<span class="nm">${escapeHtml(r.name)}</span>`;
      const del = document.createElement("span");
      del.className = "del";
      del.textContent = "×";
      del.title = "删除";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSaved(g.name, r.name);
      });
      item.appendChild(del);
      item.addEventListener("click", () => openSaved(r, g.name));
      item.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        showCtxMenu(e.clientX, e.clientY, [
          ["在新标签打开", () => openSaved(r, g.name, true)],
          ["重命名", () => renameSaved(g.name, r)],
          ["删除", () => deleteSaved(g.name, r.name)],
        ]);
      });
      body.appendChild(item);
    });
    if (!reqs.length) {
      body.innerHTML = '<div class="kv-empty" style="padding:8px 0 8px 22px">空分组</div>';
    }
    wrap.append(head, body);
    box.appendChild(wrap);
  });

  if (!anyShown) {
    box.innerHTML = kw
      ? '<div class="kv-empty">无匹配请求</div>'
      : '<div class="kv-empty">还没有保存的请求</div>';
  }
}

function openSaved(r, group, forceNew) {
  if (!forceNew) {
    const exist = state.tabs.find((t) => t.saved && t.saved.group === group && t.saved.name === r.name);
    if (exist) { activateTab(exist.id); return; }
  }
  newTab(snapshotFromRequest(r), { name: r.name, group });
  toast(`已加载: ${r.name}`, "ok");
}

async function renameSaved(group, r) {
  const name = await askPrompt("重命名请求", "新名称", r.name);
  if (!name || name === r.name) return;
  const j = await api("/api/save_request", { data: r, group, name, update_old: r.name });
  if (j.error) { toast(j.error, "err"); return; }
  state.saved = j.saved;
  state.tabs.forEach((t) => {
    if (t.saved && t.saved.group === group && t.saved.name === r.name) t.saved = { name, group };
  });
  renderSaved();
  renderTabs();
  toast(`已重命名为: ${name}`, "ok");
}

async function deleteSaved(group, name) {
  const ok = await askConfirm(`确定删除快捷请求「${name}」？`);
  if (!ok) return;
  const j = await api("/api/delete_request", { group, name });
  if (j.ok) {
    state.saved = j.saved;
    state.tabs.forEach((t) => {
      if (t.saved && t.saved.group === group && t.saved.name === name) t.saved = null;
    });
    renderSaved();
    renderTabs();
    toast("已删除", "ok");
  }
}

/* ─────────── 右键菜单组件 ─────────── */
let ctxMenu = null;
function hideCtxMenu() {
  if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
}
function showCtxMenu(x, y, items) {
  hideCtxMenu();
  ctxMenu = document.createElement("div");
  ctxMenu.className = "ctx-menu";
  items.forEach(([label, fn]) => {
    const it = document.createElement("button");
    it.className = "ctx-item";
    it.type = "button";
    it.textContent = label;
    it.addEventListener("click", () => { hideCtxMenu(); fn(); });
    ctxMenu.appendChild(it);
  });
  document.body.appendChild(ctxMenu);
  const mw = ctxMenu.offsetWidth, mh = ctxMenu.offsetHeight;
  ctxMenu.style.left = Math.max(4, Math.min(x, window.innerWidth - mw - 8)) + "px";
  ctxMenu.style.top = Math.max(4, Math.min(y, window.innerHeight - mh - 8)) + "px";
}
document.addEventListener("click", hideCtxMenu);
window.addEventListener("blur", hideCtxMenu);
document.addEventListener("contextmenu", (e) => {
  if (ctxMenu && !ctxMenu.contains(e.target)) hideCtxMenu();
});

{
  const savedSection = $("panel-saved");
  savedSection.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    e.stopPropagation();
    showCtxMenu(e.clientX, e.clientY, [["＋ 新建请求", () => newTab()]]);
  });
}

function renderHistory() {
  const box = $("history-list");
  box.innerHTML = "";
  const q = (state.savedFilter || "").toLowerCase();
  const list = q
    ? state.history.filter((h) => (h.url || "").toLowerCase().includes(q) || (h.method || "").toLowerCase().includes(q))
    : state.history;
  if (!list.length) {
    box.innerHTML = `<div class="kv-empty">${state.history.length ? "没有匹配的历史" : "暂无历史"}</div>`;
    return;
  }
  list.forEach((h) => {
    const el = document.createElement("div");
    el.className = "history-item";
    el.title = "点击在预览中查看";
    el.innerHTML = `<time>${escapeHtml(h.time || "")}</time>${escapeHtml(h.method)} ${escapeHtml(h.url)}`;
    el.addEventListener("click", () => {
      state.curl = h.curl;
      $("curl-preview").innerHTML = highlightCurl(h.curl);
      showPpage("preview");
      toast("已载入预览（历史快照）");
    });
    box.appendChild(el);
  });
}

/* ─────────── 导入：Postman Collection v2.1 / OpenAPI 3 ─────────── */
function pmUrl(u) {
  if (!u) return "";
  if (typeof u === "string") return u;
  const host = (u.host || []).join(".");
  const path = (u.path || []).join("/");
  const proto = u.protocol ? u.protocol + "://" : "";
  return proto + host + (path ? "/" + path : "");
}
function pmKV(list) {
  return (list || []).map((h) => [h.key || "", h.value ?? "", !h.disabled]);
}
function pmQuery(u) {
  if (!u || typeof u === "string" || !u.query) return [];
  return u.query.map((q) => [q.key || "", q.value ?? "", !q.disabled]);
}
function pmBody(b) {
  if (!b || !b.mode) return { bodyType: "None", body: "" };
  if (b.mode === "raw") {
    const lang = b.options && b.options.raw && b.options.raw.language;
    return { bodyType: lang === "json" ? "JSON" : "Raw Text", body: b.raw || "" };
  }
  if (b.mode === "formdata" || b.mode === "urlencoded") {
    const list = b[b.mode] || [];
    return { bodyType: "Form Data", body: list.map((x) => `${x.key}=${x.value ?? ""}`).join("\n") };
  }
  return { bodyType: "None", body: "" };
}
function pmAuth(a) {
  if (!a || !a.type) return null;
  const sub = a[a.type];
  const m = {};
  if (Array.isArray(sub)) sub.forEach((x) => { m[x.key] = x.value; });
  else if (sub && typeof sub === "object") Object.assign(m, sub);
  if (a.type === "bearer") return { auth_type: "Bearer Token", auth_value: m.token || "" };
  if (a.type === "basic") return { auth_type: "Basic Auth", auth_user: m.username || "", auth_pass: m.password || "" };
  if (a.type === "apikey") return { auth_type: "API Key", auth_header_name: m.key || "X-API-Key", auth_value: m.value || "" };
  return null;
}
function pmScripts(evts) {
  let pre = "", post = "";
  (evts || []).forEach((e) => {
    const ex = e.script && e.script.exec;
    const code = ex ? (Array.isArray(ex) ? ex.join("\n") : String(ex)) : "";
    if (e.listen === "prerequest") pre = code;
    else if (e.listen === "test") post = code;
  });
  return { pre_script: pre, post_script: post };
}
function parsePostman(col) {
  const groups = [];
  const colAuth = col.auth;
  const walk = (items, prefix) => {
    (items || []).forEach((it) => {
      if (it.item) { walk(it.item, prefix ? prefix + " / " + (it.name || "") : (it.name || "")); return; }
      if (!it.request) return;
      const gname = prefix || (col.info && col.info.name) || "导入";
      let g = groups.find((x) => x.name === gname);
      if (!g) { g = { name: gname, requests: [] }; groups.push(g); }
      const req = it.request;
      const b = pmBody(req.body);
      const auth = pmAuth(req.auth) || pmAuth(colAuth) || {};
      const sc = pmScripts(it.event);
      g.requests.push({
        name: it.name || (req.method || "GET") + " 请求",
        method: String(req.method || "GET").toUpperCase(),
        url: pmUrl(req.url),
        headers: pmKV(req.header),
        params: pmQuery(req.url),
        body_type: b.bodyType,
        body: b.body,
        auth_type: auth.auth_type || "None",
        auth_value: auth.auth_value || "",
        auth_user: auth.auth_user || "",
        auth_pass: auth.auth_pass || "",
        auth_header_name: auth.auth_header_name || "X-API-Key",
        pre_script: sc.pre_script,
        post_script: sc.post_script,
      });
    });
  };
  walk(col.item, "");
  return groups;
}
function parseOpenApi(doc) {
  const groups = [];
  const server = (doc.servers && doc.servers[0] && doc.servers[0].url) || "";
  const HTTP = ["get", "post", "put", "patch", "delete", "head", "options"];
  Object.keys(doc.paths || {}).forEach((p) => {
    HTTP.forEach((m) => {
      const op = doc.paths[p][m];
      if (!op) return;
      const gname = (op.tags && op.tags[0]) || "导入";
      let g = groups.find((x) => x.name === gname);
      if (!g) { g = { name: gname, requests: [] }; groups.push(g); }
      const headers = [], params = [];
      [...(doc.paths[p].parameters || []), ...(op.parameters || [])].forEach((pr) => {
        if (pr.$ref || !pr.name) return;
        const val = pr.example != null ? String(pr.example)
          : (pr.schema && pr.schema.default != null ? String(pr.schema.default) : "");
        if (pr.in === "query") params.push([pr.name, val, true]);
        else if (pr.in === "header") headers.push([pr.name, val, true]);
      });
      let bodyType = "None", body = "";
      const content = op.requestBody && op.requestBody.content;
      if (content) {
        if (content["application/json"]) {
          bodyType = "JSON";
          const c = content["application/json"];
          const ex = c.example || (c.examples && Object.values(c.examples)[0] && Object.values(c.examples)[0].value);
          body = ex ? JSON.stringify(ex, null, 2) : "";
        } else if (content["application/x-www-form-urlencoded"] || content["multipart/form-data"]) {
          bodyType = "Form Data";
          const c = content["application/x-www-form-urlencoded"] || content["multipart/form-data"];
          const props = (c.schema && c.schema.properties) || {};
          body = Object.keys(props).map((k) => `${k}=${props[k].example ?? props[k].default ?? ""}`).join("\n");
        }
      }
      g.requests.push({
        name: op.summary || op.operationId || `${m.toUpperCase()} ${p}`,
        method: m.toUpperCase(),
        url: server + p,
        headers, params,
        body_type: bodyType, body,
        auth_type: "None", pre_script: "", post_script: "",
      });
    });
  });
  return groups;
}
function parseImportText(text) {
  let doc;
  try { doc = JSON.parse(text); } catch (e) { throw new Error("不是有效的 JSON 文件"); }
  if (doc && doc.info && Array.isArray(doc.item)) return parsePostman(doc);
  if (doc && (doc.openapi || doc.swagger)) return parseOpenApi(doc);
  throw new Error("无法识别的格式：支持 Postman Collection v2.1 / 环境变量 或 OpenAPI 3 (JSON)");
}

/* ─────────── 导入：Postman 环境变量 ─────────── */
function isPostmanEnv(doc) {
  return doc && typeof doc.name === "string" && Array.isArray(doc.values) && !doc.item && !doc.openapi;
}
async function importPostmanEnv(doc) {
  const alias = (doc.name || "").trim() || "Postman 环境";
  const params = (doc.values || [])
    .filter((v) => v && v.key && v.enabled !== false)
    .map((v) => [String(v.key), v.value == null ? "" : String(v.value)]);
  if (!params.length) { toast("环境文件里没有可用变量", "err"); return; }
  const exist = state.envs.find((e) => e.alias === alias);
  if (exist) {
    const map = new Map((exist.custom_params || []).map((p) => [p[0], p[1]]));
    params.forEach(([k, v]) => map.set(k, v));
    exist.custom_params = [...map.entries()];
  } else {
    state.envs.push({
      alias, key: "", algo: "MD5", param_name: "sign", enable_sign: false,
      custom_params: params, use_ssh: false, ssh_target: "",
    });
  }
  const j = await api("/api/save_envs", state.envs);
  if (!j.ok) { toast("保存失败", "err"); return; }
  renderEnvSelect();
  toast(exist
    ? `已合并 ${params.length} 个变量到环境「${alias}」✓`
    : `已导入环境「${alias}」(${params.length} 个变量)，右上角选择即用 ✓`, "ok");
}
async function doImportFile(file) {
  const text = await file.text();
  let doc;
  try { doc = JSON.parse(text); } catch (e) { toast("不是有效的 JSON 文件", "err"); return; }
  if (isPostmanEnv(doc)) { await importPostmanEnv(doc); return; }
  const groups = parseImportText(text);
  if (!groups.length) { toast("文件里没有可导入的请求", "err"); return; }
  const j = await api("/api/import_requests", { groups });
  if (j.error) { toast(j.error, "err"); return; }
  state.saved = j.saved;
  renderSaved();
  toast(`导入完成：${j.requests_added} 个请求 / ${j.groups_added} 个新分组`, "ok");
}

/* ─────────── 通用弹窗：输入 / 确认 ─────────── */
let promptResolve = null;
function askPrompt(title, label, value) {
  $("prompt-title").textContent = title;
  $("prompt-label").textContent = label;
  $("prompt-input").value = value || "";
  openModal("modal-prompt");
  setTimeout(() => { $("prompt-input").focus(); $("prompt-input").select(); }, 50);
  return new Promise((res) => { promptResolve = res; });
}
let confirmResolve = null;
function askConfirm(text) {
  $("confirm-text").textContent = text;
  openModal("modal-confirm");
  return new Promise((res) => { confirmResolve = res; });
}
function settleModals() {
  if (promptResolve) { promptResolve(null); promptResolve = null; }
  if (confirmResolve) { confirmResolve(false); confirmResolve = null; }
}

/* ─────────── 响应面板 ─────────── */
function respClear() {
  state.respRaw = "";
  state.respCode = null;
  state.respElapsed = 0;
  $("response-view").textContent = "";
  $("response-view").classList.remove("streaming");
  $("resp-meta").textContent = "";
}
function respAppend(text, track = true) {
  if (track) state.respRaw += (state.respRaw && !state.respRaw.endsWith("\n") ? "\n" : "") + text;
  const v = $("response-view");
  v.textContent += (v.textContent && !v.textContent.endsWith("\n") ? "\n" : "") + text;
  v.scrollTop = v.scrollHeight;
}
function respSetStatus(text, kind) {
  const el = $("resp-status");
  el.textContent = text;
  el.className = `resp-status ${kind || ""}`;
}
function fmtSize(n) {
  return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(2) + " MB";
}
function tryExtractJson(raw) {
  const t = (raw || "").trim();
  if (!t) return null;
  try { return JSON.stringify(JSON.parse(t), null, 2); } catch (e) { /* fallthrough */ }
  const i = t.search(/[{[]/);
  if (i >= 0) {
    const tail = t.slice(i);
    try { return (i > 0 ? t.slice(0, i) + "\n" : "") + JSON.stringify(JSON.parse(tail), null, 2); } catch (e) { /* fallthrough */ }
    const j = Math.max(tail.lastIndexOf("}"), tail.lastIndexOf("]"));
    if (j > 0) {
      try { return (i > 0 ? t.slice(0, i) + "\n" : "") + JSON.stringify(JSON.parse(tail.slice(0, j + 1)), null, 2); } catch (e) { /* fallthrough */ }
    }
  }
  return null;
}
function renderRespBody() {
  const v = $("response-view");
  v.classList.remove("streaming");
  if (state.respPretty) {
    const pretty = tryExtractJson(state.respRaw);
    v.textContent = pretty || state.respRaw;
  } else {
    v.textContent = state.respRaw;
  }
  v.scrollTop = v.scrollHeight;
}
function respFinalize(code) {
  state.respCode = code;
  const elapsed = state.respStartTs ? (Date.now() - state.respStartTs) / 1000 : 0;
  state.respElapsed = Math.round(elapsed * 1000);
  const size = new Blob([state.respRaw]).size;
  $("resp-meta").textContent = `exit ${code} · ${elapsed.toFixed(2)}s · ${fmtSize(size)}`;
  renderRespBody();
}
function showPpage(name) {
  document.querySelectorAll(".ptab").forEach((x) => x.classList.toggle("active", x.dataset.ptab === name));
  document.querySelectorAll(".ppage").forEach((x) => x.classList.toggle("active", x.dataset.ppage === name));
}

/* ─────────── 前后置脚本（Postman 风格 pm API） ─────────── */
function kvFacade(key) {
  return {
    get(k) {
      const r = state[key].find((x) => x[0] === k && x[2] !== false);
      return r ? r[1] : undefined;
    },
    set(k, v) {
      const r = state[key].find((x) => x[0] === k);
      if (r) { r[1] = String(v); r[2] = true; } else state[key].push([k, String(v), true]);
    },
    remove(k) { state[key] = state[key].filter((x) => x[0] !== k); },
  };
}
function buildPm(kind) {
  const pm = {
    request: {
      get method() { return state.method; },
      set method(v) { state.method = String(v).toUpperCase(); },
      get url() { return $("url-input").value; },
      set url(v) { $("url-input").value = String(v); },
      get body() { return $("body-input").value; },
      set body(v) { $("body-input").value = String(v); syncBodyHighlight(); },
      headers: kvFacade("headers"),
      params: kvFacade("params"),
    },
    variables: {
      get(k) { return state.vars[k]; },
      set(k, v) { state.vars[k] = v; },
    },
    environment: { get(k) { return envVarMap()[k]; } },
  };
  if (kind === "post") {
    pm.response = {
      code: state.respCode,
      time: state.respElapsed,
      text() { return state.respRaw; },
      json() { return JSON.parse(state.respRaw); },
    };
  }
  return pm;
}
function runScript(kind) {
  const code = kind === "pre" ? state.preScript : state.postScript;
  if (!code || !code.trim()) return { ok: true, logs: [], ran: false };
  const logs = [];
  const con = {};
  ["log", "info", "warn", "error"].forEach((m) => {
    con[m] = (...a) => logs.push(a.map((x) => {
      try { return typeof x === "object" ? JSON.stringify(x) : String(x); } catch (e) { return String(x); }
    }).join(" "));
  });
  try {
    new Function("pm", "console", `"use strict";\n${code}`)(buildPm(kind), con);
    return { ok: true, logs, ran: true };
  } catch (e) {
    return { ok: false, error: e.message, logs, ran: true };
  }
}

/* ─────────── 核心动作 ─────────── */
async function doGenerate() {
  const pre = runScript("pre");
  if (!pre.ok) {
    toast(`前置脚本错误: ${pre.error}`, "err");
    setStatus("前置脚本错误", "err");
    return;
  }
  if (pre.ran) {
    syncControlsFromState();
    markDirty();
  }
  const data = collectForm();
  if (!data.url) {
    toast("请输入 URL", "err");
    setStatus("请输入 URL", "err");
    return;
  }
  const j = await api("/api/generate", data);
  if (j.error) {
    toast(j.error, "err");
    setStatus(j.error, "err");
    return;
  }
  state.curl = j.curl;
  $("curl-preview").innerHTML = highlightCurl(j.curl);
  $("sign-preview").textContent = j.sign_preview || "";
  showPpage("preview");
  setStatus("已生成 ✓", "ok");
  refreshHistory();
}

async function doCopy() {
  if (!state.curl) { toast("没有可复制的内容", "err"); return; }
  await api("/api/copy", { text: state.curl });
  toast("已复制到剪贴板 ✓", "ok");
}

async function doSave() {
  const data = collectForm();
  if (!data.url) { toast("请先输入 URL", "err"); return; }
  const sel = $("save-group");
  sel.innerHTML = "";
  (state.saved.groups || []).forEach((g) => {
    const o = document.createElement("option");
    o.value = g.name; o.textContent = g.name;
    sel.appendChild(o);
  });
  const oNew = document.createElement("option");
  oNew.value = "(新建分组)"; oNew.textContent = "(新建分组)";
  sel.appendChild(oNew);
  const t = activeTabRef();
  $("save-name").value = (t && t.saved && t.saved.name) || "";
  $("save-newgroup").classList.add("hidden");
  openModal("modal-save");
  setTimeout(() => $("save-name").focus(), 50);
}

async function doSaveConfirm() {
  const name = $("save-name").value.trim();
  if (!name) { toast("请填写请求名称", "err"); return; }
  let group = $("save-group").value;
  if (group === "(新建分组)") {
    group = $("save-newgroup").value.trim();
    if (!group) { toast("请填写新分组名称", "err"); return; }
  }
  const t = activeTabRef();
  const j = await api("/api/save_request", {
    data: collectSaveData(), group, name,
    update_old: (t && t.saved && t.saved.name) || "",
  });
  if (j.error) { toast(j.error, "err"); return; }
  state.saved = j.saved;
  if (t) { t.saved = { name, group }; t.dirty = false; }
  renderSaved();
  renderTabs();
  closeModal();
  toast(`已保存: ${name}`, "ok");
}

async function doExec() {
  const data = collectForm();
  if (!data.url) { toast("请先输入 URL", "err"); return; }
  if (!state.curl) await doGenerate();
  // 当前环境启用 SSH 时默认选中远程执行
  const env = state.envs.find((x) => x.alias === state.env);
  const useSsh = !!(env && env.use_ssh);
  document.querySelector('input[name="execmode"][value="ssh"]').checked = useSsh;
  document.querySelector('input[name="execmode"][value="local"]').checked = !useSsh;
  openModal("modal-exec");
}

async function doExecConfirm() {
  const mode = document.querySelector('input[name="execmode"]:checked').value;
  closeModal();
  const data = collectForm();
  state.executing = true;
  state.respStartTs = Date.now();
  $("btn-exec").disabled = true;
  $("btn-stop").disabled = false;
  respClear();
  showPpage("response");
  $("response-view").classList.add("streaming");
  respSetStatus("执行中...", "busy");
  setStatus("执行中...", "busy");
  $("conn-badge").textContent = `● ${mode === "ssh" ? "SSH 远程" : "本地"}执行中`;
  const j = await api("/api/execute", { data, mode });
  if (j.error) {
    state.executing = false;
    $("btn-exec").disabled = false;
    $("btn-stop").disabled = true;
    respAppend(`[错误] ${j.error}`);
    respSetStatus("配置不完整", "err");
    setStatus("执行失败", "err");
    updateConnBadge();
    toast(j.error, "err");
  }
}

async function doStop() {
  await api("/api/stop");
  toast("正在停止...", "busy");
}

async function refreshHistory() {
  const h = await apiGet("/api/history");
  if (Array.isArray(h)) {
    state.history = h;
    renderHistory();
  }
}

/* ─────────── 模态框 ─────────── */
function openModal(id) {
  $("modal-mask").classList.add("open");
  document.querySelectorAll(".modal").forEach((m) => m.style.display = "none");
  $(id).style.display = "flex";
}
function closeModal() {
  $("modal-mask").classList.remove("open");
}

/* ─────────── 环境管理 ─────────── */
async function openEnvModal() {
  state.envs = await apiGet("/api/envs");
  state.sshTargets = await apiGet("/api/ssh_targets");
  renderEnvTable();
  openModal("modal-env");
}

function renderEnvTable() {
  const tbody = $("env-table").querySelector("tbody");
  tbody.innerHTML = "";
  state.envs.forEach((e, i) => {
    const tr = document.createElement("tr");
    const cell = (val, ph, onInput, type) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = type || "text";
      inp.value = val ?? "";
      if (ph) inp.placeholder = ph;
      inp.addEventListener("input", () => onInput(inp.value));
      td.appendChild(inp);
      return td;
    };
    tr.appendChild(cell(e.alias, "唯一别名", (v) => e.alias = v));
    tr.appendChild(cell(e.key, "签名密钥", (v) => e.key = v, "password"));
    const algoTd = document.createElement("td");
    const algoSel = document.createElement("select");
    ["MD5", "SHA256"].forEach((a) => {
      const o = document.createElement("option");
      o.value = a; o.textContent = a;
      algoSel.appendChild(o);
    });
    algoSel.value = e.algo || "MD5";
    algoSel.addEventListener("change", () => e.algo = algoSel.value);
    algoTd.appendChild(algoSel);
    tr.appendChild(algoTd);
    tr.appendChild(cell(e.param_name || "sign", "sign", (v) => e.param_name = v));
    const signTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = e.enable_sign !== false;
    cb.addEventListener("change", () => e.enable_sign = cb.checked);
    signTd.appendChild(cb);
    tr.appendChild(signTd);
    // SSH 执行开关
    const sshTd = document.createElement("td");
    const sshCb = document.createElement("input");
    sshCb.type = "checkbox";
    sshCb.checked = !!e.use_ssh;
    sshCb.addEventListener("change", () => {
      e.use_ssh = sshCb.checked;
      targetSel.disabled = !sshCb.checked;
    });
    sshTd.appendChild(sshCb);
    tr.appendChild(sshTd);
    // 目标机选择
    const targetTd = document.createElement("td");
    const targetSel = document.createElement("select");
    targetSel.disabled = !e.use_ssh;
    const emptyOpt = document.createElement("option");
    emptyOpt.value = ""; emptyOpt.textContent = "—";
    targetSel.appendChild(emptyOpt);
    state.sshTargets.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.label; o.textContent = t.label;
      targetSel.appendChild(o);
    });
    targetSel.value = e.ssh_target || "";
    targetSel.addEventListener("change", () => e.ssh_target = targetSel.value);
    targetTd.appendChild(targetSel);
    tr.appendChild(targetTd);
    const cp = (e.custom_params || []).map(([k, v]) => `${k}=${v}`).join("; ");
    tr.appendChild(cell(cp, "k1=v1; k2=v2", (v) => {
      e.custom_params = v.split(/[;\n]/).map((s) => s.trim()).filter(Boolean)
        .map((s) => { const idx = s.indexOf("="); return idx > 0 ? [s.slice(0, idx).trim(), s.slice(idx + 1).trim()] : [s, ""]; });
    }));
    const delTd = document.createElement("td");
    const del = document.createElement("button");
    del.className = "row-del";
    del.textContent = "×";
    del.addEventListener("click", () => { state.envs.splice(i, 1); renderEnvTable(); });
    delTd.appendChild(del);
    tr.appendChild(delTd);
    tbody.appendChild(tr);
  });
  if (!state.envs.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="kv-empty">暂无环境，点击「+ 添加环境」</td></tr>';
  }
}

async function saveEnvs() {
  const cleaned = state.envs
    .map((e) => ({ ...e, alias: (e.alias || "").trim() }))
    .filter((e) => e.alias);
  const seen = new Set();
  for (const e of cleaned) {
    if (seen.has(e.alias)) { toast(`别名重复: ${e.alias}`, "err"); return; }
    seen.add(e.alias);
    if (e.use_ssh && !e.ssh_target) { toast(`环境 ${e.alias} 启用 SSH 但未选择目标机`, "err"); return; }
  }
  const j = await api("/api/save_envs", cleaned);
  if (!j.ok) { toast("保存失败", "err"); return; }
  state.envs = cleaned;
  renderEnvSelect();
  renderEnvTable();
  toast("环境配置已保存 ✓", "ok");
}

/* ─────────── SSH 设置 ─────────── */
async function openSshModal() {
  state.ssh = await apiGet("/api/ssh");
  $("ssh-jh").value = state.ssh.jump_host || "";
  $("ssh-jp").value = state.ssh.jump_port || "";
  $("ssh-ju").value = state.ssh.jump_user || "";
  $("ssh-jw").value = state.ssh.jump_pass || "";
  $("ssh-test-status").textContent = "";
  renderSshTargets();
  openModal("modal-ssh");
}

function renderSshTargets() {
  const tbody = $("ssh-targets-table").querySelector("tbody");
  tbody.innerHTML = "";
  const targets = state.ssh.targets || [];
  targets.forEach((t, i) => {
    const tr = document.createElement("tr");
    const cell = (val, ph, onInput) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.value = val ?? "";
      if (ph) inp.placeholder = ph;
      inp.addEventListener("input", () => onInput(inp.value));
      td.appendChild(inp);
      return td;
    };
    tr.appendChild(cell(t.label, "如: 生产-244", (v) => t.label = v));
    tr.appendChild(cell(t.host, "IP / 域名", (v) => t.host = v));
    tr.appendChild(cell(t.port || "22", "22", (v) => t.port = v));
    tr.appendChild(cell(t.user, "登录用户", (v) => t.user = v));
    const delTd = document.createElement("td");
    const del = document.createElement("button");
    del.className = "row-del";
    del.textContent = "×";
    del.addEventListener("click", () => {
      state.ssh.targets.splice(i, 1);
      renderSshTargets();
    });
    delTd.appendChild(del);
    tr.appendChild(delTd);
    tbody.appendChild(tr);
  });
  if (!targets.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="kv-empty">暂无目标机，点击「+ 添加目标机」</td></tr>';
  }
}

async function saveSsh() {
  const targets = (state.ssh.targets || [])
    .map((t) => ({
      label: (t.label || "").trim() || (t.host || "").trim(),
      host: (t.host || "").trim(),
      port: (t.port || "22").trim() || "22",
      user: (t.user || "").trim(),
    }))
    .filter((t) => t.host);
  const data = {
    jump_host: $("ssh-jh").value.trim(),
    jump_port: $("ssh-jp").value.trim(),
    jump_user: $("ssh-ju").value.trim(),
    jump_pass: $("ssh-jw").value,
    targets,
  };
  const j = await api("/api/save_ssh", data);
  if (j.ok) {
    state.ssh.targets = targets;
    state.sshTargets = targets;
    updateConnBadge();
    toast("SSH 配置已保存 ✓", "ok");
    closeModal();
  } else toast("保存失败", "err");
}

/* ─────────── AI 脚本生成 ─────────── */
const AI_PRESETS = {
  qwen:     { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", models: ["qwen-plus", "qwen-turbo", "qwen-max"] },
  deepseek: { base_url: "https://api.deepseek.com/v1", models: ["deepseek-chat", "deepseek-reasoner"] },
  kimi:     { base_url: "https://api.moonshot.cn/v1", models: ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"] },
};

function applyAiPreset() {
  const p = AI_PRESETS[$("ai-preset").value];
  const list = $("ai-model-list");
  list.innerHTML = "";
  if (p) {
    $("ai-base-url").value = p.base_url;
    for (const m of p.models) {
      const opt = document.createElement("option");
      opt.value = m;
      list.appendChild(opt);
    }
    if (!p.models.includes($("ai-model").value)) $("ai-model").value = p.models[0];
  }
}

function openAiModal() {
  const ai = state.ai || {};
  $("ai-base-url").value = ai.base_url || "";
  $("ai-api-key").value = ai.api_key || "";
  $("ai-model").value = ai.model || "";
  const preset = Object.keys(AI_PRESETS).find((k) => AI_PRESETS[k].base_url === ai.base_url) || "custom";
  $("ai-preset").value = preset;
  if (preset !== "custom") {
    const p = AI_PRESETS[preset];
    const list = $("ai-model-list");
    list.innerHTML = "";
    for (const m of p.models) {
      const opt = document.createElement("option");
      opt.value = m;
      list.appendChild(opt);
    }
  } else $("ai-model-list").innerHTML = "";
  openModal("modal-ai");
}

async function saveAiConfig() {
  const data = {
    base_url: $("ai-base-url").value.trim(),
    api_key: $("ai-api-key").value.trim(),
    model: $("ai-model").value.trim(),
  };
  const j = await api("/api/save_ai", data);
  if (!j.ok) { toast("保存失败", "err"); return; }
  state.ai = j.ai;
  toast("AI 配置已保存 ✓", "ok");
  closeModal();
}

/* ─────────── AI 生成对话框 ─────────── */
const aiGen = { kind: "pre", script: "" };

function openAiGenModal(kind) {
  if (!(state.ai || {}).api_key) { openAiModal(); toast("请先配置大模型接口", "err"); return; }
  aiGen.kind = kind;
  aiGen.script = "";
  $("ai-gen-title").textContent = kind === "pre" ? "✨ AI 生成前置脚本" : "✨ AI 生成后置脚本";
  $("ai-gen-prompt").placeholder = kind === "pre"
    ? "如：给请求加 timestamp 参数并写入 X-Sign 头"
    : "如：把响应里的 token 存进变量，打印耗时";
  $("ai-gen-prompt").value = "";
  const res = $("ai-gen-result");
  res.hidden = true;
  res.textContent = "";
  $("ai-gen-apply").hidden = true;
  const run = $("ai-gen-run");
  run.disabled = false;
  run.textContent = "✨ 生成";
  openModal("modal-ai-gen");
  setTimeout(() => $("ai-gen-prompt").focus(), 50);
}

async function runAiGen() {
  const prompt = $("ai-gen-prompt").value.trim();
  if (!prompt) { toast("先描述一下想让脚本做什么", "err"); $("ai-gen-prompt").focus(); return; }
  const run = $("ai-gen-run");
  run.disabled = true;
  run.textContent = "生成中…";
  $("ai-gen-apply").hidden = true;
  try {
    const ctx = collectForm();
    ctx.pre_script = state.preScript;
    ctx.post_script = state.postScript;
    const j = await api("/api/ai_script", { kind: aiGen.kind, prompt, context: ctx });
    if (j.error) { toast(j.error, "err"); return; }
    aiGen.script = j.script;
    const res = $("ai-gen-result");
    res.textContent = j.script;
    res.hidden = false;
    $("ai-gen-apply").hidden = false;
    run.textContent = "↻ 重新生成";
  } finally {
    run.disabled = false;
    if (run.textContent === "生成中…") run.textContent = "✨ 生成";
  }
}

function applyAiGen() {
  if (!aiGen.script) return;
  const ta = $(aiGen.kind === "pre" ? "pre-script" : "post-script");
  if (aiGen.kind === "pre") state.preScript = aiGen.script; else state.postScript = aiGen.script;
  ta.value = aiGen.script;
  markDirty();
  closeModal();
  toast("脚本已应用，可直接修改后再执行 ✓", "ok");
}

/* ─────────── 事件流（轮询） ─────────── */
async function pollEvents() {
  const j = await apiGet(`/api/events?since=${state.eventCursor}`);
  if (!j.events) return;
  for (const ev of j.events) {
    if (ev.type === "output") {
      if (ev.tag === "info") respAppend(`[${ev.text.trim()}]`);
      else respAppend(ev.text.replace(/\r?\n$/, ""));
    } else if (ev.type === "finished") {
      state.executing = false;
      $("btn-exec").disabled = false;
      $("btn-stop").disabled = true;
      updateConnBadge();
      const code = ev.code;
      let txt, kind;
      if (code === 0) { txt = "成功 (exit 0)"; kind = "ok"; }
      else if (code === -1) { txt = "失败"; kind = "err"; }
      else if (code === -2) { txt = "已停止"; kind = "busy"; }
      else if (code === -3) { txt = "超时"; kind = "err"; }
      else { txt = `exit ${code}`; kind = "busy"; }
      respFinalize(code);
      respAppend(`──── 状态: ${txt} ────`, false);
      // 后置脚本
      const post = runScript("post");
      post.logs.forEach((l) => respAppend(`[script] ${l}`, false));
      if (!post.ok) respAppend(`[脚本错误] ${post.error}`, false);
      respSetStatus(txt, kind);
      setStatus(code === 0 ? "执行完成 ✓" : txt, kind);
    } else if (ev.type === "notify") {
      toast(ev.text, ev.kind || "");
    } else if (ev.type === "ssh_test") {
      const el = $("ssh-test-status");
      if (el) {
        el.textContent = `${ev.is_target === "target" ? "目标机" : "跳板机"}: ${ev.msg}`;
        el.className = `ssh-test-status ${ev.ok ? "ok" : "err"}`;
      }
    } else if (ev.type === "legacy") {
      document.body.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;color:#8b9ab5;font-family:sans-serif">正在切换回旧版界面…</div>';
    }
  }
  state.eventCursor = j.next;
}

/* ─────────── 事件绑定 ─────────── */
function bindEvents() {
  $("btn-generate").addEventListener("click", doGenerate);
  $("btn-copy").addEventListener("click", doCopy);
  $("btn-copy2").addEventListener("click", doCopy);
  $("btn-save").addEventListener("click", doSave);
  $("btn-exec").addEventListener("click", doExec);
  $("btn-stop").addEventListener("click", doStop);
  $("save-ok").addEventListener("click", doSaveConfirm);
  $("exec-ok").addEventListener("click", doExecConfirm);
  $("btn-resp-copy").addEventListener("click", async () => {
    const text = $("response-view").textContent;
    if (!text) { toast("没有可复制的响应", "err"); return; }
    await api("/api/copy", { text });
    toast("响应已复制 ✓", "ok");
  });
  $("btn-resp-pretty").addEventListener("click", () => {
    if (state.executing) return;
    state.respPretty = !state.respPretty;
    $("btn-resp-pretty").textContent = state.respPretty ? "Pretty" : "Raw";
    renderRespBody();
  });

  // 编辑器子页签
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab-page").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      document.querySelector(`.tab-page[data-page="${t.dataset.tab}"]`).classList.add("active");
    });
  });
  // 底部面板页签
  document.querySelectorAll(".ptab").forEach((t) => {
    t.addEventListener("click", () => showPpage(t.dataset.ptab));
  });

  // URL / KV 增删
  $("url-input").addEventListener("input", markDirty);
  $("headers-add").addEventListener("click", () => { state.headers.push(["", "", true]); markDirty(); renderKV($("headers-table"), state.headers, "headers"); });
  $("headers-clear").addEventListener("click", () => { state.headers = []; markDirty(); renderKV($("headers-table"), state.headers, "headers"); });
  $("params-add").addEventListener("click", () => { state.params.push(["", "", true]); markDirty(); renderKV($("params-table"), state.params, "params"); });
  $("params-clear").addEventListener("click", () => { state.params = []; markDirty(); renderKV($("params-table"), state.params, "params"); });

  // Body
  $("body-type").addEventListener("change", (e) => { state.bodyType = e.target.value; markDirty(); });
  $("body-input").addEventListener("input", () => { syncBodyHighlight(); markDirty(); });
  $("body-input").addEventListener("scroll", () => {
    const hl = $("body-hl");
    hl.scrollTop = $("body-input").scrollTop;
    hl.scrollLeft = $("body-input").scrollLeft;
    hideRefTip();
  });
  $("body-input").addEventListener("mousemove", handleRefHover);
  $("body-input").addEventListener("mouseleave", hideRefTip);
  $("btn-pretty").addEventListener("click", () => {
    try {
      const obj = JSON.parse($("body-input").value);
      $("body-input").value = JSON.stringify(obj, null, 2);
      syncBodyHighlight();
      markDirty();
      toast("JSON 已美化 ✓", "ok");
    } catch (e) { toast(`JSON 格式错误: ${e.message}`, "err"); }
  });
  $("btn-minify").addEventListener("click", () => {
    try {
      const obj = JSON.parse($("body-input").value);
      $("body-input").value = JSON.stringify(obj);
      syncBodyHighlight();
      markDirty();
      toast("JSON 已压缩 ✓", "ok");
    } catch (e) { toast(`JSON 格式错误: ${e.message}`, "err"); }
  });

  // Auth
  $("auth-type").addEventListener("change", (e) => { state.authType = e.target.value; markDirty(); renderAuthFields(); });

  // Options
  $("opt-ssl").addEventListener("change", (e) => { state.ssl = e.target.checked; markDirty(); });
  $("opt-silent").addEventListener("change", (e) => { state.silent = e.target.checked; markDirty(); });
  $("opt-proxy").addEventListener("input", markDirty);
  $("opt-timeout").addEventListener("input", markDirty);

  // 脚本
  $("pre-script").addEventListener("input", (e) => { state.preScript = e.target.value; markDirty(); });
  $("post-script").addEventListener("input", (e) => { state.postScript = e.target.value; markDirty(); });

  // 侧边栏
  $("saved-new").addEventListener("click", () => { newTab(); toast("已新建空白请求"); });
  $("saved-import").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    doImportFile(f).catch((err) => toast(err.message || "导入失败", "err"));
  });
  $("saved-search").addEventListener("input", (e) => { state.savedFilter = e.target.value; renderSaved(); renderHistory(); });
  document.querySelectorAll(".side-tab").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".side-tab").forEach((x) => x.classList.toggle("active", x === b));
      const which = b.dataset.stab;
      $("panel-saved").classList.toggle("hidden", which !== "saved");
      $("panel-history").classList.toggle("hidden", which !== "history");
      $("saved-new").classList.toggle("hidden", which !== "saved");
      $("saved-import").classList.toggle("hidden", which !== "saved");
      $("saved-collapse-all").classList.toggle("hidden", which !== "saved");
      $("history-clear").classList.toggle("hidden", which !== "history");
      $("saved-search").placeholder = which === "saved" ? "搜索请求..." : "搜索历史...";
    });
  });
  $("saved-collapse-all").addEventListener("click", () => {
    const groups = state.saved.groups || [];
    const anyOpen = groups.some((g) => !g.collapsed);
    groups.forEach((g) => { g.collapsed = anyOpen; });
    renderSaved();
  });
  $("nav-env").addEventListener("click", openEnvModal);
  $("nav-ssh").addEventListener("click", openSshModal);
  $("nav-theme").addEventListener("click", (e) => {
    e.stopPropagation();
    $("theme-menu").classList.toggle("open");
  });
  $("theme-menu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => $("theme-menu").classList.remove("open"));
  $("nav-legacy").addEventListener("click", async () => {
    await api("/api/switch_legacy");
  });
  $("nav-ai").addEventListener("click", openAiModal);
  $("ai-preset").addEventListener("change", applyAiPreset);
  $("ai-save").addEventListener("click", saveAiConfig);
  $("ai-pre-fab").addEventListener("click", () => openAiGenModal("pre"));
  $("ai-post-fab").addEventListener("click", () => openAiGenModal("post"));
  $("ai-gen-run").addEventListener("click", runAiGen);
  $("ai-gen-apply").addEventListener("click", applyAiGen);
  $("ai-gen-prompt").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("ai-gen-run").click();
  });
  $("history-clear").addEventListener("click", async () => {
    await api("/api/clear_history");
    state.history = [];
    renderHistory();
    toast("历史已清空", "ok");
  });
  $("env-add").addEventListener("click", () => {
    state.envs.push({ alias: "", key: "", algo: "MD5", param_name: "sign", enable_sign: true, custom_params: [], use_ssh: false, ssh_target: "" });
    renderEnvTable();
  });
  $("ssh-save").addEventListener("click", saveSsh);
  $("ssh-target-add").addEventListener("click", () => {
    if (!state.ssh.targets) state.ssh.targets = [];
    state.ssh.targets.push({ label: "", host: "", port: "22", user: "" });
    renderSshTargets();
  });
  $("ssh-test-jump").addEventListener("click", async () => { await api("/api/test_ssh", { target: false }); });
  $("ssh-test-target").addEventListener("click", async () => {
    const label = env_ssh_label() || (state.ssh.targets && state.ssh.targets[0] && state.ssh.targets[0].label) || "";
    await api("/api/test_ssh", { target: true, label });
  });

  // 通用输入 / 确认弹窗
  $("prompt-ok").addEventListener("click", () => {
    const v = $("prompt-input").value.trim();
    closeModal();
    if (promptResolve) promptResolve(v || null);
    promptResolve = null;
  });
  $("prompt-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("prompt-ok").click();
  });
  $("confirm-ok").addEventListener("click", () => {
    closeModal();
    if (confirmResolve) confirmResolve(true);
    confirmResolve = null;
  });

  // 保存弹窗分组联动
  $("save-group").addEventListener("change", (e) => {
    $("save-newgroup").classList.toggle("hidden", e.target.value !== "(新建分组)");
  });

  // 模态框关闭（遮罩点击等同「关闭」按钮，保留各弹窗的关闭前逻辑）
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeModal));
  $("modal-prompt").querySelector("[data-close]").addEventListener("click", settleModals);
  $("modal-confirm").querySelector("[data-close]").addEventListener("click", settleModals);
  $("modal-mask").addEventListener("click", (e) => {
    if (e.target !== $("modal-mask")) return;
    const open = [...document.querySelectorAll(".modal")].find((m) => m.style.display === "flex");
    const closeBtn = open && open.querySelector("[data-close]");
    if (closeBtn) closeBtn.click();
    else closeModal();
  });
  // 环境管理关闭时保存
  $("modal-env").querySelector("[data-close]").addEventListener("click", saveEnvs);

  // 快捷键
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); doGenerate(); }
    if (e.ctrlKey && e.shiftKey && e.key === "Enter") { e.preventDefault(); doExec(); }
    if (e.ctrlKey && (e.key === "t" || e.key === "T")) { e.preventDefault(); newTab(); }
    if (e.key === "Escape") { settleModals(); closeModal(); }
  });

  // 事件轮询
  setInterval(pollEvents, 300);
}

/* ─────────── 主题系统 ─────────── */
const THEMES = [
  { id: "dark",     name: "深空蓝",   bg: "#070b16", ac: "#5b8cff" },
  { id: "light",    name: "晨雾白",   bg: "#eef2f9", ac: "#2f6bff" },
  { id: "obsidian", name: "曜石黑",   bg: "#000000", ac: "#38bdf8" },
  { id: "violet",   name: "暗夜紫",   bg: "#0e0a1a", ac: "#a78bfa" },
  { id: "forest",   name: "墨绿森林", bg: "#071310", ac: "#34d399" },
  { id: "qq",       name: "经典QQ",   bg: "#e8f3fd", ac: "#12b7f5" },
  { id: "sunset",   name: "阳光橙",   bg: "#fdf3ea", ac: "#f97316" },
];
function applyTheme(id, persist = true) {
  document.documentElement.dataset.theme = id;
  if (persist) { try { localStorage.setItem("cg-theme", id); } catch (e) { /* 忽略 */ } }
  document.querySelectorAll("#theme-menu button").forEach((b) =>
    b.classList.toggle("active", b.dataset.themeId === id));
}
function renderThemeMenu() {
  const menu = $("theme-menu");
  menu.innerHTML = "";
  THEMES.forEach((t) => {
    const b = document.createElement("button");
    b.dataset.themeId = t.id;
    b.innerHTML = `<span class="theme-swatch" style="background:linear-gradient(135deg, ${t.bg} 55%, ${t.ac} 55%)"></span>${t.name}`;
    b.addEventListener("click", () => {
      applyTheme(t.id);
      $("theme-menu").classList.remove("open");
      toast(`主题：${t.name}`);
    });
    menu.appendChild(b);
  });
}

/* ─────────── 启动 ─────────── */
async function init() {
    renderMethodMenu();
    renderThemeMenu();
    let savedTheme = "dark";
    try { savedTheme = localStorage.getItem("cg-theme") || "dark"; } catch (e) { /* 忽略 */ }
    if (!THEMES.some((t) => t.id === savedTheme)) savedTheme = "dark";
    applyTheme(savedTheme, false);
    bindEvents();
    newTab();

    const j = await apiGet("/api/state");
    if (j.error) {
        toast("后端连接失败，请重启应用", "err");
        return;
    }
    state.envs = j.envs || [];
    state.saved = j.saved || { groups: [] };
    state.ssh = j.ssh || {};
    state.ai = j.ai || {};
    state.ssh_ready = !!j.ssh_ready;
    if (j.logo) $("brand-logo").src = j.logo;
    renderEnvSelect();
    updateConnBadge();
    renderSaved();
    refreshHistory();
    toast("星轨控制台已就绪 ⚡", "ok");
}

/* setHtml 场景下 DOMContentLoaded 可能早于本脚本触发，需按 readyState 判断 */
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
