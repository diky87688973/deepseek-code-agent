(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});
  var CM = null;
  var ta,
    goBtn,
    stopBtn,
    modePlus,
    modeMenu,
    modeChip,
    modeChipText,
    modeChipClear,
    hdrModel,
    hdrCid,
    slashPop,
    reasonBtn,
    reasonMenu,
    reasonLabel,
    reasonAnchor,
    modelSubBtn,
    modelSubmenu,
    modelSubWrap,
    sessBtn,
    sessMenu,
    nwCol;
  var slashSelectedIndex = 0;
  var atSelectedIndex = -1;
  var atRestoreSelectPath = null;

  IMM.selectedMode = "auto";
  IMM.selectedModel = "deepseek-v4-flash";
  IMM.allowedModels = ["deepseek-v4-pro", "deepseek-v4-flash"];

  function normalizeMode(m) {
    m = String(m || "").toLowerCase();
    return m === "plan" || m === "execute" ? m : "auto";
  }

  function getActiveCol() {
    return CM ? CM.getActive() : null;
  }

  function isConversationBusy() {
    if (IMM.userConfirmBlocking) return true;
    var c = getActiveCol();
    return !!(c && c.s.abortController);
  }

  function syncModePlusLabel(mode) {
    mode = normalizeMode(mode);
    if (!modePlus) return;
    var label = mode === "auto" ? "Auto" : mode === "plan" ? "Plan" : "Execute";
    modePlus.textContent = label + " +";
    modePlus.classList.remove("plan", "execute");
    if (mode === "plan") modePlus.classList.add("plan");
    if (mode === "execute") modePlus.classList.add("execute");
  }

  function applyReasoningEffort(effort, doPut) {
    if (effort !== "high" && effort !== "max") return;
    if (reasonLabel) {
      reasonLabel.textContent = effort === "high" ? "High" : "Max";
      reasonLabel.style.color = effort === "max" ? "#e8c98a" : "";
    }
    if (reasonBtn) {
      reasonBtn.style.borderColor = effort === "max" ? "#8b6a2d" : "";
    }
    if (reasonMenu) {
      var cs = reasonMenu.querySelectorAll(".reasoning-choice");
      for (var i = 0; i < cs.length; i++) {
        cs[i].classList.remove("reasoning-current");
        if (cs[i].getAttribute("data-effort") === effort) cs[i].classList.add("reasoning-current");
      }
    }
    if (doPut !== false) {
      var c = getActiveCol();
      try {
        fetch("/api/reasoning-effort", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: (c && c.id) || "", effort: effort }),
        }).catch(function () {});
      } catch (e) {}
    }
  }

  function fetchReasoningForCol(col) {
    if (!col || !reasonMenu) return;
    fetch("/api/reasoning-effort?conversation_id=" + encodeURIComponent(col.id))
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reasoning_effort) applyReasoningEffort(String(d.reasoning_effort).toLowerCase(), false);
      })
      .catch(function () {});
  }

  function syncModelSubmenuHighlight() {
    var c = getActiveCol();
    var mid = (c && c.s.selectedModel) || IMM.selectedModel || "";
    var choices = document.querySelectorAll("#immModelSubmenu .imm-model-choice");
    for (var i = 0; i < choices.length; i++) {
      choices[i].classList.toggle("model-current", choices[i].getAttribute("data-model") === mid);
    }
  }

  function applyModeModelUi(mode, model) {
    IMM.selectedMode = normalizeMode(mode);
    IMM.selectedModel = model || IMM.selectedModel;
    syncModePlusLabel(IMM.selectedMode);
    if (modeChip) modeChip.classList.add("hidden");
    if (hdrModel) hdrModel.textContent = "模型: " + (IMM.selectedModel || "");
    syncModelSubmenuHighlight();
  }

  IMM.applyModeModelUi = applyModeModelUi;

  function syncActiveToHeader() {
    var c = getActiveCol();
    if (hdrCid && c) hdrCid.textContent = c.id.slice(0, 8);
    if (c) applyModeModelUi(c.s.selectedMode, c.s.selectedModel);
  }

  IMM.onActiveColumnChange = function (col) {
    syncActiveToHeader();
    fetchReasoningForCol(col);
    syncModelSubmenuHighlight();
    IMM.updateComposerBusy();
    if (typeof IMM.updateImmersiveContextBar === "function") IMM.updateImmersiveContextBar();
    void persistUiState();
  };

  function persistUiState() {
    if (!CM) return Promise.resolve();
    try {
      if (CM.activeId)
        sessionStorage.setItem("codeWebAgent.activeConversationId", CM.activeId);
    } catch (_eSs) {}
    var tabs = CM.cols.slice(-8).map(function (t) {
      return { id: t.id, title: t.title || "会话 " + t.id.slice(0, 8) };
    });
    return fetch("/api/chat/ui-state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active_conversation_id: CM.activeId, tabs: tabs }),
    }).catch(function () {});
  }

  async function refreshConversationTitle(id) {
    var c = CM.byId(id);
    if (!c) return;
    var t0 = String(c.title || "");
    if (t0 && !/^会话\s+[A-Za-z0-9._:-]{8}$/.test(t0) && t0 !== "生成标题中…") return;
    CM.updateTitle(id, "生成标题中…");
    try {
      var r = await fetch("/api/chat/title", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: id }),
      });
      if (!r.ok) return;
      var j = await r.json();
      var title = String((j && j.title) || "").trim();
      if (title) CM.updateTitle(id, title.slice(0, 18));
    } catch (e) {}
  }

  async function loadConversationHistory(col) {
    if (!col || col.s.historyLoaded || col.s.abortController) return;
    col.s.historyLoaded = true;
    try {
      var r = await fetch(
        "/api/chat/history?" + new URLSearchParams({ conversation_id: col.id })
      );
      if (!r.ok) return;
      var j = await r.json();
      if (!j || j.ok !== true) return;
      var items = j.items || [];
      var msgsEl = col.msgsEl;
      while (msgsEl.firstChild) msgsEl.removeChild(msgsEl.firstChild);
      for (var i = 0; i < items.length; i++) {
        var it = items[i] || {};
        if (it.role === "user") IMM.immAddUser(msgsEl, String(it.content || ""));
        else if (it.role === "assistant")
          IMM.immAddAssistantMarkdown(msgsEl, String(it.content || ""));
      }
      msgsEl.scrollTop = msgsEl.scrollHeight;
      if (Array.isArray(j.todo_list) && j.todo_list.length && typeof IMM.renderTodoInColumn === "function") {
        IMM.renderTodoInColumn(col, {
          items: j.todo_list,
          all_done: j.todo_list.every(function (it) { return !!it.done; }),
          collapsed: false,
        });
      }
      msgsEl.scrollTop = msgsEl.scrollHeight;
    } catch (e) {}
  }

  IMM.loadConversationHistory = loadConversationHistory;

  /** 与经典页 updateTaskControls 一致：仅在有流式 AbortController 时显示「停止」并隐藏「发送」；其余忙态下发送置灰但仍可见 */
  IMM.updateComposerBusy = function () {
    var c = getActiveCol();
    var canStop = !!(c && c.s.abortController);
    var busy = isConversationBusy();
    if (goBtn) {
      goBtn.classList.toggle("hidden", canStop);
      goBtn.disabled = !canStop && busy;
    }
    if (stopBtn) {
      stopBtn.classList.toggle("hidden", !canStop);
      stopBtn.disabled = !canStop;
    }
  };

  async function sendChatMessage() {
    if (!ta || !CM) return;
    var text = String(ta.value || "").trim();
    if (!text) return;
    if (isConversationBusy()) {
      alert("当前栏仍在执行中，请等待完成后再发送。");
      return;
    }
    var col = getActiveCol();
    if (!col) return;
    var sendCid = col.id;
    IMM.immAddUser(col.msgsEl, text);
    ta.value = "";
    autoResize();
    IMM.immShowChatLoading(col.msgsEl, col.s);
    var controller = new AbortController();
    col.s.abortController = controller;
    col.s.activeRunId = "";
    IMM.updateComposerBusy();
    try {
      var r = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          conversation_id: sendCid,
          mode: col.s.selectedMode || IMM.selectedMode,
          model: col.s.selectedModel || IMM.selectedModel,
        }),
        signal: controller.signal,
      });
      if (!r.ok) {
        var bt = "";
        try {
          bt = await r.text();
        } catch (e) {}
        IMM.immHideChatLoading(col.s);
        IMM.immAddAssistantMarkdown(col.msgsEl, "HTTP " + r.status + " " + String(bt || "").slice(0, 400));
        return;
      }
      await IMM.drainChatSseFromResponse(r, sendCid, CM);
      void refreshConversationTitle(sendCid);
    } catch (err) {
      if (!(err && err.name === "AbortError")) {
        IMM.immHideChatLoading(col.s);
        IMM.immAddAssistantMarkdown(
          col.msgsEl,
          "请求失败: " + (err && err.message ? err.message : String(err))
        );
      }
    } finally {
      col.s.abortController = null;
      IMM.immHideChatLoading(col.s);
      IMM.updateComposerBusy();
      persistUiState();
    }
  }

  function stopCurrent() {
    var col = getActiveCol();
    if (!col || !col.s.abortController) return;
    var rid = String(col.s.activeRunId || "");
    try {
      fetch("/api/chat/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: col.id, run_id: rid }),
      }).catch(function () {});
    } catch (e) {}
    try {
      col.s.abortController.abort();
    } catch (e2) {}
    col.s.abortController = null;
    col.s.activeRunId = "";
    IMM.immHideChatLoading(col.s);
    IMM.immAddAssistantMarkdown(col.msgsEl, "任务已停止。");
    IMM.updateComposerBusy();
  }

  function autoResize() {
    if (!ta) return;
    ta.style.height = "auto";
    var maxH = 320;
    var nh = Math.min(ta.scrollHeight, maxH);
    ta.style.height = nh + "px";
    ta.style.overflowY = ta.scrollHeight > maxH ? "auto" : "hidden";
  }

  function applyMode(m) {
    hideSlash();
    m = normalizeMode(m);
    var c = getActiveCol();
    if (c) c.s.selectedMode = m;
    IMM.selectedMode = m;
    applyModeModelUi(m, IMM.selectedModel);
    if (modeMenu) modeMenu.classList.add("hidden");
    if (modelSubmenu) modelSubmenu.classList.add("hidden");
    persistUiState();
  }

  function applyModel(mid) {
    mid = String(mid || "").trim();
    var am = IMM.allowedModels || [];
    if (am.indexOf(mid) < 0) return;
    var c = getActiveCol();
    if (c) c.s.selectedModel = mid;
    IMM.selectedModel = mid;
    applyModeModelUi(IMM.selectedMode, mid);
    if (modelSubmenu) modelSubmenu.classList.add("hidden");
    syncModelSubmenuHighlight();
    persistUiState();
  }

  function getReasonEffort() {
    var cur = reasonMenu && reasonMenu.querySelector(".reasoning-choice.reasoning-current");
    return (cur && cur.getAttribute("data-effort")) || "max";
  }

  function docClickClosePopovers(e) {
    var t = e.target;
    var modeAnchor = modePlus ? modePlus.closest(".imm-mode-anchor") : null;
    if (modeMenu && !modeMenu.classList.contains("hidden")) {
      if (!modeAnchor || !modeAnchor.contains(t)) modeMenu.classList.add("hidden");
    }
    if (modelSubmenu && !modelSubmenu.classList.contains("hidden")) {
      if (!modelSubWrap || !modelSubWrap.contains(t)) modelSubmenu.classList.add("hidden");
    }
    if (reasonMenu && !reasonMenu.classList.contains("hidden")) {
      if (!reasonAnchor || !reasonAnchor.contains(t)) reasonMenu.classList.add("hidden");
    }
    if (slashPop && !slashPop.classList.contains("hidden") && !slashPop.contains(t) && t !== ta) hideSlash();
    if (sessMenu && !sessMenu.classList.contains("hidden")) {
      if (!sessMenu.contains(t) && t !== sessBtn) sessMenu.classList.add("hidden");
    }
  }

  function initModePicker() {
    modePlus.addEventListener("click", function (e) {
      e.stopPropagation();
      if (reasonMenu) reasonMenu.classList.add("hidden");
      if (modelSubmenu) modelSubmenu.classList.add("hidden");
      modeMenu.classList.toggle("hidden");
    });
    modeMenu.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-mode],[data-action],[data-model]");
      if (!btn || !btn.dataset) return;
      if (btn.dataset.mode) {
        applyMode(btn.dataset.mode);
        return;
      }
      if (btn.dataset.action === "model-menu") {
        e.stopPropagation();
        modelSubmenu.classList.toggle("hidden");
        return;
      }
      if (btn.dataset.model) {
        applyModel(btn.dataset.model);
        modeMenu.classList.add("hidden");
        modelSubmenu.classList.add("hidden");
      }
    });
    modeChipClear.addEventListener("click", function (e) {
      e.stopPropagation();
      applyMode("auto");
    });
    document.addEventListener("click", docClickClosePopovers);
  }

  function initReasoning() {
    if (!reasonBtn || !reasonMenu) return;
    reasonBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      modeMenu.classList.add("hidden");
      if (modelSubmenu) modelSubmenu.classList.add("hidden");
      reasonMenu.classList.toggle("hidden");
    });
    reasonMenu.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-effort]");
      if (!btn || !btn.dataset || !btn.dataset.effort) return;
      applyReasoningEffort(btn.dataset.effort, true);
      reasonMenu.classList.add("hidden");
    });
    fetch("/api/reasoning-effort?conversation_id=" + encodeURIComponent((getActiveCol() && getActiveCol().id) || ""))
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reasoning_effort) applyReasoningEffort(String(d.reasoning_effort).toLowerCase(), false);
      })
      .catch(function () {});
  }

  function hideSlash() {
    if (!slashPop) return;
    slashPop.classList.add("hidden");
    slashPop.setAttribute("aria-hidden", "true");
  }

  function updateSlashPopHints() {
    var items = slashPop.querySelectorAll(".slash-item");
    var visibles = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].style.display !== "none") visibles.push(items[i]);
    }
    if (!visibles.length) return;
    var selIdx = -1;
    for (var j = 0; j < visibles.length; j++) {
      if (visibles[j].classList.contains("selected")) {
        selIdx = j;
        break;
      }
    }
    if (selIdx < 0) {
      visibles[0].classList.add("selected");
      selIdx = 0;
    }
    var ci = slashPop.children;
    for (var si = 0; si < ci.length; si++) {
      if (ci[si] === visibles[selIdx]) {
        slashSelectedIndex = si;
        break;
      }
    }
    for (var k = 0; k < visibles.length; k++) {
      var sh = visibles[k].querySelector(".sh");
      if (!sh) continue;
      var sk = (visibles[k].getAttribute("data-slash") || "").charAt(0).toUpperCase();
      var hint;
      if (k === selIdx) hint = "Enter";
      else if (k < selIdx) hint = "↑ + Enter";
      else hint = "↓ + Enter";
      sh.textContent = sk + "  " + hint;
    }
  }

  function showSlashFromTyping() {
    if (!slashPop || !ta) return;
    var ci = slashPop.children;
    for (var i = 0; i < ci.length; i++) {
      ci[i].classList.remove("selected");
      ci[i].style.display = ci[i].getAttribute("data-slash") === IMM.selectedMode ? "none" : "";
    }
    for (var j = 0; j < ci.length; j++) {
      if (ci[j].style.display !== "none") {
        ci[j].classList.add("selected");
        slashSelectedIndex = j;
        break;
      }
    }
    updateSlashPopHints();
    slashPop.classList.remove("hidden");
    slashPop.setAttribute("aria-hidden", "false");
  }

  function initSlash() {
    slashPop.addEventListener("click", function (e) {
      var b = e.target.closest(".slash-item");
      if (!b) return;
      e.stopPropagation();
      hideSlash();
      ta.value = "";
      applyMode(b.getAttribute("data-slash") || "auto");
    });
    ta.addEventListener("keyup", function (e) {
      if (e.isComposing) return;
      if (e.key === "Escape") {
        hideSlash();
        return;
      }
      if (e.key === "/" && document.activeElement === ta && ta.value === "/") showSlashFromTyping();
    });
    ta.addEventListener("input", function () {
      autoResize();
      if (ta.value === "/" && document.activeElement === ta) {
        showSlashFromTyping();
      } else if (ta.value !== "/") hideSlash();
    });
  }

  function getAtPop() {
    return document.getElementById("immAtPop");
  }
  function getAtList() {
    return document.getElementById("immAtList");
  }
  function getAtPath() {
    return document.getElementById("immAtPath");
  }
  function atNormPath(s) {
    return String(s || "")
      .replace(/\\/g, "/")
      .toLowerCase();
  }
  function atLastTriggerIndexIn(t) {
    var re = /(?:^|\s)@/g,
      m,
      last = -1;
    while ((m = re.exec(t)) !== null) last = m.index + m[0].length - 1;
    return last;
  }
  function updateAtSelection() {
    var items = getAtList().querySelectorAll(".at-item");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("selected", i === atSelectedIndex);
    }
  }
  function selectAtFile(fpath) {
    var before = ta.value.slice(0, ta.selectionStart || 0);
    var after = ta.value.slice(ta.selectionStart || 0);
    var atIdx = atLastTriggerIndexIn(before);
    if (atIdx >= 0) ta.value = before.slice(0, atIdx) + "@" + fpath + " " + after;
    else ta.value = before + "@" + fpath + " " + after;
    hideAt();
    ta.focus();
  }
  function atMentionActive() {
    var v = ta.value,
      sel = ta.selectionStart || 0,
      before = v.slice(0, sel);
    var re = /(?:^|\s)@/g,
      m,
      last = null;
    while ((m = re.exec(before)) !== null) last = m;
    if (!last) return false;
    var atIdx = last.index + last[0].length - 1;
    if (sel <= atIdx) return false;
    var seg = before.slice(atIdx + 1, sel);
    return !/\s/.test(seg);
  }
  function hideAt() {
    atSelectedIndex = -1;
    atRestoreSelectPath = null;
    var p = getAtPop();
    if (p) {
      p.style.visibility = "hidden";
      p.style.top = "-99999px";
      p.setAttribute("aria-hidden", "true");
    }
  }
  function positionAtPop() {
    var pop = getAtPop();
    if (!pop || !ta) return;
    var rect = ta.getBoundingClientRect();
    pop.style.position = "fixed";
    var h = pop.offsetHeight || 300;
    var top = rect.top - h - 10;
    if (top < 8) top = 8;
    var pw = Math.min(1024, Math.max(280, pop.offsetWidth || 360));
    var left = Math.min(Math.max(8, rect.left), w.innerWidth - pw - 8);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }
  async function loadAtDir(path) {
    try {
      var resp = await fetch("/api/dir-browse?path=" + encodeURIComponent(path || ""));
      var data = await resp.json();
      getAtPath().textContent = data.current;
      getAtPath().dataset.parent = data.parent || "";
      var up = document.getElementById("immAtUp");
      if (up) up.style.display = !data.parent || data.parent === data.current ? "none" : "";
      getAtList().innerHTML = "";
      atSelectedIndex = -1;
      (data.items || []).forEach(function (item, idx) {
        var div = document.createElement("div");
        div.className = "at-item" + (item.type === "dir" ? " dir" : "");
        div.dataset.type = item.type;
        div.dataset.path = item.path;
        if (item.type === "dir") div.title = "单击进入子目录；回车插入该目录路径";
        div.innerHTML =
          '<span class="at-icon">' +
          (item.type === "dir" ? "📁" : "📄") +
          '</span><span class="at-name">' +
          IMM.escapeHtml(item.name) +
          "</span>";
        div.addEventListener("click", function () {
          if (item.type === "dir") void loadAtDir(item.path);
          else selectAtFile(item.path);
        });
        div.addEventListener("mouseenter", function () {
          atSelectedIndex = idx;
          updateAtSelection();
        });
        getAtList().appendChild(div);
      });
      var pick = 0;
      if (atRestoreSelectPath) {
        var want = atRestoreSelectPath;
        atRestoreSelectPath = null;
        for (var rj = 0; rj < (data.items || []).length; rj++) {
          if (
            data.items[rj].type === "dir" &&
            atNormPath(data.items[rj].path) === atNormPath(want)
          ) {
            pick = rj;
            break;
          }
        }
      }
      if (data.items && data.items.length) {
        atSelectedIndex = pick;
        updateAtSelection();
      }
      w.requestAnimationFrame(function () {
        w.requestAnimationFrame(positionAtPop);
      });
    } catch (e) {
      getAtList().innerHTML = '<div class="at-item" style="color:#f66">加载失败</div>';
    }
  }
  function initAt() {
    document.getElementById("immAtUp").addEventListener("click", function () {
      var cur = getAtPath().textContent.trim();
      var parentStored = getAtPath().dataset.parent || "";
      if (!parentStored) return;
      var p = parentStored || cur.substring(0, cur.lastIndexOf("/"));
      if (!p || p.length < 3) {
        if (p && p.length === 2 && p.charAt(1) === ":") p = p + "/";
        else p = cur;
      }
      if (p !== cur) atRestoreSelectPath = cur;
      void loadAtDir(p);
    });
    ta.addEventListener("input", function () {
      if (atMentionActive()) {
        var pop = getAtPop();
        pop.style.visibility = "visible";
        pop.setAttribute("aria-hidden", "false");
        void loadAtDir("");
      } else hideAt();
    });
    w.addEventListener("resize", function () {
      if (getAtPop() && getAtPop().style.visibility === "visible") positionAtPop();
    });
  }

  function renderSessionMenu(items) {
    sessMenu.innerHTML = "";
    var openIds = {};
    if (CM) {
      for (var oi = 0; oi < CM.cols.length; oi++) openIds[CM.cols[oi].id] = true;
    }
    var nb = document.createElement("button");
    nb.type = "button";
    nb.className = "imm-session-row imm-session-new";
    nb.textContent = "新会话栏 +";
    nb.onclick = function () {
      sessMenu.classList.add("hidden");
      var nc = CM.addColumn(IMM.newConversationId(), "");
      if (nc) {
        CM.setActive(nc.id);
        loadConversationHistory(nc);
        persistUiState();
      }
    };
    sessMenu.appendChild(nb);
    items.forEach(function (s) {
      var id = IMM.normalizeConversationId(s && s.id);
      if (!id) return;
      var row = document.createElement("button");
      row.type = "button";
      row.className = "imm-session-row" + (openIds[id] ? " imm-session-open" : "");
      var title = String(s.title || "会话 " + id.slice(0, 8));
      row.innerHTML =
        '<span class="imm-session-title">' +
        IMM.escapeHtml(title) +
        "</span>" +
        (openIds[id] ? '<span class="imm-session-open-badge">已打开</span>' : "");
      row.onclick = function () {
        sessMenu.classList.add("hidden");
        var ex = CM.byId(id);
        if (!ex) {
          ex = CM.addColumn(id, String(s.title || ""));
          loadHistoryForCol(ex);
        } else CM.setActive(id);
        persistUiState();
      };
      sessMenu.appendChild(row);
    });
  }

  function loadHistoryForCol(c) {
    if (!c) return;
    c.s.historyLoaded = false;
    void loadConversationHistory(c);
  }

  async function toggleSessionMenu() {
    if (!sessMenu) return;
    if (!sessMenu.classList.contains("hidden")) {
      sessMenu.classList.add("hidden");
      return;
    }
    sessMenu.classList.remove("hidden");
    sessMenu.innerHTML = '<div class="imm-session-empty">加载中…</div>';
    try {
      var r = await fetch("/api/chat/sessions");
      var j = await r.json();
      renderSessionMenu((j && j.sessions) || []);
    } catch (e) {
      sessMenu.innerHTML = '<div class="imm-session-empty">加载失败</div>';
    }
  }

  IMM.initComposer = function (columnManager) {
    CM = columnManager;
    ta = document.getElementById("immT");
    goBtn = document.getElementById("immGo");
    stopBtn = document.getElementById("immStop");
    modePlus = document.getElementById("immModePlus");
    modeMenu = document.getElementById("immModeMenu");
    modeChip = document.getElementById("immModeChip");
    modeChipText = document.getElementById("immModeChipText");
    modeChipClear = document.getElementById("immModeChipClear");
    hdrModel = document.getElementById("immHdrModel");
    hdrCid = document.getElementById("immCid");
    slashPop = document.getElementById("immSlashPop");
    reasonBtn = document.getElementById("immReasonBtn");
    reasonMenu = document.getElementById("immReasonMenu");
    reasonLabel = document.getElementById("immReasonLabel");
    reasonAnchor = document.getElementById("immReasonAnchor");
    modelSubBtn = document.getElementById("immModelSubBtn");
    modelSubmenu = document.getElementById("immModelSubmenu");
    modelSubWrap = document.getElementById("immModelSubWrap");
    sessBtn = document.getElementById("immSessBtn");
    sessMenu = document.getElementById("immSessMenu");
    nwCol = document.getElementById("immNwCol");
    initModePicker();
    initReasoning();
    initSlash();
    initAt();
    goBtn.addEventListener("click", function () {
      void sendChatMessage();
    });
    stopBtn.addEventListener("click", stopCurrent);
    ta.addEventListener("keydown", function (e) {
      if (e.isComposing) return;
      var sh = slashPop && !slashPop.classList.contains("hidden");
      var ah = getAtPop() && getAtPop().style.visibility !== "hidden";
      var k = e.key;
      var kl = k.toLowerCase();
      var ci = slashPop ? slashPop.children : [];
      if (ah && k === "Escape") {
        e.preventDefault();
        hideAt();
        return;
      }
      if (ah) {
        var ai = getAtList().querySelectorAll(".at-item");
        if (k === "ArrowDown" || k === "ArrowUp") {
          e.preventDefault();
          var nn = ai.length;
          if (!nn) return;
          if (atSelectedIndex < 0 || atSelectedIndex >= nn)
            atSelectedIndex = k === "ArrowDown" ? 0 : nn - 1;
          else if (k === "ArrowDown") {
            if (atSelectedIndex < nn - 1) atSelectedIndex++;
          } else {
            if (atSelectedIndex > 0) atSelectedIndex--;
          }
          updateAtSelection();
          var row = ai[atSelectedIndex];
          if (row && row.scrollIntoView) {
            try {
              row.scrollIntoView({ block: "nearest" });
            } catch (_) {}
          }
          return;
        }
        if (k === "ArrowRight") {
          e.preventDefault();
          var pickR = atSelectedIndex;
          if (pickR < 0 || pickR >= ai.length) pickR = 0;
          if (ai.length && ai[pickR] && ai[pickR].dataset.type === "dir")
            void loadAtDir(ai[pickR].dataset.path);
          return;
        }
        if (k === "ArrowLeft") {
          e.preventDefault();
          var curL = getAtPath().textContent.trim();
          var ps = getAtPath().dataset.parent || "";
          if (!ps) return;
          var pU = ps || curL.substring(0, curL.lastIndexOf("/"));
          if (!pU || pU.length < 3) {
            if (pU && pU.length === 2 && pU.charAt(1) === ":") pU = pU + "/";
            else pU = curL;
          }
          if (pU !== curL) atRestoreSelectPath = curL;
          void loadAtDir(pU);
          return;
        }
        if (k === "Enter" || e.code === "Enter" || e.code === "NumpadEnter") {
          e.preventDefault();
          var pick = atSelectedIndex;
          if (pick < 0 || pick >= ai.length) pick = 0;
          if (ai.length && ai[pick]) {
            var sel = ai[pick];
            selectAtFile(sel.dataset.path);
          }
          return;
        }
        if (atMentionActive()) return;
        hideSlash();
        hideAt();
        return;
      }
      if (!sh && !ah) {
        if (!e.shiftKey && (k === "Enter" || e.code === "Enter" || e.code === "NumpadEnter")) {
          e.preventDefault();
          void sendChatMessage();
        }
        return;
      }
      if (sh && k === "Escape") {
        e.preventDefault();
        hideSlash();
        return;
      }
      if (sh && (k === "ArrowDown" || k === "ArrowUp")) {
        e.preventDefault();
        var vi3 = [];
        for (var i3 = 0; i3 < ci.length; i3++) {
          if (ci[i3].style.display !== "none") vi3.push(i3);
        }
        if (!vi3.length) return;
        var idx3 = vi3.indexOf(slashSelectedIndex);
        if (idx3 < 0) idx3 = k === "ArrowDown" ? -1 : 0;
        else if (k === "ArrowDown") {
          if (idx3 < vi3.length - 1) idx3++;
        } else {
          if (idx3 > 0) idx3--;
        }
        var cur3 = ci[slashSelectedIndex];
        if (cur3) cur3.classList.remove("selected");
        slashSelectedIndex = vi3[idx3];
        var nxt3 = ci[slashSelectedIndex];
        if (nxt3) nxt3.classList.add("selected");
        updateSlashPopHints();
        return;
      }
      if (sh && (k === "Enter" || e.code === "Enter" || e.code === "NumpadEnter")) {
        e.preventDefault();
        var sel4 = ci[slashSelectedIndex];
        if (sel4 && sel4.dataset && sel4.dataset.slash) {
          applyMode(sel4.dataset.slash);
          ta.value = "";
        } else hideSlash();
        return;
      }
      if (sh && (kl === "a" || kl === "p" || kl === "e")) {
        e.preventDefault();
        hideSlash();
        ta.value = "";
        if (kl === "a") applyMode("auto");
        else if (kl === "p") applyMode("plan");
        else applyMode("execute");
        return;
      }
      if (sh) hideSlash();
    });
    sessBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      void toggleSessionMenu();
    });
    nwCol.addEventListener("click", function () {
      var nc = CM.addColumn(IMM.newConversationId(), "");
      if (nc) {
        CM.setActive(nc.id);
        void persistUiState();
      }
    });
    var classicPill = document.getElementById("immClassicPill");
    if (classicPill) {
      classicPill.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        void persistUiState().finally(function () {
          window.location.href = "/";
        });
      });
    }
    syncModelSubmenuHighlight();
    IMM.updateComposerBusy();
    if (typeof IMM.updateImmersiveContextBar === "function") IMM.updateImmersiveContextBar();
  };
})(window);
