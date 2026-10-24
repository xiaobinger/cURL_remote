/*
 * Task 6 real-browser checks. Run against the local HTTP handler with a real
 * TerminalManager; SSH targets and toast spies used here stay in memory.
 */
(() => {
  "use strict";

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = () => document.querySelector("#terminal-output").textContent;

  async function waitFor(predicate, message, timeout = 8000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (predicate()) return;
      await sleep(60);
    }
    throw new Error(message);
  }

  async function runTerminalBrowserChecks() {
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    assert(window.terminalPanel, "TerminalPanel 应初始化");
    const panel = window.terminalPanel;
    const input = document.querySelector("#terminal-input");
    const output = document.querySelector("#terminal-output");
    const results = [];
    const originalFetch = window.fetch;
    const originalToast = window.toast;
    const originalSSH = state.ssh;
    const requests = [];
    let activePolls = 0;
    let maxActivePolls = 0;

    window.fetch = async (url, options) => {
      const target = String(url);
      const isPoll = target.includes("/api/terminal/events");
      requests.push({ target, method: (options && options.method) || "GET" });
      if (isPoll) {
        activePolls += 1;
        maxActivePolls = Math.max(maxActivePolls, activePolls);
      }
      try {
        return await originalFetch(url, options);
      } finally {
        if (isPoll) activePolls -= 1;
      }
    };

    const check = async (name, fn) => {
      await fn();
      results.push(name);
    };
    const send = async (command, marker, pause = 500) => {
      input.value = command;
      await panel.send();
      await sleep(pause);
      if (marker) {
        await waitFor(() => text().includes(marker), `未显示命令输出 ${marker}`);
      }
    };
    const key = (value) => input.dispatchEvent(new KeyboardEvent("keydown", {
      key: value, bubbles: true, cancelable: true,
    }));
    const sessionStatus = async (id) => {
      const response = await originalFetch(
        `${window.API_BASE}/api/terminal/events?id=${encodeURIComponent(id)}&since=0`,
        { headers: { "X-CurlGen-Token": window.TERMINAL_TOKEN } },
      );
      return (await response.json()).session.status;
    };

    try {
      await check("受令牌保护的真实终端 API", async () => {
        const response = await originalFetch(`${window.API_BASE}/api/terminal/open`, {
          method: "POST", headers: { "Content-Type": "text/plain" }, body: "{}",
        });
        assert(response.status === 403, "缺少 token 的 open 应返回 403");
      });

      await check("Dashboard 可见时 ensureLocal 幂等", async () => {
        await panel.ensureLocal();
        const id = panel.id;
        assert(id, "应创建本地会话");
        const before = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        await Promise.all([panel.ensureLocal(), panel.ensureLocal()]);
        syncDashboard();
        renderDashboard();
        await sleep(150);
        assert(panel.id === id, "重复 ensureLocal 不得更换会话");
        const after = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        assert(after === before, "重复 ensureLocal 不得重复 open");
      });

      await check("真实 echo 与 textContent 防注入", async () => {
        await panel.openLocal();
        await send("echo CURLGEN_TERMINAL_OK", "CURLGEN_TERMINAL_OK");
        await send('echo "<img id=terminal-injected src=x>"', "<img id=terminal-injected");
        assert(!document.querySelector("#terminal-injected"), "终端输出不得解析 HTML");
        assert(!output.querySelector("*"), "输出应仅包含文本节点");
      });

      await check("cd 后 cd 显示持久工作目录", async () => {
        panel.clear();
        await send("cd");
        await send("cd ..", null, 250);
        await send("cd");
        const paths = text().match(/[A-Za-z]:\\[^\r\n]+/g) || [];
        assert(paths.length >= 2, `应读取两次 cwd：${text()}`);
        assert(paths.at(-1) !== paths.at(-2), "cd .. 后 cwd 应改变且跨命令保持");
      });

      await check("上下箭头遍历历史", async () => {
        await send("echo TERMINAL_HISTORY_ONE", "TERMINAL_HISTORY_ONE");
        await send("echo TERMINAL_HISTORY_TWO", "TERMINAL_HISTORY_TWO");
        input.value = "";
        key("ArrowUp");
        assert(input.value === "echo TERMINAL_HISTORY_TWO", "ArrowUp 应取最近命令");
        key("ArrowUp");
        assert(input.value === "echo TERMINAL_HISTORY_ONE", "第二次 ArrowUp 应取较早命令");
        key("ArrowDown");
        assert(input.value === "echo TERMINAL_HISTORY_TWO", "ArrowDown 应返回较新命令");
        key("ArrowDown");
        assert(input.value === "", "历史末尾 ArrowDown 应恢复空输入");
      });

      await check("清屏仅清前端且会话可继续", async () => {
        document.querySelector("#terminal-clear").click();
        assert(text() === "", "清屏后输出应为空");
        const id = panel.id;
        await send("echo TERMINAL_AFTER_CLEAR", "TERMINAL_AFTER_CLEAR");
        assert(panel.id === id, "清屏不得重开会话");
      });

      await check("错误命令显示 shell 错误", async () => {
        await send("CURLGEN_DEFINITELY_NOT_A_COMMAND", "CURLGEN_DEFINITELY_NOT_A_COMMAND");
        await waitFor(
          () => /not recognized|不是内部或外部命令|not found/i.test(text()),
          "应显示错误命令的 stderr",
        );
      });

      await check("向上滚动后追加强制不回底", async () => {
        panel.clear();
        await send("for /L %i IN (1,1,80) DO @echo TERMINAL_SCROLL_%i", "TERMINAL_SCROLL_80", 700);
        output.scrollTop = 0;
        output.dispatchEvent(new Event("scroll"));
        const before = output.scrollTop;
        await send("echo TERMINAL_SCROLL_LATER", "TERMINAL_SCROLL_LATER");
        assert(Math.abs(output.scrollTop - before) < 3, "用户向上查看时不得强制回底");
        output.scrollTop = output.scrollHeight;
      });

      await check("setTimeout 串行轮询且 seq 不重复", async () => {
        maxActivePolls = 0;
        await sleep(1000);
        assert(maxActivePolls === 1, `同时最多一个 events 请求，实际 ${maxActivePolls}`);
        panel.clear();
        panel.cursor = 0;
        panel._applyEvents({ events: [{ seq: 1, type: "stdout", text: "SEQ_ONCE" }], next: 2 });
        panel._applyEvents({ events: [{ seq: 1, type: "stdout", text: "SEQ_ONCE" }], next: 2 });
        assert(text() === "SEQ_ONCE", `重复 seq 不得重复输出：${text()}`);
      });

      await check("reset 清空并重放保留事件", async () => {
        panel._applyEvents({
          reset: true,
          events: [{ seq: 2, type: "stdout", text: "RESET_REPLAY" }],
          next: 3,
        });
        assert(text() === "RESET_REPLAY", `reset 应只显示本次事件：${text()}`);
        panel._applyEvents({
          reset: true,
          events: [{ seq: 2, type: "stdout", text: "RESET_REPLAY" }],
          next: 3,
        });
        assert(text() === "RESET_REPLAY", "相同 reset 重放仍不得重复");
      });

      await check("中断、已断开状态与重连", async () => {
        await panel.openLocal();
        await send("ping 127.0.0.1 -n 30 >nul", null, 250);
        await panel.interrupt();
        await waitFor(() => panel.status === "closed", "中断后本地会话应关闭");
        assert(document.querySelector("#terminal-status").textContent === "已断开", "应显示已断开");
        const id = panel.id;
        document.querySelector("#terminal-reconnect").click();
        await waitFor(() => panel.id && panel.id !== id && panel.status === "ready", "重连应建立新会话");
        await send("echo TERMINAL_RECONNECTED", "TERMINAL_RECONNECTED");
      });

      await check("openLocal/openSSH 先关闭旧会话", async () => {
        const localId = panel.id;
        await panel.openSSH("__terminal_missing_target__");
        assert(panel.id !== localId, "openSSH 应切换会话 id");
        assert(await sessionStatus(localId) === "closed", "openSSH 前必须关闭旧本地会话");
        await waitFor(() => text().includes("目标机配置不存在"), "缺失目标配置应显示安全错误");
        const sshId = panel.id;
        await panel.openLocal();
        assert(panel.id !== sshId, "openLocal 应切换会话 id");
        assert(await sessionStatus(sshId) === "closed", "openLocal 前必须关闭旧 SSH 会话");
      });

      await check("SSH 菜单使用 .ctx-menu 且 click 不立即关闭", async () => {
        state.ssh = { targets: [{ label: "内存测试机", host: "127.0.0.1" }] };
        document.querySelector("#terminal-ssh").click();
        const menu = document.querySelector(".ctx-menu");
        assert(menu, "SSH 按钮应打开 .ctx-menu");
        assert(menu.textContent.includes("内存测试机(127.0.0.1)"), "菜单应显示 label(host)");
        assert(!document.querySelector("#ctx-menu"), "菜单不得使用错误的 #ctx-menu 断言");
        document.body.click();
      });

      await check("无 SSH 配置给出设置提示", async () => {
        const messages = [];
        window.toast = (message, type) => messages.push({ message, type });
        state.ssh = { targets: [] };
        document.querySelector("#terminal-ssh").click();
        assert(messages.some((entry) => entry.message === "请先在 SSH 设置中配置堡垒机和目标机"), "应提示先配置 SSH");
        assert(!document.querySelector(".ctx-menu"), "无配置不应打开空菜单");
      });

      await check("组件隐藏不自动断线，重新可见只 ensureLocal 一次", async () => {
        const id = panel.id;
        const item = dashboardLayout.items.find((entry) => entry.id === "terminal");
        item.visible = false;
        dashboardLayout.apply();
        syncDashboard();
        await sleep(250);
        assert(panel.id === id && await sessionStatus(id) === "ready", "隐藏组件不得关闭会话");
        await panel.close();
        await panel.ensureLocal();
        assert(!panel.id, "组件隐藏时 ensureLocal 不得打开会话");
        const before = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        item.visible = true;
        dashboardLayout.apply();
        syncDashboard();
        await panel.ensureLocal();
        await panel.ensureLocal();
        const after = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        assert(
          panel.id && after === before + 1,
          `syncDashboard 与显式 ensureLocal 合计只能 open 一次：before=${before} after=${after} id=${panel.id}`,
        );
      });

      await check("工作台低频轮询但不断会话", async () => {
        const id = panel.id;
        const dashboardDelay = panel._pollDelay();
        const tab = newTab();
        await sleep(900);
        assert(panel.id === id && await sessionStatus(id) === "ready", "打开工作台不得断开会话");
        assert(panel._pollDelay() > dashboardDelay, "工作台应降低轮询频率");
        await closeTab(tab.id);
        assert(panel._pollDelay() === dashboardDelay, "回 Dashboard 应恢复轮询频率");
      });

      await check("紧凑终端与响应式布局", async () => {
        assert(parseFloat(getComputedStyle(output).minHeight) >= 220, "输出最小高度应为 220px");
        assert(getComputedStyle(document.querySelector(".terminal-toolbar")).flexWrap === "nowrap", "工具栏应保持单行");
        assert(getComputedStyle(input).minWidth === "0px", "输入框应可收缩而不挤压按钮");
        output.scrollIntoView({ block: "center" });
      });

      return { ok: true, results, maxActivePolls, requests: requests.length };
    } finally {
      window.fetch = originalFetch;
      window.toast = originalToast;
      state.ssh = originalSSH;
      document.body.click();
      if (panel.id) await panel.openLocal();
    }
  }

  window.runTerminalBrowserChecks = runTerminalBrowserChecks;
})();
