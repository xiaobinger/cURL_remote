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
    const originalSSHTargets = state.sshTargets;
    const requests = [];
    const activePollsById = new Map();
    let activePolls = 0;
    let maxActivePolls = 0;
    let maxActivePollsPerSession = 0;
    let pollGate = null;
    let closeFailure = null;
    let resetPoll = null;
    let inputGate = null;
    let inputFailure = null;
    const inputAttempts = [];
    const inputArrivals = [];
    const unhandledRejections = [];
    const onUnhandledRejection = (event) => {
      unhandledRejections.push(String(event.reason && (event.reason.message || event.reason)));
      event.preventDefault();
    };
    window.addEventListener("unhandledrejection", onUnhandledRejection);

    window.fetch = async (url, options) => {
      const target = String(url);
      const isPoll = target.includes("/api/terminal/events");
      const isInput = target.endsWith("/api/terminal/input");
      const pollId = isPoll ? new URL(target).searchParams.get("id") : null;
      const inputBody = isInput ? JSON.parse(options.body) : null;
      if (isInput) inputAttempts.push({ id: inputBody.id, text: inputBody.text });
      requests.push({ target, method: (options && options.method) || "GET", body: options && options.body });
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
        if (isInput && inputGate && inputGate.text === inputBody.text && !inputGate.released) {
          inputGate.entered = true;
          await new Promise((resolve) => {
            inputGate.release = () => {
              inputGate.released = true;
              resolve();
            };
          });
        }
        if (isInput && inputFailure && inputFailure.remaining
            && (!inputFailure.text || inputFailure.text === inputBody.text)) {
          inputFailure.remaining -= 1;
          if (inputFailure.kind === "network") throw new TypeError(inputFailure.message);
          return new Response(JSON.stringify({ error: inputFailure.message }), {
            status: inputFailure.status,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (isInput) inputArrivals.push({ id: inputBody.id, text: inputBody.text });
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

      await check("显示 Dashboard 不自动拉起本地 shell（懒启动）", async () => {
        await panel.close();
        syncDashboard();
        renderDashboard();
        await sleep(300);
        assert(!panel.id, "Dashboard 可见不得自动创建终端会话");
        input.focus();
        // 自动化环境 document 可能没有窗口焦点，focus() 不派发事件，这里显式补发
        input.dispatchEvent(new Event("focus"));
        await waitFor(() => !!panel.id, "首次聚焦输入框应建立本地会话");
        await waitFor(() => panel.status === "ready", "懒启动会话应就绪");
      });

      await check("跳板机按钮可用性跟随堡垒机配置", async () => {
        const jumpButton = document.querySelector("#terminal-jump");
        assert(jumpButton, "工具栏应包含跳板机按钮");
        const savedSSH = state.ssh;
        state.ssh = { jump_host: "gate.example", jump_user: "ops", targets: [] };
        panel.refresh();
        assert(!jumpButton.disabled, "配置堡垒机后跳板机按钮应可用");
        state.ssh = { targets: [] };
        panel.refresh();
        assert(jumpButton.disabled, "未配置堡垒机时跳板机按钮应禁用");
        state.ssh = savedSSH;
        panel.refresh();
      });

      await check("跳板机会话发送 jump 请求且不直连目标机", async () => {
        await panel.close();
        await panel.openJump();
        const entry = requests.filter((e) => e.target.endsWith("/api/terminal/open") && e.body).at(-1);
        assert(entry && JSON.parse(entry.body).jump === true, "openJump 应发送 jump: true 的会话请求");
        assert(panel.kind === "ssh" && panel.label === "跳板机", "跳板机会话前端状态应为 ssh/跳板机");
        assert(panel.targetSelect.value === "", "跳板机会话不选中任何目标机");
        assert(panel.jump === true, "面板应记住 jump 会话标记用于重连");
        await panel.close();
      });

      await check("SSH 会话可直接在输出区键入（raw 逐键）", async () => {
        const outputArea = document.querySelector("#terminal-output");
        assert(outputArea.tabIndex === 0, "输出区应可聚焦（tabindex=0）");
        // 伪造一个就绪的 SSH 会话，拦截相关请求校验 raw 键值
        const saved = { id: panel.id, kind: panel.kind, status: panel.status, label: panel.label, jump: panel.jump };
        const rawBodies = [];
        const realFetch = window.fetch;
        const okResponse = () => new Response(JSON.stringify({ ok: true }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
        window.fetch = async (url, options) => {
          const target = String(url);
          if (target.endsWith("/api/terminal/input")) {
            rawBodies.push(JSON.parse(options.body));
            return okResponse();
          }
          if (target.includes("/api/terminal/events") && target.includes("inline-test")) {
            return new Response(JSON.stringify({
              session: { id: "inline-test", kind: "ssh", label: "跳板机", status: "ready", jump: true },
              events: [], next: 1,
            }), { status: 200, headers: { "Content-Type": "application/json" } });
          }
          if (target.endsWith("/api/terminal/interrupt")) return okResponse();
          return realFetch(url, options);
        };
        try {
          panel.id = "inline-test";
          panel.kind = "ssh";
          panel.status = "ready";
          const key = (k, extra = {}) => outputArea.dispatchEvent(new KeyboardEvent("keydown", {
            key: k, bubbles: true, cancelable: true, ...extra,
          }));
          key("a");
          key("Enter");
          key("Backspace");
          key("ArrowUp");
          key("Tab");
          key("c", { ctrlKey: true });
          await sleep(300);
          assert(JSON.stringify(rawBodies) === JSON.stringify([
            { id: "inline-test", text: "a", raw: true },
            { id: "inline-test", text: "\r", raw: true },
            { id: "inline-test", text: "\x7f", raw: true },
            { id: "inline-test", text: "\x1b[A", raw: true },
            { id: "inline-test", text: "\t", raw: true },
          ]), `输出区按键应逐键 raw 发送，实际 ${JSON.stringify(rawBodies)}`);
          // Ctrl+C 走中断接口，不进 raw 队列
          assert(!rawBodies.some((body) => body.text === "c"), "Ctrl+C 应走中断而非 raw 输入");
          // 本地会话不启用直敲
          panel.kind = "local";
          rawBodies.length = 0;
          key("a");
          await sleep(150);
          assert(rawBodies.length === 0, "本地会话输出区按键不得发送 raw 输入");
        } finally {
          window.fetch = realFetch;
          panel.id = saved.id;
          panel.kind = saved.kind;
          panel.status = saved.status;
          panel.label = saved.label;
          panel.jump = saved.jump;
        }
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

      await check("连续 input 严格按 A/B 到达", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "顺序测试前应建立本地会话");
        panel.clear();
        const commandA = "echo TERMINAL_DEFERRED_ORDER_A";
        const commandB = "echo TERMINAL_DEFERRED_ORDER_B";
        const markerA = "TERMINAL_DEFERRED_ORDER_A";
        const markerB = "TERMINAL_DEFERRED_ORDER_B";
        inputGate = { text: commandA, entered: false, released: false, release: null };
        let attemptsBeforeRelease = [];
        try {
          input.value = commandA;
          const sendingA = panel.send();
          input.value = commandB;
          const sendingB = panel.send();
          await waitFor(() => inputGate.entered, "第一条 input 应进入 deferred 门控");
          await sleep(150);
          attemptsBeforeRelease = inputAttempts.filter(
            (entry) => entry.text === commandA || entry.text === commandB,
          ).map((entry) => entry.text);
          inputGate.release();
          await Promise.all([sendingA, sendingB]);
          await waitFor(() => (text().match(/TERMINAL_DEFERRED_ORDER_A/g) || []).length >= 2
            && (text().match(/TERMINAL_DEFERRED_ORDER_B/g) || []).length >= 2,
          "A/B 应都产生真实 shell 输出");
          const arrivals = inputArrivals.filter(
            (entry) => entry.text === commandA || entry.text === commandB,
          ).map((entry) => entry.text);
          const rendered = text();
          const actualA = rendered.indexOf(markerA, rendered.indexOf(markerA) + 1);
          const actualB = rendered.indexOf(markerB, rendered.indexOf(markerB) + 1);
          assert(JSON.stringify(attemptsBeforeRelease) === JSON.stringify([commandA]),
            `A 完成前不得发出 B，实际 ${attemptsBeforeRelease.join(",")}`);
          assert(JSON.stringify(arrivals) === JSON.stringify([commandA, commandB]),
            `stdin/adapter 到达必须为 A,B，实际 ${arrivals.join(",")}`);
          assert(actualA >= 0 && actualB > actualA, "真实 shell 输出顺序必须为 A,B");
          assert(panel.history.slice(-2).join(",") === [commandA, commandB].join(","),
            "历史必须保持用户输入 A,B 顺序");
        } finally {
          if (inputGate && inputGate.release) inputGate.release();
          inputGate = null;
        }
        await sleep(0);
        assert(!unhandledRejections.length, `不得出现未处理 rejection：${unhandledRejections.join(";")}`);
      });

      await check("input 网络失败后队列继续", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "失败恢复前应建立本地会话");
        panel.clear();
        const messages = [];
        window.toast = (message, type) => messages.push({ message, type });
        const commandA = "echo TERMINAL_FAILED_INPUT_A";
        const commandB = "echo TERMINAL_AFTER_FAILED_INPUT_B";
        inputFailure = { kind: "network", remaining: 1, text: commandA, message: "injected input network failure" };
        try {
          input.value = commandA;
          const sendingA = panel.send();
          input.value = commandB;
          const sendingB = panel.send();
          await Promise.all([sendingA, sendingB]);
          await waitFor(() => (text().match(/TERMINAL_AFTER_FAILED_INPUT_B/g) || []).length >= 2,
            "A 网络失败后 B 仍应真实执行");
          assert(messages.some((entry) => entry.type === "err"
            && entry.message.includes("injected input network failure")), "input 网络失败应显示错误");
          assert(inputAttempts.filter((entry) => entry.text === commandA || entry.text === commandB)
            .map((entry) => entry.text).join(",") === [commandA, commandB].join(","),
          "失败后后续 input 仍应按 A,B 尝试");
        } finally {
          inputFailure = null;
          window.toast = originalToast;
        }
        await sleep(0);
        assert(!unhandledRejections.length, `失败队列不得出现未处理 rejection：${unhandledRejections.join(";")}`);
      });

      await check("generation 切换隔离旧 input 队列", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "generation 测试前应建立本地会话");
        const oldId = panel.id;
        const commandA = "echo TERMINAL_OLD_GENERATION_A";
        const commandB = "echo TERMINAL_OLD_GENERATION_B";
        const newCommand = "echo TERMINAL_NEW_GENERATION_C";
        inputGate = { text: commandA, entered: false, released: false, release: null };
        try {
          input.value = commandA;
          const sendingA = panel.send();
          input.value = commandB;
          const sendingB = panel.send();
          await waitFor(() => inputGate.entered, "旧会话 A 应进入 deferred 门控");
          await sleep(100);
          const switching = panel.openLocal();
          await switching;
          await waitFor(() => panel.status === "ready", "新会话应独立就绪");
          const newId = panel.id;
          assert(newId && newId !== oldId, "generation 切换应建立新会话");
          input.value = newCommand;
          const sendingNew = panel.send();
          await waitFor(() => inputArrivals.some((entry) => entry.id === newId && entry.text === newCommand),
            "旧队列 pending 不得阻塞新会话 input", 2000);
          inputGate.release();
          await Promise.all([sendingA, sendingB, sendingNew]);
          assert(!inputAttempts.some((entry) => entry.id === newId
            && (entry.text === commandA || entry.text === commandB)), "旧命令不得写入新会话");
          assert(!inputArrivals.some((entry) => entry.text === commandB),
            "切换后旧队列中尚未发送的 B 应被丢弃");
          assert(inputAttempts.filter((entry) => entry.id === oldId
            && (entry.text === commandA || entry.text === commandB)).length === 1,
          "旧 generation 只应实际发出已 pending 的 A");
        } finally {
          if (inputGate && inputGate.release) inputGate.release();
          inputGate = null;
        }
        await sleep(0);
        assert(!unhandledRejections.length, `generation 切换不得出现未处理 rejection：${unhandledRejections.join(";")}`);
      });

      await check("cd 后 cd 显示持久工作目录", async () => {
        panel.clear();
        await send("cd");
        await send("cd ..", null, 250);
        await send("cd");
        const paths = text().match(/[A-Za-z]:\\[^\r\n]+/g) || [];
        assert(paths.length >= 2, `应读取两次 cwd：${text()}`);
        assert(paths[paths.length - 1] !== paths[paths.length - 2], "cd .. 后 cwd 应改变且跨命令保持");
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

      await check("终端全屏与 Esc 退出保留会话", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "全屏前应建立本地会话");
        const id = panel.id;
        const card = document.querySelector('[data-component="terminal"]');
        const toggle = document.querySelector("#terminal-fullscreen");
        toggle.click();
        assert(card.classList.contains("terminal-maximized"), "按钮应最大化终端卡片");
        assert(document.body.classList.contains("terminal-fullscreen-open"), "全屏应锁定背景滚动");
        assert(toggle.getAttribute("aria-pressed") === "true", "全屏按钮应暴露 pressed 状态");
        assert(panel.id === id && await sessionStatus(id) === "ready", "进入全屏不得重开或断开会话");
        card.classList.add("component-hidden");
        assert(getComputedStyle(card).display === "none", "全屏终端仍必须响应组件隐藏");
        card.classList.remove("component-hidden");
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        assert(!card.classList.contains("terminal-maximized"), "Esc 应退出全屏");
        assert(!document.body.classList.contains("terminal-fullscreen-open"), "退出全屏应恢复背景滚动");
        assert(panel.id === id && await sessionStatus(id) === "ready", "退出全屏不得重开或断开会话");
        toggle.click();
        toggle.click();
        assert(!card.classList.contains("terminal-maximized"), "再次点击应退出全屏");
      });

      await check("SSH 下拉列出全部服务器并直接切换", async () => {
        state.sshTargets = [
          { label: "内存测试机A", host: "127.0.0.1" },
          { label: "内存测试机B", host: "127.0.0.2" },
        ];
        state.ssh = { targets: state.sshTargets };
        panel.refresh();
        const select = document.querySelector("#terminal-target");
        const options = Array.from(select.options).map((option) => option.textContent);
        assert(options.some((label) => label.includes("内存测试机A(127.0.0.1)")), "下拉应显示第一台 label(host)");
        assert(options.some((label) => label.includes("内存测试机B(127.0.0.2)")), "下拉应显示第二台 label(host)");
        const localId = panel.id;
        select.value = "1";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await waitFor(() => panel.kind === "ssh" && panel.label === "内存测试机B", "选择服务器应直接打开对应 SSH 会话");
        assert(panel.id !== localId, "选择服务器应切换 session id");
        assert(await sessionStatus(localId) === "closed", "选择服务器前必须关闭旧本地会话");
        await waitFor(() => text().includes("目标机配置不存在"), "应使用现有安全后端解析所选目标");
        const openRequest = requests.filter((entry) => entry.target.endsWith("/api/terminal/open") && entry.body)
          .map((entry) => JSON.parse(entry.body)).at(-1);
        assert(openRequest.target_index === 1 && openRequest.label === "内存测试机B",
          "下拉必须把目标索引和标签一起发送，避免同标签歧义");
        assert(!document.querySelector(".ctx-menu"), "服务器选择不得再依赖上下文菜单");
      });

      await check("取消 SSH 编辑后下拉仍使用已保存目标", async () => {
        state.sshTargets = [{ label: "已保存目标", host: "127.0.0.8", port: "22", user: "root" }];
        state.ssh = { targets: [{ label: "已保存目标", host: "127.0.0.250", port: "22", user: "root" }] };
        panel.refresh();
        const select = document.querySelector("#terminal-target");
        assert(select.options[1].textContent === "已保存目标(127.0.0.8)",
          "未保存的 host 编辑不得进入终端下拉或改变实际连接目标");
      });

      await check("保存 SSH 设置后立即刷新服务器下拉", async () => {
        state.sshTargets = [];
        state.ssh = { targets: [] };
        panel.refresh();
        const select = document.querySelector("#terminal-target");
        assert(select.disabled, "保存前空配置应禁用下拉");
        state.ssh.targets.push({ label: "保存后新增", host: "127.0.0.9", port: "22", user: "root" });
        const fetchBeforeSave = window.fetch;
        window.fetch = async (url, options) => String(url).endsWith("/api/save_ssh")
          ? new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } })
          : fetchBeforeSave(url, options);
        try {
          await saveSsh();
        } finally {
          window.fetch = fetchBeforeSave;
        }
        assert(!select.disabled && select.options.length === 2, "保存成功后必须立即重建服务器下拉");
        assert(select.options[1].textContent.includes("保存后新增(127.0.0.9)"), "新保存目标应立即可选");
      });

      await check("无 SSH 配置时下拉显示设置提示", async () => {
        state.sshTargets = [];
        state.ssh = { targets: [] };
        panel.refresh();
        const select = document.querySelector("#terminal-target");
        assert(select.options.length === 1, "无配置只保留提示项");
        assert(select.options[0].textContent.includes("请先配置 SSH"), "提示项应引导先配置 SSH");
        assert(select.disabled, "无配置时服务器下拉不可选择");
      });

      await check("组件隐藏不自动断线，重新可见只 ensureLocal 一次", async () => {
        await panel.openLocal();
        await waitFor(() => panel.status === "ready", "隐藏组件前应建立本地 ready 会话");
        const id = panel.id;
        const item = dashboardLayout.items.find((entry) => entry.id === "terminal");
        item.visible = false;
        dashboardLayout.apply();
        syncDashboard();
        await sleep(250);
        assert(panel.id === id && await sessionStatus(id) === "ready",
          `隐藏组件不得关闭会话：id=${id} current=${panel.id} kind=${panel.kind} status=${panel.status} backend=${await sessionStatus(id)}`);
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

      assert(!unhandledRejections.length, `完整套件不得出现未处理 rejection：${unhandledRejections.join(";")}`);
      return { ok: true, results, maxActivePolls, maxActivePollsPerSession, requests: requests.length };
    } finally {
      if (pollGate && pollGate.release) pollGate.release();
      if (inputGate && inputGate.release) inputGate.release();
      closeFailure = null;
      resetPoll = null;
      inputGate = null;
      inputFailure = null;
      window.fetch = originalFetch;
      window.toast = originalToast;
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
      state.ssh = originalSSH;
      state.sshTargets = originalSSHTargets;
      document.body.click();
      if (panel.id) await panel.openLocal();
    }
  }

  window.runTerminalBrowserChecks = runTerminalBrowserChecks;
})();
