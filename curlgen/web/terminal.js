/* Dashboard persistent terminal: local / SSH sessions over the token-protected API. */
"use strict";

async function terminalApi(path, body) {
  const options = { headers: { "X-CurlGen-Token": window.TERMINAL_TOKEN } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "text/plain";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(window.API_BASE + path, options);
  const json = await response.json();
  if (!response.ok || json.error) {
    const error = new Error(json.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return json;
}

class TerminalPanel {
  constructor() {
    this.id = null;
    this.kind = "local";
    this.label = "";
    this.targetIndex = null;
    this.jump = false;
    this.status = "closed";
    this.cursor = 0;
    this.history = [];
    this.historyIndex = 0;
    this.historyDraft = "";
    this.polling = false;
    this.timer = null;
    this.generation = 0;
    this._pollPromise = null;
    this._switching = null;
    this._inputQueue = Promise.resolve();
    this._visible = null;

    this.output = document.querySelector("#terminal-output");
    this.input = document.querySelector("#terminal-input");
    this.statusEl = document.querySelector("#terminal-status");
    this.promptEl = document.querySelector("#terminal-prompt");
    this.card = document.querySelector('[data-component="terminal"]');
    this.targetSelect = document.querySelector("#terminal-target");
    this.jumpButton = document.querySelector("#terminal-jump");
    this.fullscreenButton = document.querySelector("#terminal-fullscreen");
    this._bind();
    this._renderTargets();
    this._setStatus("closed");
  }

  _bind() {
    this.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void this.send();
      } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        this._historyStep(event.key === "ArrowUp" ? -1 : 1);
      }
    });
    // 懒启动：首次点击输入框才建立本地会话，避免启动即拉起 shell
    this.input.addEventListener("focus", () => {
      if (!this.id && !this._switching) void this.ensureLocal();
    });
    document.querySelector("#terminal-local").addEventListener("click", () => void this.openLocal());
    this.jumpButton.addEventListener("click", () => void this.openJump());
    this.targetSelect.addEventListener("change", () => {
      if (!this.targetSelect.value) return;
      const option = this.targetSelect.selectedOptions[0];
      void this.openSSH(option.dataset.label, Number(this.targetSelect.value));
    });
    this.fullscreenButton.addEventListener("click", () => this.toggleFullscreen());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.card.classList.contains("terminal-maximized")) {
        event.preventDefault();
        this.toggleFullscreen(false);
      }
    });
    document.querySelector("#terminal-interrupt").addEventListener("click", () => void this.interrupt());
    document.querySelector("#terminal-reconnect").addEventListener("click", () => void this.reconnect());
    document.querySelector("#terminal-clear").addEventListener("click", () => this.clear());
    // SSH 会话可直接在输出区键入（PTY 远端回显），本地 cmd 无回显仍走底部输入框
    this.output.addEventListener("keydown", (event) => this._onOutputKey(event));
  }

  _onOutputKey(event) {
    if (this.kind !== "ssh" || this.status !== "ready" || !this.id) return;
    if (event.ctrlKey && (event.key === "c" || event.key === "C")) {
      event.preventDefault();
      void this.interrupt();
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    let text = null;
    if (event.key === "Enter") text = "\r";
    else if (event.key === "Backspace") text = "\x7f";
    else if (event.key === "Tab") text = "\t";
    else if (event.key === "ArrowUp") text = "\x1b[A";
    else if (event.key === "ArrowDown") text = "\x1b[B";
    else if (event.key === "ArrowRight") text = "\x1b[C";
    else if (event.key === "ArrowLeft") text = "\x1b[D";
    else if (event.key === "Escape") return; // Esc 优先用于退出全屏
    else if (event.key.length === 1) text = event.key;
    if (text === null) return;
    event.preventDefault();
    void this._sendRaw(text);
  }

  async _sendRaw(text) {
    const id = this.id;
    const generation = this.generation;
    const operation = this._inputQueue.then(async () => {
      if (id !== this.id || generation !== this.generation) return;
      try {
        await terminalApi("/api/terminal/input", { id, text, raw: true });
        if (id === this.id && generation === this.generation) this._wake();
      } catch (error) {
        if (id === this.id && generation === this.generation) {
          this._append(`错误: ${error.message}\n`);
          this._setStatus("error");
          toast(`按键发送失败: ${error.message}`, "err");
        }
      }
    });
    this._inputQueue = operation;
    await operation;
  }

  _renderTargets() {
    const targets = typeof state !== "undefined" && Array.isArray(state.sshTargets)
      ? state.sshTargets : [];
    this.targetSelect.textContent = "";
    const placeholder = new Option(targets.length ? "选择 SSH 服务器" : "请先配置 SSH 服务器", "");
    this.targetSelect.add(placeholder);
    targets.forEach((target, index) => {
      const label = target.label || target.host;
      const option = new Option(`${label}(${target.host})`, String(index));
      option.dataset.label = label;
      this.targetSelect.add(option);
    });
    this.targetSelect.disabled = !targets.length;
    this.targetSelect.value = this.kind === "ssh" && !this.jump && this.targetIndex !== null
      ? String(this.targetIndex) : "";
    const jumpReady = typeof state !== "undefined" && state.ssh
      && !!((state.ssh.jump_host || "").trim() && (state.ssh.jump_user || "").trim());
    this.jumpButton.disabled = !jumpReady;
    this.jumpButton.title = jumpReady
      ? "登录跳板机（进入堡垒机菜单，可直接选服务器）" : "请先在 SSH 设置中配置堡垒机";
  }

  toggleFullscreen(force) {
    const maximized = force === undefined
      ? !this.card.classList.contains("terminal-maximized") : Boolean(force);
    this.card.classList.toggle("terminal-maximized", maximized);
    document.body.classList.toggle("terminal-fullscreen-open", maximized);
    this.fullscreenButton.setAttribute("aria-pressed", String(maximized));
    this.fullscreenButton.textContent = maximized ? "退出全屏" : "全屏";
    if (maximized) this.output.scrollTop = this.output.scrollHeight;
  }

  _historyStep(direction) {
    if (!this.history.length) return;
    if (direction < 0) {
      if (this.historyIndex === this.history.length) this.historyDraft = this.input.value;
      this.historyIndex = Math.max(0, this.historyIndex - 1);
    } else {
      if (this.historyIndex === this.history.length) return;
      this.historyIndex += 1;
    }
    this.input.value = this.historyIndex === this.history.length
      ? this.historyDraft : this.history[this.historyIndex];
    this.input.setSelectionRange(this.input.value.length, this.input.value.length);
  }

  _isVisible() {
    const dashboard = document.querySelector("#dashboard");
    const card = document.querySelector('[data-component="terminal"]');
    return !!dashboard && !dashboard.classList.contains("hidden")
      && !!card && !card.classList.contains("component-hidden");
  }

  _pollDelay() {
    if (document.hidden) return 2500;
    return this._isVisible() ? 250 : 1500;
  }

  refresh() {
    this._renderTargets();
    const visible = this._isVisible();
    if (visible === this._visible) return;
    this._visible = visible;
    if (!this.id || this.status === "closed") return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this._schedule();
  }

  async ensureLocal() {
    this.refresh();
    if (!this._isVisible() || this.id || this._switching) return this._switching;
    return this.openLocal();
  }

  openLocal() {
    return this._switch("local", "");
  }

  openJump() {
    return this._switch("ssh", "跳板机", null, true);
  }

  openSSH(label, targetIndex = null) {
    return this._switch("ssh", label || "", targetIndex, false);
  }

  async _switch(kind, label, targetIndex = null, jump = false) {
    const run = async () => {
      if (!await this.close()) return null;
      this.clear();
      this.cursor = 0;
      this.kind = kind;
      this.label = label;
      this.targetIndex = targetIndex;
      this.jump = jump;
      this._setStatus("connecting");
      try {
        const payload = { kind, label: label || undefined };
        if (kind === "ssh" && targetIndex !== null) payload.target_index = targetIndex;
        if (kind === "ssh" && jump) payload.jump = true;
        const session = await terminalApi("/api/terminal/open", payload);
        this.id = session.id;
        this.kind = session.kind || kind;
        this.label = session.label || label;
        this.jump = !!session.jump || jump;
        this.cursor = 0;
        this._setStatus(session.status || "connecting");
        this._schedule(0, true);
        return session;
      } catch (error) {
        this.id = null;
        this._setStatus("error");
        this._append(`错误: ${error.message}\n`);
        toast(`终端连接失败: ${error.message}`, "err");
        return null;
      }
    };
    const previous = this._switching;
    const operation = previous ? previous.then(run, run) : run();
    this._switching = operation;
    try {
      return await operation;
    } finally {
      if (this._switching === operation) this._switching = null;
    }
  }

  reconnect() {
    if (this.kind === "ssh") {
      return this.jump ? this.openJump() : this.openSSH(this.label, this.targetIndex);
    }
    return this.openLocal();
  }

  _schedule(delay = this._pollDelay(), includeClosed = false) {
    if (!this.id || (this.status === "closed" && !includeClosed) || this.timer !== null) return;
    const id = this.id;
    const generation = this.generation;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (id !== this.id || generation !== this.generation) return;
      void this.poll(includeClosed);
    }, delay);
  }

  _wake() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this._schedule(0);
  }

  poll(includeClosed = false) {
    if (!this.id || (this.status === "closed" && !includeClosed)) return Promise.resolve();
    if (this._pollPromise) return this._pollPromise;
    const id = this.id;
    const generation = this.generation;
    this.polling = true;
    const promise = (async () => {
      try {
        const data = await terminalApi(
          `/api/terminal/events?id=${encodeURIComponent(id)}&since=${this.cursor}`,
        );
        if (id !== this.id || generation !== this.generation) return;
        this._applyEvents(data);
      } catch (error) {
        if (id === this.id && generation === this.generation) this._setStatus("error");
      } finally {
        if (this._pollPromise !== promise) return;
        this.polling = false;
        this._pollPromise = null;
        if (id === this.id && generation === this.generation) this._schedule();
      }
    })();
    this._pollPromise = promise;
    return promise;
  }

  _applyEvents(data) {
    if (data.reset) {
      this.clear();
      this.cursor = 0;
    }
    const events = Array.isArray(data.events) ? data.events : [];
    events.forEach((event) => {
      const seq = Number(event.seq);
      if (!Number.isSafeInteger(seq) || seq <= this.cursor) return;
      if (event.type === "status") this._setStatus(event.text);
      else if (event.type === "stdout" || event.type === "stderr") this._append(event.text || "");
      this.cursor = seq;
    });
    if (Number.isSafeInteger(data.next)) this.cursor = Math.max(this.cursor, data.next - 1);
    const session = data.session;
    if (session && session.id === this.id) {
      this.kind = session.kind || this.kind;
      this.label = session.label || this.label;
      this.jump = session.jump === undefined ? this.jump : !!session.jump;
      this._setStatus(session.status || this.status);
    }
  }

  _append(value) {
    const nearBottom = this.output.scrollHeight - this.output.scrollTop - this.output.clientHeight < 24;
    this.output.appendChild(document.createTextNode(String(value)));
    if (nearBottom) this.output.scrollTop = this.output.scrollHeight;
  }

  _setStatus(status) {
    this.status = status;
    const labels = { connecting: "连接中", closed: "已断开", error: "错误" };
    const readyLabel = this.kind === "ssh" ? (this.label || "SSH") : "本地";
    this.statusEl.textContent = labels[status] || readyLabel;
    this.statusEl.dataset.status = status;
    this.promptEl.textContent = `${this.kind === "ssh" ? (this.label || "SSH") : "本地"}>`;
    this.targetSelect.value = this.kind === "ssh" && !this.jump && this.targetIndex !== null
      ? String(this.targetIndex) : "";
    // 无会话时保持可聚焦（懒启动入口）；已连接但未就绪（连接中/出错）才禁用
    this.input.disabled = Boolean(this.id) && status !== "ready";
    this.input.placeholder = this.kind === "ssh"
      ? "输入命令，Enter 执行；也可点击上方输出区直接键入"
      : "输入命令，Enter 执行";
    document.querySelector("#terminal-interrupt").disabled = !this.id || status !== "ready";
  }

  async send() {
    const command = this.input.value;
    if (!command.trim()) return;
    if (!this.id) await this.ensureLocal();
    if (!this.id || this.status !== "ready") {
      toast("终端尚未连接，请稍候或点击重连", "err");
      return;
    }
    const id = this.id;
    const generation = this.generation;
    if (this.history[this.history.length - 1] !== command) this.history.push(command);
    if (this.history.length > 100) this.history.shift();
    this.historyIndex = this.history.length;
    this.historyDraft = "";
    this.input.value = "";
    this._append(`${this.promptEl.textContent} ${command}\n`);
    const operation = this._inputQueue.then(async () => {
      if (id !== this.id || generation !== this.generation) return;
      try {
        await terminalApi("/api/terminal/input", { id, text: command });
        if (id === this.id && generation === this.generation) this._wake();
      } catch (error) {
        if (id === this.id && generation === this.generation) {
          this._append(`错误: ${error.message}\n`);
          this._setStatus("error");
          toast(`命令发送失败: ${error.message}`, "err");
        }
      }
    });
    this._inputQueue = operation;
    await operation;
  }

  async interrupt() {
    if (!this.id) return;
    const id = this.id;
    try {
      await terminalApi("/api/terminal/interrupt", { id });
      if (id === this.id) this._wake();
    } catch (error) {
      if (id === this.id) toast(`中断失败: ${error.message}`, "err");
    }
  }

  async close() {
    const id = this.id;
    this.generation += 1;
    this._inputQueue = Promise.resolve();
    const generation = this.generation;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this._pollPromise = null;
    this.polling = false;
    if (!id) {
      this.cursor = 0;
      this._setStatus("closed");
      return true;
    }
    try {
      await terminalApi("/api/terminal/close", { id });
    } catch (error) {
      const missing = error.status === 404
        || (error.status === 500 && error.message === `'${id}'`);
      if (!missing) {
        if (id === this.id && generation === this.generation) this._setStatus("error");
        toast(`终端关闭失败: ${error.message}`, "err");
        return false;
      }
    }
    if (id === this.id && generation === this.generation) {
      this.id = null;
      this.cursor = 0;
      this._setStatus("closed");
    }
    return true;
  }

  clear() {
    this.output.textContent = "";
    this.output.scrollTop = 0;
  }
}

window.TerminalPanel = TerminalPanel;
window.terminalPanel = new TerminalPanel();
