/* ═══════════════════════════════════════════════════════════
   cURL Generator · 星轨控制台 前端逻辑
   通过本地 HTTP API（window.API_BASE）与 Python 后端通信
   ═══════════════════════════════════════════════════════════ */
"use strict";

/* ─────────── 状态 ─────────── */
const state = {
  method: "GET",
  url: "",
  env: "(不选择环境)",
  headers: [],
  params: [],
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
  curl: "",
  envs: [],
  saved: { groups: [] },
  history: [],
  ssh: {},
  currentReq: null, // {name, group}
  executing: false,
  eventCursor: 0,
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
  /* pct<0 变暗、>0 变亮（百分比） */
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

/* ─────────── 表单采集 / 回填 ─────────── */
function collectForm() {
  const clean = (list) => list
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

function fillForm(d) {
  state.method = d.method || "GET";
  state.url = d.url || "";
  state.env = d.env || "(不选择环境)";
  state.headers = (d.headers || []).map((p) => [p[0], p[1]]);
  state.params = (d.params || []).map((p) => [p[0], p[1]]);
  state.bodyType = d.body_type || "JSON";
  state.body = d.body || "";
  state.authType = d.auth_type || "None";
  state.authValue = d.auth_value || "";
  state.authUser = d.auth_user || "";
  state.authPass = d.auth_pass || "";
  state.authHeader = d.auth_header_name || "X-API-Key";
  state.ssl = !!d.ssl;
  state.silent = !!d.silent;
  state.proxy = d.proxy || "";
  state.timeout = d.timeout || "";

  syncControlsFromState();
  state.currentReq = d.name ? { name: d.name, group: d._group_name } : null;
  toast(d.name ? `已加载: ${d.name}` : "表单已重置", "ok");
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
  // Auth
  $("auth-type").value = state.authType;
  renderAuthFields();
  // Options
  $("opt-ssl").checked = state.ssl;
  $("opt-silent").checked = state.silent;
  $("opt-proxy").value = state.proxy;
  $("opt-timeout").value = state.timeout;
}

/* ─────────── KV 编辑器 ─────────── */
function renderKV(container, list, key) {
  container.innerHTML = "";
  if (!list.length) {
    container.innerHTML = '<div class="kv-empty">暂无数据，点击「+ 添加」新增一行</div>';
    return;
  }
  list.forEach(([k, v], i) => {
    const row = document.createElement("div");
    row.className = "kv-row";
    const ki = document.createElement("input");
    ki.placeholder = "Key";
    ki.value = k || "";
    ki.addEventListener("input", () => { state[key][i][0] = ki.value; });
    const vi = document.createElement("input");
    vi.placeholder = "Value";
    vi.value = v || "";
    vi.addEventListener("input", () => { state[key][i][1] = vi.value; });
    const del = document.createElement("button");
    del.className = "row-del";
    del.title = "删除";
    del.textContent = "×";
    del.addEventListener("click", () => {
      state[key].splice(i, 1);
      renderKV(container, list, key);
    });
    row.append(ki, vi, del);
    container.appendChild(row);
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
    inp.addEventListener("input", () => onInput(inp.value));
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
$("env-select").addEventListener("change", (e) => { state.env = e.target.value; });

/* ─────────── 方法下拉 ─────────── */
function renderMethodMenu() {
  const menu = $("method-menu");
  menu.innerHTML = "";
  Object.keys(METHOD_COLORS).forEach((m) => {
    const b = document.createElement("button");
    b.innerHTML = `<span class="dot" style="background:${METHOD_COLORS[m]}"></span>${m}`;
    b.addEventListener("click", () => {
      state.method = m;
      setMethodChip(m);
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

/* ─────────── 侧边栏：快捷请求 / 历史 ─────────── */
function renderSaved() {
  const box = $("saved-list");
  box.innerHTML = "";
  const groups = state.saved.groups || [];
  if (!groups.length) {
    box.innerHTML = '<div class="kv-empty">还没有保存的请求</div>';
    return;
  }
  groups.forEach((g) => {
    const wrap = document.createElement("div");
    wrap.className = "saved-group" + (g.collapsed ? " collapsed" : "");
    const head = document.createElement("button");
    head.className = "saved-group-head";
    head.innerHTML = `<span class="arrow">▾</span>${escapeHtml(g.name)}`;
    head.addEventListener("click", () => {
      g.collapsed = !g.collapsed;
      wrap.classList.toggle("collapsed");
    });
    const body = document.createElement("div");
    body.className = "saved-group-body";
    (g.requests || []).forEach((r) => {
      const item = document.createElement("button");
      item.className = "saved-item";
      item.style.setProperty("--tag-c", METHOD_COLORS[r.method] || "var(--accent)");
      item.innerHTML = `<span class="method-tag">${escapeHtml(r.method || "GET")}</span>` +
        `<span class="nm">${escapeHtml(r.name)}</span>`;
      const del = document.createElement("span");
      del.className = "del";
      del.textContent = "×";
      del.title = "删除";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        const j = await api("/api/delete_request", { group: g.name, name: r.name });
        if (j.ok) {
          state.saved = j.saved;
          renderSaved();
          toast("已删除", "ok");
        }
      });
      item.appendChild(del);
      item.addEventListener("click", () => fillForm({ ...r, _group_name: g.name }));
      body.appendChild(item);
    });
    if (!(g.requests || []).length) {
      body.innerHTML = '<div class="kv-empty" style="padding:8px 0 8px 22px">空分组</div>';
    }
    wrap.append(head, body);
    box.appendChild(wrap);
  });
}

function renderHistory() {
  const box = $("history-list");
  box.innerHTML = "";
  if (!state.history.length) {
    box.innerHTML = '<div class="kv-empty">暂无历史</div>';
    return;
  }
  state.history.forEach((h) => {
    const el = document.createElement("div");
    el.className = "history-item";
    el.title = "点击在预览中查看";
    el.innerHTML = `<time>${escapeHtml(h.time || "")}</time>${escapeHtml(h.method)} ${escapeHtml(h.url)}`;
    el.addEventListener("click", () => {
      state.curl = h.curl;
      $("curl-preview").innerHTML = highlightCurl(h.curl);
      toast("已载入预览（历史快照）");
    });
    box.appendChild(el);
  });
}

/* ─────────── 响应面板 ─────────── */
function respClear() {
  $("response-view").textContent = "";
  $("response-view").classList.remove("streaming");
}
function respAppend(text) {
  const v = $("response-view");
  v.textContent += (v.textContent && !v.textContent.endsWith("\n") ? "\n" : "") + text;
  v.scrollTop = v.scrollHeight;
}
function respSetStatus(text, kind) {
  const el = $("resp-status");
  el.textContent = text;
  el.className = `resp-status ${kind || ""}`;
}

/* ─────────── 核心动作 ─────────── */
async function doGenerate() {
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
  $("save-name").value = state.currentReq?.name || "";
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
  const data = collectForm();
  const j = await api("/api/save_request", { data, group, name, update_old: state.currentReq?.name || "" });
  if (j.error) { toast(j.error, "err"); return; }
  state.saved = j.saved;
  state.currentReq = { name, group };
  renderSaved();
  closeModal();
  toast(`已保存: ${name}`, "ok");
}

async function doExec() {
  const data = collectForm();
  if (!data.url) { toast("请先输入 URL", "err"); return; }
  if (!state.curl) await doGenerate();
  openModal("modal-exec");
}

async function doExecConfirm() {
  const mode = document.querySelector('input[name="execmode"]:checked').value;
  closeModal();
  const data = collectForm();
  state.executing = true;
  $("btn-exec").disabled = true;
  $("btn-stop").disabled = false;
  respClear();
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
    $("conn-badge").textContent = "● 本地模式";
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
    tbody.innerHTML = '<tr><td colspan="7" class="kv-empty">暂无环境，点击「+ 添加环境」</td></tr>';
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
  $("ssh-th").value = state.ssh.target_host || "";
  $("ssh-tp").value = state.ssh.target_port || "";
  $("ssh-tu").value = state.ssh.target_user || "";
  $("ssh-test-status").textContent = "";
  openModal("modal-ssh");
}

async function saveSsh() {
  const data = {
    jump_host: $("ssh-jh").value.trim(),
    jump_port: $("ssh-jp").value.trim(),
    jump_user: $("ssh-ju").value.trim(),
    jump_pass: $("ssh-jw").value,
    target_host: $("ssh-th").value.trim(),
    target_port: $("ssh-tp").value.trim(),
    target_user: $("ssh-tu").value.trim(),
  };
  const j = await api("/api/save_ssh", data);
  if (j.ok) {
    state.ssh = data;
    toast("SSH 配置已保存 ✓", "ok");
    closeModal();
  } else toast("保存失败", "err");
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
      $("conn-badge").textContent = "● 本地模式";
      const code = ev.code;
      let txt, kind;
      if (code === 0) { txt = "成功 (exit 0)"; kind = "ok"; }
      else if (code === -1) { txt = "失败"; kind = "err"; }
      else if (code === -2) { txt = "已停止"; kind = "busy"; }
      else if (code === -3) { txt = "超时"; kind = "err"; }
      else { txt = `exit ${code}`; kind = "busy"; }
      respAppend(`──── 状态: ${txt} ────`);
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

  // 标签页
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab-page").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      document.querySelector(`.tab-page[data-page="${t.dataset.tab}"]`).classList.add("active");
    });
  });

  // KV 增删
  $("headers-add").addEventListener("click", () => { state.headers.push(["", ""]); renderKV($("headers-table"), state.headers, "headers"); });
  $("headers-clear").addEventListener("click", () => { state.headers = []; renderKV($("headers-table"), state.headers, "headers"); });
  $("params-add").addEventListener("click", () => { state.params.push(["", ""]); renderKV($("params-table"), state.params, "params"); });
  $("params-clear").addEventListener("click", () => { state.params = []; renderKV($("params-table"), state.params, "params"); });

  // Body
  $("body-type").addEventListener("change", (e) => state.bodyType = e.target.value);
  $("btn-pretty").addEventListener("click", () => {
    try {
      const obj = JSON.parse($("body-input").value);
      $("body-input").value = JSON.stringify(obj, null, 2);
      toast("JSON 已美化 ✓", "ok");
    } catch (e) { toast(`JSON 格式错误: ${e.message}`, "err"); }
  });
  $("btn-minify").addEventListener("click", () => {
    try {
      const obj = JSON.parse($("body-input").value);
      $("body-input").value = JSON.stringify(obj);
      toast("JSON 已压缩 ✓", "ok");
    } catch (e) { toast(`JSON 格式错误: ${e.message}`, "err"); }
  });

  // Auth
  $("auth-type").addEventListener("change", (e) => { state.authType = e.target.value; renderAuthFields(); });

  // Options
  $("opt-ssl").addEventListener("change", (e) => state.ssl = e.target.checked);
  $("opt-silent").addEventListener("change", (e) => state.silent = e.target.checked);

  // 侧边栏
  $("nav-env").addEventListener("click", openEnvModal);
  $("nav-ssh").addEventListener("click", openSshModal);
  $("nav-theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme;
    document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
    toast(cur === "dark" ? "已切换亮色主题" : "已切换暗色主题");
  });
  $("nav-legacy").addEventListener("click", async () => {
    await api("/api/switch_legacy");
  });
  $("history-clear").addEventListener("click", async () => {
    await api("/api/clear_history");
    state.history = [];
    renderHistory();
    toast("历史已清空", "ok");
  });
  $("env-add").addEventListener("click", () => {
    state.envs.push({ alias: "", key: "", algo: "MD5", param_name: "sign", enable_sign: true, custom_params: [] });
    renderEnvTable();
  });
  $("ssh-save").addEventListener("click", saveSsh);
  $("ssh-test-jump").addEventListener("click", async () => { await api("/api/test_ssh", { target: false }); });
  $("ssh-test-target").addEventListener("click", async () => { await api("/api/test_ssh", { target: true }); });

  // 保存弹窗分组联动
  $("save-group").addEventListener("change", (e) => {
    $("save-newgroup").classList.toggle("hidden", e.target.value !== "(新建分组)");
  });

  // 模态框关闭（遮罩点击等同「关闭」按钮，保留各弹窗的关闭前逻辑）
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeModal));
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
    if (e.key === "Escape") closeModal();
  });

  // 事件轮询
  setInterval(pollEvents, 300);
}

/* ─────────── 启动 ─────────── */
async function init() {
    renderMethodMenu();
    bindEvents();
    syncControlsFromState();

    const j = await apiGet("/api/state");
    if (j.error) {
        toast("后端连接失败，请重启应用", "err");
        return;
    }
    state.envs = j.envs || [];
    state.saved = j.saved || { groups: [] };
    state.ssh = j.ssh || {};
    if (j.logo) $("brand-logo").src = j.logo;
    $("conn-badge").textContent = j.ssh_ready ? "● SSH 已配置" : "● 本地模式";
    renderEnvSelect();
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
