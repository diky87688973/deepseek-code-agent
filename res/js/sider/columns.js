/* 分栏：增量 DOM（增删栏不重绘消息区）、拖拽调宽、localStorage */
(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});
  var STORAGE_KEY = "codeWebAgent.immersive.v1";
  var MAX_COLS = 8;
  /** 每栏最小像素宽，与 .imm-col 的 min-width 一致；避免栏过窄拖不到分割线 */
  var MIN_COL_PX = 100;

  function loadLayout() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }
  function saveLayout(cm) {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          activeId: cm.activeId,
          tabs: cm.cols.map(function (c) {
            return { id: c.id, title: c.title };
          }),
          colWidths: cm.colWidths.slice(),
        })
      );
    } catch (e) {}
  }

  function makeColumnState() {
    return {
      streamAssistantEl: null,
      streamAssistantText: "",
      roundReasoningText: "",
      pendingDeltaSeparator: false,
      anyToolThisTurn: false,
      chatLoadingEl: null,
      abortController: null,
      activeRunId: "",
      selectedMode: "auto",
      selectedModel: "",
      historyLoaded: false,
      lastContextLayout: null,
    };
  }

  function ColumnManager(rowEl) {
    this.rowEl = rowEl;
    this.cols = [];
    this.activeId = "";
    this.colWidths = [];
    this._drag = null;
  }

  ColumnManager.prototype.byId = function (id) {
    id = IMM.normalizeConversationId(id);
    for (var i = 0; i < this.cols.length; i++) {
      if (this.cols[i].id === id) return this.cols[i];
    }
    return null;
  };

  ColumnManager.prototype.getActive = function () {
    return this.byId(this.activeId) || this.cols[0] || null;
  };

  ColumnManager.prototype._applyFlexWidths = function () {
    var n = this.cols.length;
    if (!n) return;
    var wts = this.colWidths;
    if (!wts || wts.length !== n) {
      wts = [];
      for (var i = 0; i < n; i++) wts.push(100 / n);
      this.colWidths = wts;
    }
    var sum = wts.reduce(function (a, b) {
      return a + b;
    }, 0);
    if (sum <= 0) sum = 1;
    var ch = this.rowEl.children;
    var ci = 0;
    for (var j = 0; j < ch.length; j++) {
      var el = ch[j];
      if (el.classList.contains("imm-col")) {
        var flex = (wts[ci] / sum) * 100;
        el.style.flex = flex + " 1 0";
        ci++;
      }
    }
  };

  ColumnManager.prototype._attachResizer = function (resizerEl, leftIndex) {
    var self = this;
    resizerEl.addEventListener("mousedown", function (ev) {
      ev.preventDefault();
      var startX = ev.clientX;
      var w0 = self.colWidths.slice();
      self._drag = { left: leftIndex, startX: startX, w0: w0 };
      function onMove(e) {
        if (!self._drag) return;
        var dx = e.clientX - self._drag.startX;
        var rowW = self.rowEl.getBoundingClientRect().width;
        if (rowW < 1) rowW = 1;
        var dFrac = (dx / rowW) * 100;
        var i = self._drag.left;
        var w = self._drag.w0.slice();
        var n = self.cols.length;
        var resizerPx = Math.max(0, (n - 1) * 3);
        var freeSpace = Math.max(1, rowW - resizerPx);
        var sum = w.reduce(function (a, b) {
          return a + b;
        }, 0);
        if (sum <= 0) sum = 1;
        var minWeight = (MIN_COL_PX * sum) / freeSpace;
        var C = w[i] + w[i + 1];
        var a0 = w[i] + dFrac;
        var a;
        var b;
        if (C < 2 * minWeight) {
          a = C / 2;
          b = C / 2;
        } else {
          a = Math.max(minWeight, Math.min(C - minWeight, a0));
          b = C - a;
        }
        w[i] = a;
        w[i + 1] = b;
        self.colWidths = w;
        self._applyFlexWidths();
      }
      function onUp() {
        self._drag = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        saveLayout(self);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  };

  ColumnManager.prototype._mountColumnWrap = function (col, showClose) {
    var self = this;
    var wrap = document.createElement("div");
    wrap.className = "imm-col" + (col.id === self.activeId ? " imm-col-active" : "");
    wrap.dataset.cid = col.id;
    wrap.addEventListener("mousedown", function () {
      self.setActive(col.id);
    });
    var head = document.createElement("div");
    head.className = "imm-col-head";
    var t = document.createElement("span");
    t.className = "imm-col-title";
    t.textContent = col.title || col.id.slice(0, 8);
    col.titleEl = t;
    head.appendChild(t);
    if (showClose) {
      var xb = document.createElement("button");
      xb.type = "button";
      xb.className = "imm-col-close";
      xb.textContent = "×";
      xb.title = "关闭此栏";
      xb.addEventListener("click", function (ev) {
        ev.stopPropagation();
        self.removeColumn(col.id);
      });
      head.appendChild(xb);
    }
    var msgs = document.createElement("div");
    msgs.className = "imm-msgs";
    col.wrapEl = wrap;
    col.msgsEl = msgs;
    col.s.chatLoadingEl = null;
    wrap.appendChild(head);
    wrap.appendChild(msgs);
    return wrap;
  };

  ColumnManager.prototype._updateCloseButtonsAll = function () {
    var multi = this.cols.length > 1;
    for (var i = 0; i < this.cols.length; i++) {
      var col = this.cols[i];
      if (!col.wrapEl) continue;
      var head = col.wrapEl.querySelector(".imm-col-head");
      if (!head) continue;
      var xb = head.querySelector(".imm-col-close");
      if (multi && !xb) {
        xb = document.createElement("button");
        xb.type = "button";
        xb.className = "imm-col-close";
        xb.textContent = "×";
        xb.title = "关闭此栏";
        var self = this;
        var cid = col.id;
        xb.addEventListener("click", function (ev) {
          ev.stopPropagation();
          self.removeColumn(cid);
        });
        head.appendChild(xb);
      } else if (!multi && xb) {
        xb.remove();
      }
    }
  };

  ColumnManager.prototype._rebuildAllResizers = function () {
    var rzs = this.rowEl.querySelectorAll(".imm-resizer");
    for (var r = 0; r < rzs.length; r++) rzs[r].remove();
    for (var i = 0; i < this.cols.length - 1; i++) {
      var rz = document.createElement("div");
      rz.className = "imm-resizer";
      rz.style.flex = "0 0 3px";
      this.rowEl.insertBefore(rz, this.cols[i + 1].wrapEl);
      this._attachResizer(rz, i);
    }
  };

  ColumnManager.prototype._rehydrateColumns = function (msgsHtmlByAbortCid) {
    msgsHtmlByAbortCid = msgsHtmlByAbortCid || {};
    for (var k = 0; k < this.cols.length; k++) {
      var c = this.cols[k];
      c.s.historyLoaded = false;
      var snap = msgsHtmlByAbortCid[c.id];
      if (c.s.abortController && typeof snap === "string" && snap.length) {
        c.msgsEl.innerHTML = snap;
        c.s.historyLoaded = true;
        var assis = c.msgsEl.querySelectorAll(".b.a");
        if (assis.length) c.s.streamAssistantEl = assis[assis.length - 1];
        else c.s.streamAssistantEl = null;
        c.s.chatLoadingEl = c.msgsEl.querySelector(".imm-loading") || null;
      } else if (c.s.abortController) {
        c.s.streamAssistantEl = null;
        c.s.chatLoadingEl = null;
        if (c.s.streamAssistantText && typeof IMM.renderMarkdown === "function") {
          var el = document.createElement("div");
          el.className = "b a";
          el.innerHTML = IMM.renderMarkdown(c.s.streamAssistantText);
          c.msgsEl.appendChild(el);
          c.s.streamAssistantEl = el;
        }
        if (!c.s.streamAssistantEl && typeof IMM.immShowChatLoading === "function") {
          IMM.immShowChatLoading(c.msgsEl, c.s);
        }
        c.s.historyLoaded = true;
      } else {
        c.s.streamAssistantEl = null;
        c.s.streamAssistantText = "";
        c.s.roundReasoningText = "";
        c.s.pendingDeltaSeparator = false;
        if (typeof IMM.loadConversationHistory === "function")
          void IMM.loadConversationHistory(c);
      }
    }
  };

  /** 冷启动或必须整行重建时用（会重挂消息区，仅首栏/空行） */
  ColumnManager.prototype._renderFull = function () {
    var self = this;
    var msgsHtmlByAbortCid = {};
    for (var si = 0; si < this.cols.length; si++) {
      var cx = this.cols[si];
      if (cx.msgsEl && cx.s.abortController) msgsHtmlByAbortCid[cx.id] = cx.msgsEl.innerHTML;
    }
    this.rowEl.innerHTML = "";
    this.rowEl.style.display = "flex";
    this.rowEl.style.flexDirection = "row";
    this.rowEl.style.alignItems = "stretch";
    this.rowEl.style.minHeight = "0";
    this.rowEl.style.flex = "1 1 0";
    var multi = this.cols.length > 1;
    for (var i = 0; i < this.cols.length; i++) {
      var col = this.cols[i];
      var wrap = this._mountColumnWrap(col, multi);
      this.rowEl.appendChild(wrap);
      if (i < this.cols.length - 1) {
        var rz = document.createElement("div");
        rz.className = "imm-resizer";
        rz.style.flex = "0 0 3px";
        this.rowEl.appendChild(rz);
        this._attachResizer(rz, i);
      }
    }
    this._applyFlexWidths();
    this._rehydrateColumns(msgsHtmlByAbortCid);
  };

  /** 在右侧追加一栏：已有栏等比缩宽，不重绘消息 DOM */
  ColumnManager.prototype._insertColumnEnd = function (col) {
    var n = this.cols.length;
    var prevN = n - 1;
    for (var w = 0; w < prevN; w++) {
      this.colWidths[w] = (this.colWidths[w] || 100 / prevN) * (prevN / n);
    }
    this.colWidths.push(100 / n);
    this._updateCloseButtonsAll();
    var wrap = this._mountColumnWrap(col, true);
    var rz = document.createElement("div");
    rz.className = "imm-resizer";
    rz.style.flex = "0 0 3px";
    this.rowEl.appendChild(rz);
    this.rowEl.appendChild(wrap);
    this._attachResizer(rz, n - 2);
    this._applyFlexWidths();
    col.s.historyLoaded = false;
    if (typeof IMM.loadConversationHistory === "function" && !col.s.abortController)
      void IMM.loadConversationHistory(col);
  };

  ColumnManager.prototype.setActive = function (id) {
    id = IMM.normalizeConversationId(id);
    if (!id) return;
    var c = this.byId(id);
    if (!c) return;
    this.activeId = id;
    try {
      sessionStorage.setItem("codeWebAgent.activeConversationId", id);
    } catch (e) {}
    var ch = this.rowEl.querySelectorAll(".imm-col");
    for (var i = 0; i < ch.length; i++) {
      ch[i].classList.toggle("imm-col-active", ch[i].dataset.cid === id);
    }
    saveLayout(this);
    if (typeof IMM.onActiveColumnChange === "function") IMM.onActiveColumnChange(c);
  };

  ColumnManager.prototype.addColumn = function (id, title) {
    id = IMM.normalizeConversationId(id) || IMM.newConversationId();
    if (this.byId(id)) {
      this.setActive(id);
      return this.byId(id);
    }
    if (this.cols.length >= MAX_COLS) {
      alert("最多同时打开 " + MAX_COLS + " 个会话栏。");
      return null;
    }
    var col = {
      id: id,
      title: title || "会话 " + id.slice(0, 8),
      wrapEl: null,
      msgsEl: null,
      titleEl: null,
      s: makeColumnState(),
    };
    this.cols.push(col);
    if (!this.activeId) this.activeId = id;
    if (this.cols.length === 1) {
      this.colWidths = [100];
      this._renderFull();
    } else {
      this._insertColumnEnd(col);
    }
    saveLayout(this);
    return col;
  };

  ColumnManager.prototype.removeColumn = function (id) {
    id = IMM.normalizeConversationId(id);
    if (this.cols.length <= 1) return;
    var idx = -1;
    for (var i = 0; i < this.cols.length; i++) {
      if (this.cols[i].id === id) {
        idx = i;
        break;
      }
    }
    if (idx < 0) return;
    var c = this.cols[idx];
    if (c.s.abortController) {
      alert("该栏正在响应，请等待或停止后再关闭。");
      return;
    }
    var el = c.wrapEl;
    if (el && el.parentNode === this.rowEl) {
      var prev = el.previousElementSibling;
      var next = el.nextElementSibling;
      if (prev && prev.classList.contains("imm-resizer")) prev.remove();
      else if (next && next.classList.contains("imm-resizer")) next.remove();
      el.remove();
    }
    this.cols.splice(idx, 1);
    this.colWidths.splice(idx, 1);
    var sum = this.colWidths.reduce(function (a, b) {
      return a + b;
    }, 0);
    if (sum > 0) {
      for (var j = 0; j < this.colWidths.length; j++) this.colWidths[j] *= 100 / sum;
    } else if (this.cols.length) {
      var eq = 100 / this.cols.length;
      this.colWidths = [];
      for (var k = 0; k < this.cols.length; k++) this.colWidths.push(eq);
    }
    if (this.activeId === id) {
      var nx = this.cols[Math.max(0, idx - 1)] || this.cols[0];
      this.activeId = nx ? nx.id : "";
    }
    this._rebuildAllResizers();
    this._updateCloseButtonsAll();
    this._applyFlexWidths();
    if (this.activeId) this.setActive(this.activeId);
    saveLayout(this);
  };

  ColumnManager.prototype.updateTitle = function (id, title) {
    var c = this.byId(id);
    if (!c) return;
    c.title = String(title || "").slice(0, 80);
    if (c.titleEl) c.titleEl.textContent = c.title;
    saveLayout(this);
  };

  IMM.ColumnManager = ColumnManager;
  IMM.loadImmersiveLayout = loadLayout;
  IMM.saveImmersiveLayout = function (cm) {
    saveLayout(cm);
  };
})(window);
