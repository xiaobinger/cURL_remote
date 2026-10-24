/* Dashboard 十二列可编辑网格：布局规范化、拖放排序、显隐与文件偏好保存。 */
"use strict";

const DASHBOARD_COMPONENT_DEFAULTS = [
  { id: "stats", visible: true, width: 12 },
  { id: "calendar", visible: true, width: 8 },
  { id: "almanac", visible: true, width: 4 },
  { id: "market", visible: true, width: 8 },
  { id: "todo", visible: true, width: 4 },
  { id: "terminal", visible: true, width: 12 },
];
const DASHBOARD_COMPONENT_NAMES = {
  stats: "统计概览", calendar: "日历", almanac: "黄历",
  market: "行情 / K线", todo: "待办", terminal: "终端",
};
const DASHBOARD_WIDTHS = [4, 6, 8, 12];

function normalize_dashboard_layout(value) {
  const defaults = new Map(DASHBOARD_COMPONENT_DEFAULTS.map((item) => [item.id, item]));
  const source = value && Array.isArray(value.items) ? value.items : [];
  const result = [];
  const seen = new Set();
  source.forEach((item) => {
    if (!item || typeof item !== "object" || !defaults.has(item.id) || seen.has(item.id)) return;
    const fallback = defaults.get(item.id);
    seen.add(item.id);
    result.push({
      id: item.id,
      visible: item.visible !== false,
      width: DASHBOARD_WIDTHS.includes(item.width) ? item.width : fallback.width,
    });
  });
  DASHBOARD_COMPONENT_DEFAULTS.forEach((item) => {
    if (!seen.has(item.id)) result.push({ ...item });
  });
  return { version: 1, items: result };
}

class DashboardLayout {
  constructor() {
    this.items = [];
    this.editing = false;
    this.dragId = null;
  }

  mount(layout) {
    this.items = normalize_dashboard_layout(layout).items.map((item) => ({ ...item }));
    this.bindCards();
    this.apply();
    document.querySelector("#dash-edit").onclick = () => this.setEditing(!this.editing);
    document.querySelector("#dash-reset").onclick = () => this.reset();
  }

  apply() {
    const grid = document.querySelector(".dash-grid");
    this.items.forEach((item, index) => {
      const card = grid.querySelector(`[data-component="${item.id}"]`);
      if (!card) return;
      card.style.order = String(index);
      card.style.gridColumn = `span ${item.width}`;
      card.classList.toggle("component-hidden", !item.visible);
      card.querySelectorAll("[data-width]").forEach((button) => {
        const selected = Number(button.dataset.width) === item.width;
        button.classList.toggle("active", selected);
        button.setAttribute("aria-pressed", String(selected));
      });
    });
    this.renderHiddenComponents();
    window.dispatchEvent(new Event("resize"));
  }

  save() {
    const dashboard = { version: 1, items: this.items.map((item) => ({ ...item })) };
    if (typeof state !== "undefined") state.dashboard = dashboard;
    return api("/api/ui_prefs", { dashboard });
  }

  setEditing(value) {
    this.editing = !!value;
    this.dragId = null;
    document.querySelector("#dashboard").classList.toggle("layout-editing", this.editing);
    const button = document.querySelector("#dash-edit");
    button.textContent = this.editing ? "完成" : "编辑布局";
    button.setAttribute("aria-pressed", String(this.editing));
  }

  add(id) {
    const item = this.items.find((entry) => entry.id === id);
    if (!item) return Promise.resolve();
    item.visible = true;
    this.apply();
    if (typeof renderDashboard === "function") renderDashboard();
    return this.save();
  }

  reset() {
    this.items = DASHBOARD_COMPONENT_DEFAULTS.map((fallback) => {
      const old = this.items.find((item) => item.id === fallback.id);
      return { id: fallback.id, visible: true, width: old ? old.width : fallback.width };
    });
    this.apply();
    if (typeof renderDashboard === "function") renderDashboard();
    return this.save();
  }

  bindCards() {
    document.querySelectorAll(".dash-grid [data-component]").forEach((card) => {
      this.ensureCardTools(card);
      const handle = card.querySelector(".dash-drag");
      handle.ondragstart = (event) => {
        if (!this.editing) { event.preventDefault(); return; }
        this.dragId = card.dataset.component;
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", this.dragId);
        }
      };
      handle.ondragend = () => { this.dragId = null; };
      card.ondragover = (event) => {
        if (this.editing && this.dragId) {
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        }
      };
      card.ondrop = (event) => {
        event.preventDefault();
        if (!this.editing) return;
        const from = this.items.findIndex((item) => item.id === this.dragId);
        const to = this.items.findIndex((item) => item.id === card.dataset.component);
        this.dragId = null;
        if (from < 0 || to < 0 || from === to) return;
        const [moved] = this.items.splice(from, 1);
        this.items.splice(to, 0, moved);
        this.apply();
        this.save();
      };
      card.querySelector(".dash-hide").onclick = () => {
        const item = this.items.find((entry) => entry.id === card.dataset.component);
        if (!item) return;
        item.visible = false;
        this.apply();
        this.save();
      };
      card.querySelectorAll("[data-width]").forEach((button) => {
        button.onclick = () => {
          const item = this.items.find((entry) => entry.id === card.dataset.component);
          if (!item) return;
          item.width = Number(button.dataset.width);
          this.apply();
          this.save();
        };
      });
    });
  }

  ensureCardTools(card) {
    if (card.querySelector(".dash-card-tools")) return;
    const id = card.dataset.component;
    const name = DASHBOARD_COMPONENT_NAMES[id] || id;
    const tools = document.createElement("div");
    tools.className = "dash-card-tools";
    tools.setAttribute("aria-label", `${name}布局工具`);
    const drag = document.createElement("button");
    drag.type = "button";
    drag.className = "dash-drag";
    drag.draggable = true;
    drag.textContent = "⠿";
    drag.title = `拖动排序：${name}`;
    tools.appendChild(drag);
    DASHBOARD_WIDTHS.forEach((width) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.width = String(width);
      button.textContent = String(width);
      button.title = `${name}宽度：${width}/12`;
      tools.appendChild(button);
    });
    const hide = document.createElement("button");
    hide.type = "button";
    hide.className = "dash-hide";
    hide.textContent = "×";
    hide.title = `隐藏${name}`;
    tools.appendChild(hide);
    card.appendChild(tools);
  }

  renderHiddenComponents() {
    const box = document.querySelector("#dash-add-components");
    if (!box) return;
    box.innerHTML = "";
    const hidden = this.items.filter((item) => !item.visible);
    if (!hidden.length) {
      const empty = document.createElement("span");
      empty.className = "dash-add-empty";
      empty.textContent = "所有组件均已显示";
      box.appendChild(empty);
      return;
    }
    const label = document.createElement("span");
    label.className = "dash-add-label";
    label.textContent = "重新添加：";
    box.appendChild(label);
    hidden.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-mini";
      button.dataset.addComponent = item.id;
      button.textContent = `＋ ${DASHBOARD_COMPONENT_NAMES[item.id] || item.id}`;
      button.onclick = () => this.add(item.id);
      box.appendChild(button);
    });
  }
}

window.DashboardLayout = DashboardLayout;
window.normalize_dashboard_layout = normalize_dashboard_layout;
