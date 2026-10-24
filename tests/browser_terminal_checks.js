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

  async function runTerminalBrowserChecks(focus) {
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
    const activePollsById = new Map();
    let activePolls = 0;
    let maxActivePolls = 0;
    let maxActivePollsPerSession = 0;
    let pollGate = null;
    let closeFailure = null;
    let resetPoll = null;

    window.fetch = async (url, options) => {
      const target = String(url);
      const isPoll = target.includes("/api/terminal/events");
      const pollId = isPoll ? new URL(target).searchParams.get("id") : null;
      requests.push({ target, method: (options && options.method) || "GET" });
      if (isPoll) {
        activePolls += 1;
        maxActivePolls = Math.max(maxActivePolls, activePolls);
        const activeForSession = (activePollsById.get(pollId) || 0) + 1;
        activePollsById.set(pollId, activeForSession);
        maxActivePollsPerSession = Math.max(maxActivePollsPerSession, activeForSession);
      }
      try {
        if (isPoll && pollGate && pollGate.id === pollId && !pollGate.released) {
          pollGate.entered = true;
          await new Promise((resolve) => {
            pollGate.release = () => {
              pollGate.released = true;
              resolve();
            };
          });
        }
        if (target.endsWith("/api/terminal/close") && closeFailure && closeFailure.remaining) {
          closeFailure.remaining -= 1;
          if (closeFailure.kind === "network") throw new TypeError(closeFailure.message);
          return new Response(JSON.stringify({ error: closeFailure.message }), {
            status: closeFailure.status,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (isPoll && resetPoll && resetPoll.id === pollId && resetPoll.remaining) {
          const response = await originalFetch(url, options);
          const data = await response.clone().json();
          const seq = data.next - 1;
          assert(seq >= 1, "reset 注入前真实会话应已有事件");
          resetPoll.remaining -= 1;
          resetPoll.seen += 1;
          resetPoll.cursor = seq;
          return new Response(JSON.stringify({
            reset: true,
            session: data.session,
            events: [{ seq, type: "stdout", text: "RESET_HTTP_REPLAY" }],
            next: data.next,
          }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return await originalFetch(url, options);
      } finally {
        if (isPoll) {
          activePolls -= 1;
          activePollsById.set(pollId, activePollsById.get(pollId) - 1);
        }
      }
    };

    const check = async (name, fn) => {
      if (focus && !name.includes(focus)) return;
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
        input.value = "unsent draft";
        key("ArrowDown");
        assert(input.value === "unsent draft", "历史末尾 ArrowDown 不得清空非空草稿");
        key("ArrowUp");
        assert(input.value === "echo TERMINAL_HISTORY_TWO", "草稿后 ArrowUp 应进入历史");
        key("ArrowDown");
        assert(input.value === "unsent draft", "返回历史末尾应恢复非空草稿");
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

      await check("真实 poll reset 清空重放且后续不重复", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "reset 前应建立本地会话");
        await send("echo RESET_BEFORE_HTTP", "RESET_BEFORE_HTTP");
        resetPoll = { id: panel.id, remaining: 1, seen: 0, cursor: 0 };
        await waitFor(
          () => resetPoll.seen === 1 && text() === "RESET_HTTP_REPLAY",
          "reset 必须经真实 events 响应清空并重放",
        );
        assert(panel.cursor === resetPoll.cursor, "reset 后 cursor 应推进到 next - 1");
        await send("echo RESET_AFTER_HTTP", "RESET_AFTER_HTTP");
        assert((text().match(/RESET_HTTP_REPLAY/g) || []).length === 1, "后续 poll 不得重复重放 reset 事件");
        assert(text().includes("RESET_AFTER_HTTP"), "reset 后新事件仍应正常显示");
      });

      await check("旧 events pending 时切换仍独立轮询", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "切换前应建立本地会话");
        panel.clear();
        const oldId = panel.id;
        pollGate = { id: oldId, entered: false, released: false, release: null };
        try {
          await waitFor(() => pollGate.entered, "旧 events 必须已进入 pending");
          const oldInput = await originalFetch(`${window.API_BASE}/api/terminal/input`, {
            method: "POST",
            headers: { "X-CurlGen-Token": window.TERMINAL_TOKEN, "Content-Type": "text/plain" },
            body: JSON.stringify({ id: oldId, text: "echo OLD_PENDING_EVENT" }),
          });
          assert(oldInput.ok, "旧会话输入应真实写入");
          const switching = panel.openLocal();
          await waitFor(() => panel.id && panel.id !== oldId, "close 成功后才应出现新 session");
          const newId = panel.id;
          const newPollCount = () => requests.filter(
            (entry) => entry.target.includes(`/api/terminal/events?id=${newId}&`),
          ).length;
          await waitFor(() => newPollCount() > 0, "旧 events pending 时新会话仍应发 poll", 2000);
          await waitFor(() => panel.status === "ready", "新本地会话应独立收到 ready 状态");
          assert(!text().includes("OLD_PENDING_EVENT"), "旧响应不得写入新会话输出");
          pollGate.release();
          await switching;
          const afterRelease = newPollCount();
          await waitFor(() => newPollCount() > afterRelease, "旧响应结束后新会话仍应继续串行 poll");
          await send("echo NEW_AFTER_PENDING", "NEW_AFTER_PENDING");
          assert(maxActivePollsPerSession === 1, "每个活跃会话最多一个 events 请求");
          assert(!text().includes("OLD_PENDING_EVENT"), "旧迟到响应不得渲染");
        } finally {
          if (pollGate.release) pollGate.release();
        }
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

      await check("close HTTP 500 保留旧会话且重试恢复", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "500 测试前应建立本地会话");
        const oldId = panel.id;
        const beforeOpens = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        const messages = [];
        window.toast = (message, type) => messages.push({ message, type });
        closeFailure = { kind: "http", status: 500, remaining: 1, message: "injected close 500" };
        const result = await panel.openSSH("__terminal_close_500__");
        assert(result === null, "close HTTP 500 时不得打开替代会话");
        assert(panel.id === oldId, "close HTTP 500 后必须保留旧 session id 供重试");
        assert(panel.status === "error", "close HTTP 500 后应显示错误状态");
        assert(messages.some((entry) => entry.type === "err" && entry.message.includes("injected close 500")), "close HTTP 500 应向用户显示错误");
        const afterOpens = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        assert(afterOpens === beforeOpens, "close HTTP 500 时不得请求 open");
        assert(await sessionStatus(oldId) === "ready", "注入失败的 close 不得影响真实旧会话");
        await panel.openLocal();
        assert(panel.id !== oldId, "close 恢复后应能继续切换");
        assert(await sessionStatus(oldId) === "closed", "重试应真正关闭旧会话");
      });

      await check("close 网络失败保留旧会话且重试恢复", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "网络失败测试前应建立本地会话");
        const oldId = panel.id;
        const beforeOpens = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        const messages = [];
        window.toast = (message, type) => messages.push({ message, type });
        closeFailure = { kind: "network", remaining: 1, message: "injected close network failure" };
        const result = await panel.openLocal();
        assert(result === null, "close 网络失败时不得打开替代会话");
        assert(panel.id === oldId, "close 网络失败后必须保留旧 session id 供重试");
        assert(panel.status === "error", "close 网络失败后应显示错误状态");
        assert(messages.some((entry) => entry.type === "err" && entry.message.includes("injected close network failure")), "close 网络失败应向用户显示错误");
        const afterOpens = requests.filter((entry) => entry.target.endsWith("/api/terminal/open")).length;
        assert(afterOpens === beforeOpens, "close 网络失败时不得请求 open");
        assert(await sessionStatus(oldId) === "ready", "网络失败的 close 不得影响真实旧会话");
        await panel.openLocal();
        assert(panel.id !== oldId, "网络恢复后应能关闭旧会话并切换");
        assert(await sessionStatus(oldId) === "closed", "网络恢复后的重试应真正关闭旧会话");
      });

      await check("close 已不存在可安全继续切换", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "已不存在测试前应建立本地会话");
        const oldId = panel.id;
        closeFailure = { kind: "http", status: 500, remaining: 1, message: `'${oldId}'` };
        const result = await panel.openLocal();
        assert(result && panel.id !== oldId, "后端明确报告 session 已不存在时应允许切换");
        const cleanup = await originalFetch(`${window.API_BASE}/api/terminal/close`, {
          method: "POST",
          headers: { "X-CurlGen-Token": window.TERMINAL_TOKEN, "Content-Type": "text/plain" },
          body: JSON.stringify({ id: oldId }),
        });
        assert(cleanup.ok, "已不存在测试中的真实旧会话应清理");
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

      return { ok: true, results, maxActivePolls, maxActivePollsPerSession, requests: requests.length };
    } finally {
      if (pollGate && pollGate.release) pollGate.release();
      closeFailure = null;
      resetPoll = null;
      window.fetch = originalFetch;
      window.toast = originalToast;
      state.ssh = originalSSH;
      document.body.click();
      if (panel.id) await panel.openLocal();
    }
  }

  window.runTerminalBrowserChecks = runTerminalBrowserChecks;
})();
