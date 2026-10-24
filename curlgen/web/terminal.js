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
    this._visible = null;

    this.output = document.querySelector("#terminal-output");
    this.input = document.querySelector("#terminal-input");
    this.statusEl = document.querySelector("#terminal-status");
    this.promptEl = document.querySelector("#terminal-prompt");
    this._bind();
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
    document.querySelector("#terminal-local").addEventListener("click", () => void this.openLocal());
    document.querySelector("#terminal-ssh").addEventListener("click", (event) => {
      event.stopPropagation();
      this._showSSHMenu(event);
    });
    document.querySelector("#terminal-interrupt").addEventListener("click", () => void this.interrupt());
    document.querySelector("#terminal-reconnect").addEventListener("click", () => void this.reconnect());
    document.querySelector("#terminal-clear").addEventListener("click", () => this.clear());
  }

  _showSSHMenu(event) {
    const ssh = typeof state !== "undefined" ? state.ssh : null;
    const targets = (ssh && ssh.targets) || [];
    if (!targets.length) {
      toast("请先在 SSH 设置中配置堡垒机和目标机", "err");
      return;
    }
    const items = targets.map((target) => {
      const label = target.label || target.host;
      return [`${label}(${target.host})`, () => void this.openSSH(label)];
    });
    showCtxMenu(event.clientX, event.clientY, items);
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

  openSSH(label) {
    return this._switch("ssh", label || "");
  }

  async _switch(kind, label) {
    const run = async () => {
      if (!await this.close()) return null;
      this.clear();
      this.cursor = 0;
      this.kind = kind;
      this.label = label;
      this._setStatus("connecting");
      try {
        const session = await terminalApi("/api/terminal/open", { kind, label: label || undefined });
        this.id = session.id;
        this.kind = session.kind || kind;
        this.label = session.label || label;
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
    return this.kind === "ssh" ? this.openSSH(this.label) : this.openLocal();
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
    this.input.disabled = !this.id || status !== "ready";
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
