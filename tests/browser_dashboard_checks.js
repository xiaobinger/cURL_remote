/*
 * Task 2 browser assertions. Load through Chrome DevTools evaluate_script or
 * navigate_page initScript. The fetch double keeps all preferences in memory;
 * it never reads or writes the user's real ~/.cURL directory.
 */
(() => {
  "use strict";

  const DEFAULT_LAYOUT = {
    version: 1,
    items: [
      { id: "stats", visible: true, width: 12 },
      { id: "calendar", visible: true, width: 8 },
      { id: "almanac", visible: true, width: 4 },
      { id: "market", visible: true, width: 8 },
      { id: "todo", visible: true, width: 4 },
      { id: "terminal", visible: true, width: 12 },
    ],
  };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  async function waitFor(predicate, message, timeout = 3000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (predicate()) return;
      await tick();
    }
    throw new Error(message);
  }

  function installDashboardTestApi() {
    const initial = window.__dashboardTestInitialPrefs;
    window.__dashboardTestPrefs = initial ? clone(initial) : {};
    window.__dashboardTestRequests = [];
    window.__dashboardTestSaves = [];
    window.__dashboardTestSaveCompletions = [];
    window.__dashboardTestSaveGate = null;
    window.__dashboardTestSaveFailure = null;
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      const path = target.slice(target.indexOf("/api/"));
      const method = options.method || "GET";
      window.__dashboardTestRequests.push({ path, method });
      let result = {};
      if (path === "/api/state") {
        result = {
          envs: [], saved: { groups: [] }, ssh: {}, ai: {},
          stocks: [{ code: "sh000001", name: "上证指数" }],
          ui: clone(window.__dashboardTestPrefs), logo: "", ssh_ready: false,
        };
      } else if (path === "/api/history" || path.startsWith("/api/events")) {
        result = path === "/api/history" ? [] : { events: [], next: 0 };
      } else if (path.startsWith("/api/todos")) {
        result = { todos: [] };
      } else if (path.startsWith("/api/holidays")) {
        result = { map: {} };
      } else if (path.startsWith("/api/astock_kline")) {
        const query = path.split("?")[1] || "";
        const month = (query.split("&").find((part) => part.startsWith("month=")) || "month=2026-10").split("=")[1];
        result = {
          days: [1, 2, 3, 4, 5, 6].map((day) => ({
            date: `${month}-${String(day).padStart(2, "0")}`,
            open: 3000 + day, close: 3001 + day,
            high: 3003 + day, low: 2999 + day, vol: 1000000 + day,
          })),
          prev_closes: [3000, 3001, 3002, 3003, 3004],
        };
      } else if (path.startsWith("/api/astock")) {
        result = { trading: true, open: 3000, close: 3001, pct: 0.1, live: false };
      } else if (path === "/api/ui_prefs" && method === "POST") {
        const body = JSON.parse(options.body || "{}");
        window.__dashboardTestSaves.push(clone(body));
        const gate = window.__dashboardTestSaveGate;
        if (body.dashboard && gate && !gate.entered && !gate.released) {
          gate.entered = true;
          await new Promise((resolve) => {
            gate.release = () => {
              gate.released = true;
              resolve();
            };
          });
        }
        const failure = window.__dashboardTestSaveFailure;
        if (body.dashboard && failure && failure.remaining) {
          failure.remaining -= 1;
          if (failure.kind === "network") {
            window.__dashboardTestSaveCompletions.push({ body: clone(body), ok: false });
            throw new TypeError(failure.message);
          }
          result = { error: failure.message };
          window.__dashboardTestSaveCompletions.push({ body: clone(body), ok: false });
          return {
            ok: false,
            status: failure.status,
            json: async () => clone(result),
          };
        }
        if (body.dashboard) window.__dashboardTestPrefs.dashboard = clone(body.dashboard);
        if (body.theme) window.__dashboardTestPrefs.theme = body.theme;
        if (body.stock) window.__dashboardTestPrefs.stock = body.stock;
        result = { ok: true, ui: clone(window.__dashboardTestPrefs) };
        window.__dashboardTestSaveCompletions.push({ body: clone(body), ok: true });
      }
      return { ok: true, status: 200, json: async () => clone(result) };
    };
  }

  async function runDashboardBrowserChecks(options = {}) {
    const restoreOnly = !!options.restoreOnly;
    const results = [];
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const check = (name, fn) => {
      if (options.focus && !name.includes(options.focus)) return;
      fn();
      results.push(name);
    };
    const asyncCheck = async (name, fn) => {
      if (options.focus && !name.includes(options.focus)) return;
      await fn();
      results.push(name);
    };
    const visible = (el) => !!el && getComputedStyle(el).display !== "none" && !el.classList.contains("component-hidden");
    const card = (id) => document.querySelector(`.dash-grid [data-component="${id}"]`);
    const click = async (selector) => {
      const el = document.querySelector(selector);
      assert(el, `缺少点击目标 ${selector}`);
      el.click();
      await tick();
    };

    check("启动默认显示 Dashboard", () => {
      assert(!document.querySelector("#dashboard").classList.contains("hidden"), "启动应显示 Dashboard");
      assert(document.querySelectorAll(".req-tab").length === 0, "启动不应自动新建请求");
    });
    check("六个独立组件与终端固定 ID", () => {
      const ids = [...document.querySelectorAll(".dash-grid > [data-component]")].map((el) => el.dataset.component);
      assert(ids.length === 6, `应有六个组件，实际 ${ids.join(",")}`);
      for (const id of ["stats", "calendar", "almanac", "market", "todo", "terminal"]) assert(card(id), `缺少组件 ${id}`);
      for (const id of ["cal-stock", "cal-kline-head", "cal-kline"]) assert(card("market").contains(document.getElementById(id)), `${id} 应位于 market`);
      for (const id of ["terminal-status", "terminal-output", "terminal-prompt", "terminal-input", "terminal-jump", "terminal-target", "terminal-fullscreen", "terminal-local", "terminal-interrupt", "terminal-reconnect", "terminal-clear"]) {
        assert(document.getElementById(id), `缺少终端固定 ID ${id}`);
      }
      assert(document.getElementById("stock-switch-btn"), "行情卡片应包含常驻标的切换按钮");
      assert(document.getElementById("stock-switch-name"), "标的切换按钮应包含名称元素");
      assert(document.querySelector("#dash-edit"), "应存在布局编辑按钮");
      assert(document.querySelector("#dash-reset"), "应存在恢复默认按钮");
    });
    if (restoreOnly) {
      check("刷新后恢复文件布局偏好", () => {
        const expected = window.__dashboardTestInitialPrefs.dashboard;
        assert(JSON.stringify(dashboardLayout.items) === JSON.stringify(expected.items), "刷新后的布局应与文件偏好一致");
        expected.items.forEach((item) => {
          assert(card(item.id).style.gridColumn === `span ${item.width}`, `${item.id} 刷新后宽度未恢复`);
          assert(card(item.id).classList.contains("component-hidden") === !item.visible, `${item.id} 刷新后显隐未恢复`);
          assert(Number(card(item.id).style.order) === expected.items.indexOf(item), `${item.id} 刷新后顺序未恢复`);
        });
      });
      return { ok: true, results, prefs: clone(window.__dashboardTestPrefs) };
    }

    check("前后端默认布局一致", () => {
      const expected = Object.fromEntries(DEFAULT_LAYOUT.items.map((item) => [item.id, item.width]));
      assert(dashboardLayout.items.length === 6, "默认布局应包含六项");
      dashboardLayout.items.forEach((item) => assert(item.visible && item.width === expected[item.id], `${item.id} 默认宽度或显隐错误`));
    });
    check("非法宽度逐项回退且补齐去重", () => {
      const normalized = normalize_dashboard_layout({ items: [
        { id: "calendar", visible: false, width: 7 },
        { id: "calendar", visible: true, width: 12 },
        { id: "terminal", visible: true, width: "8" },
        { id: "unknown", visible: true, width: 12 },
      ] });
      assert(normalized.version === 1 && normalized.items.length === 6, "规范化应保留版本 1 并补齐六项");
      assert(normalized.items[0].width === 8 && normalized.items[0].visible === false, "calendar 非法宽度应回退 8");
      assert(normalized.items[1].width === 12, "terminal 非法宽度应回退 12");
    });
    check("十二列网格与普通模式紧凑控件", () => {
      assert(getComputedStyle(document.querySelector(".dash-grid")).gridTemplateColumns.split(" ").length === 12, "网格应有十二列");
      assert(!document.querySelector("#dashboard").classList.contains("layout-editing"), "默认不应处于编辑模式");
      document.querySelectorAll(".dash-card-tools").forEach((el) => assert(getComputedStyle(el).display === "none", "普通模式不应显示卡片编辑工具"));
    });
    check("空待办保留组件并显示提示", () => {
      assert(visible(card("todo")), "空待办不应隐藏整个组件");
      assert(document.querySelector("#todo-list").textContent.includes("当日暂无待办"), "应显示当日暂无待办");
      assert(!card("todo").classList.contains("hidden"), "不得使用 hidden 移除待办组件");
    });

    check("日历、黄历、待办与行情共享日期", () => {
      const cells = [...document.querySelectorAll("#cal-grid .cal-cell:not(.cal-out)")];
      assert(cells.length > 3, "日历应有可点击日期");
      cells[2].click();
      const expected = `${dash.sel.y}-${String(dash.sel.m).padStart(2, "0")}-${String(dash.sel.d).padStart(2, "0")}`;
      assert(document.querySelector("#alm-date").textContent === expected, "黄历日期未联动");
      assert(document.querySelector("#todo-date").textContent === `${dash.sel.m} 月 ${dash.sel.d} 日`, "待办日期未联动");
      assert(dash.astockDate.endsWith(expected), "行情日期未联动");
      assert(dash.klineMonth.startsWith(`${curStock().code}|${dash.sel.y}-${String(dash.sel.m).padStart(2, "0")}`), "K线月份未联动");
    });
    await tick();
    check("K线点击仍更新同一选中日期", () => {
      assert(dash.klineChart, "K线图应已渲染");
      const pack = dash.kline[dash.klineMonth];
      const dataIndex = Math.min(4, pack.days.length - 1);
      const point = dash.klineChart.convertToPixel({ seriesIndex: 0 }, [dataIndex, pack.days[dataIndex].close]);
      const canvas = document.querySelector("#cal-kline canvas");
      const rect = canvas.getBoundingClientRect();
      const init = { bubbles: true, clientX: rect.left + point[0], clientY: rect.top + point[1], button: 0 };
      canvas.dispatchEvent(new MouseEvent("mousedown", init));
      canvas.dispatchEvent(new MouseEvent("mouseup", init));
      canvas.dispatchEvent(new MouseEvent("click", init));
      const expected = pack.days[dataIndex].date;
      const actual = `${dash.sel.y}-${String(dash.sel.m).padStart(2, "0")}-${String(dash.sel.d).padStart(2, "0")}`;
      assert(actual === expected, `K线点击日期应更新为 ${expected}，实际 ${actual}`);
      assert(document.querySelector("#alm-date").textContent === expected, "K线点击后黄历未共享日期");
      assert(document.querySelector("#todo-date").textContent === `${dash.sel.m} 月 ${dash.sel.d} 日`, "K线点击后待办未共享日期");
    });

    await click("#dash-edit");
    check("编辑模式显示工具且不改变卡片盒模型", () => {
      assert(document.querySelector("#dashboard").classList.contains("layout-editing"), "应进入编辑模式");
      document.querySelectorAll(".dash-card-tools").forEach((el) => assert(getComputedStyle(el).display !== "none", "编辑工具应可见"));
      assert(card("calendar").querySelector(".dash-drag").draggable, "拖动手柄应可拖动");
      assert(getComputedStyle(card("calendar").querySelector(".dash-card-tools")).position === "absolute", "编辑工具应绝对定位，避免挤压内容");
    });
    check("4/6/8/12 四种宽度生效", () => {
      for (const width of [4, 6, 8, 12]) {
        card("calendar").querySelector(`[data-width="${width}"]`).click();
        assert(card("calendar").style.gridColumn === `span ${width}`, `宽度 ${width} 未生效`);
        assert(dashboardLayout.items.find((item) => item.id === "calendar").width === width, `宽度 ${width} 未写入布局`);
      }
    });
    await tick();
    check("拖放排序并保存", () => {
      const from = card("calendar").querySelector(".dash-drag");
      const target = card("terminal");
      const before = dashboardLayout.items.map((item) => item.id);
      const dataTransfer = new DataTransfer();
      from.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer }));
      target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer }));
      target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
      const after = dashboardLayout.items.map((item) => item.id);
      assert(JSON.stringify(after) !== JSON.stringify(before), "拖放后顺序应改变");
      assert(after.indexOf("calendar") === before.indexOf("terminal"), "calendar 应移动到 terminal 原位置");
      assert(Number(card("calendar").style.order) === after.indexOf("calendar"), "拖放后 CSS order 未更新");
    });
    await tick();
    card("almanac").querySelector(".dash-hide").click();
    await tick();
    check("隐藏组件并生成重新添加入口", () => {
      assert(card("almanac").classList.contains("component-hidden"), "隐藏后应有 component-hidden");
      assert(document.querySelector('[data-add-component="almanac"]'), "编辑区应列出隐藏的 almanac");
    });
    await click('[data-add-component="almanac"]');
    check("重新添加恢复显示并保存", () => {
      assert(!card("almanac").classList.contains("component-hidden"), "重新添加后应显示");
      assert(!document.querySelector('[data-add-component="almanac"]'), "添加后入口应移除");
      assert(window.__dashboardTestSaves.some((body) => body.dashboard), "布局变更应 POST /api/ui_prefs");
      assert(window.__dashboardTestPrefs.dashboard.items.some((item) => item.id === "almanac" && item.visible), "文件偏好应保存重新添加状态");
    });
    card("market").querySelector(".dash-hide").click();
    await tick();
    const beforeRequests = window.__dashboardTestRequests.length;
    document.querySelectorAll("#cal-grid .cal-cell:not(.cal-out)")[3].click();
    await tick();
    check("不可见 market 不请求行情/K线", () => {
      const paths = window.__dashboardTestRequests.slice(beforeRequests).map((entry) => entry.path);
      assert(!paths.some((path) => path.startsWith("/api/astock")), `market 隐藏时不应请求行情/K线：${paths.join(",")}`);
    });
    await click('[data-add-component="market"]');

    const persisted = clone(window.__dashboardTestPrefs);
    await click("#dash-reset");
    check("恢复默认保留合法宽度并按默认顺序显示", () => {
      assert(JSON.stringify(dashboardLayout.items.map((item) => item.id)) === JSON.stringify(DEFAULT_LAYOUT.items.map((item) => item.id)), "恢复默认应还原组件顺序");
      dashboardLayout.items.forEach((item) => assert(item.visible, `恢复默认后 ${item.id} 应显示`));
      assert(window.__dashboardTestPrefs.dashboard.items.every((item) => item.visible), "恢复默认应保存到文件偏好");
    });
    await waitFor(() => window.__dashboardTestSaveCompletions.length === window.__dashboardTestSaves.length,
      "竞态测试前排空既有布局保存");

    await asyncCheck("连续布局保存最后快照最后写入", async () => {
      const beforeAttempts = window.__dashboardTestSaves.length;
      const beforeCompletions = window.__dashboardTestSaveCompletions.length;
      const gate = { entered: false, released: false, release: null };
      window.__dashboardTestSaveGate = gate;
      let attemptsBeforeRelease = 0;
      try {
        card("calendar").querySelector('[data-width="4"]').click();
        card("calendar").querySelector('[data-width="8"]').click();
        await waitFor(() => gate.entered, "第一张布局快照应进入 deferred 门控");
        await new Promise((resolve) => setTimeout(resolve, 30));
        attemptsBeforeRelease = window.__dashboardTestSaves.length - beforeAttempts;
        gate.release();
        await waitFor(() => window.__dashboardTestSaveCompletions.length >= beforeCompletions + 2,
          "两次布局保存均应完成");
        const completions = window.__dashboardTestSaveCompletions.slice(beforeCompletions);
        const widths = completions.map((entry) => entry.body.dashboard.items.find((item) => item.id === "calendar").width);
        assert(attemptsBeforeRelease === 1, `旧快照完成前不得发出新快照，实际请求数 ${attemptsBeforeRelease}`);
        assert(JSON.stringify(widths) === JSON.stringify([4, 8]), `完成顺序应为旧、新快照，实际 ${widths.join(",")}`);
        assert(window.__dashboardTestSaveCompletions[window.__dashboardTestSaveCompletions.length - 1]
          .body.dashboard.items.find((item) => item.id === "calendar").width === 8,
        "最后完成的必须是最后用户操作快照");
        assert(window.__dashboardTestPrefs.dashboard.items.find((item) => item.id === "calendar").width === 8,
          "内存文件偏好必须保留最后快照");
      } finally {
        if (gate.release) gate.release();
        window.__dashboardTestSaveGate = null;
      }
    });

    const checkFailureRecovery = async (kind, expectedMessage) => {
      await waitFor(() => window.__dashboardTestSaveCompletions.length === window.__dashboardTestSaves.length,
        "故障测试前排空布局保存");
      const beforeCompletions = window.__dashboardTestSaveCompletions.length;
      const messages = [];
      const unhandled = [];
      const onUnhandled = (event) => {
        unhandled.push(String(event.reason && (event.reason.message || event.reason)));
        event.preventDefault();
      };
      const originalToast = window.toast;
      window.toast = (message, type) => messages.push({ message, type });
      window.addEventListener("unhandledrejection", onUnhandled);
      window.__dashboardTestSaveFailure = {
        kind, remaining: 1, status: 500, message: expectedMessage,
      };
      try {
        card("calendar").querySelector('[data-width="6"]').click();
        card("calendar").querySelector('[data-width="12"]').click();
        await waitFor(() => window.__dashboardTestSaveCompletions.length >= beforeCompletions + 2,
          "失败后的后续布局保存应继续完成");
        await tick();
        assert(messages.some((entry) => entry.type === "err" && entry.message.includes("布局保存失败")
          && entry.message.includes(expectedMessage)), `${kind} 失败应显示可操作 toast`);
        assert(window.__dashboardTestSaveCompletions.slice(beforeCompletions)
          .map((entry) => entry.ok).join(",") === "false,true", "首个失败不得阻断后续保存");
        assert(window.__dashboardTestPrefs.dashboard.items.find((item) => item.id === "calendar").width === 12,
          "失败恢复后应保存最后用户快照");
        assert(!unhandled.length, `${kind} 失败不得出现未处理 rejection：${unhandled.join(";")}`);
      } finally {
        window.__dashboardTestSaveFailure = null;
        window.toast = originalToast;
        window.removeEventListener("unhandledrejection", onUnhandled);
      }
    };
    await asyncCheck("布局保存 HTTP 500 提示且队列恢复", async () => {
      await checkFailureRecovery("http", "injected dashboard HTTP 500");
    });
    await asyncCheck("布局保存网络拒绝提示且队列恢复", async () => {
      await checkFailureRecovery("network", "injected dashboard network failure");
    });

    await click("#dash-edit");
    check("退出编辑隐藏全部编辑控件", () => {
      assert(!document.querySelector("#dashboard").classList.contains("layout-editing"), "应退出编辑模式");
      document.querySelectorAll(".dash-card-tools").forEach((el) => assert(getComputedStyle(el).display === "none", "普通模式编辑工具应隐藏"));
    });

    return {
      ok: true,
      results,
      prefsForReload: persisted,
      currentPrefs: clone(window.__dashboardTestPrefs),
      saves: window.__dashboardTestSaves.length,
    };
  }

  window.installDashboardTestApi = installDashboardTestApi;
  window.runDashboardBrowserChecks = runDashboardBrowserChecks;
  window.installDashboardTestApi();
})();
