/* SSE：按 conversation_id 写入对应分栏；丢弃用量/上下文/步骤 UI；保留 user_confirm */
(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});

  function scrollMsgs(msgsEl) {
    if (!msgsEl) return;
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function showChatLoading(msgsEl, s) {
    if (s.chatLoadingEl || !msgsEl) return;
    var el = document.createElement("div");
    el.className = "b a imm-loading";
    el.innerHTML =
      '<span class="imm-spinner"></span> <span style="color:#888;font-size:12px">正在思考中…</span>';
    msgsEl.appendChild(el);
    s.chatLoadingEl = el;
    scrollMsgs(msgsEl);
  }

  function hideChatLoading(s) {
    if (s.chatLoadingEl) {
      try {
        s.chatLoadingEl.remove();
      } catch (e) {}
      s.chatLoadingEl = null;
    }
  }

  function addUser(msgsEl, text) {
    if (!msgsEl) return;
    var e = document.createElement("div");
    e.className = "b u";
    e.textContent = text;
    msgsEl.appendChild(e);
    scrollMsgs(msgsEl);
  }

  function peerAvatarLetter(name) {
    var s = String(name || "A").trim();
    return s ? s.charAt(0).toUpperCase() : "A";
  }

  function buildPeerMetaHtml(metaRaw) {
    var line = String(metaRaw || "")
      .trim()
      .replace(/\|/g, " ");
    if (!line) return "";
    return (
      '<span class="peer-meta-tag">' + IMM.escapeHtml(line) + "</span>"
    );
  }

  function addPeerUser(msgsEl, text, name, cid) {
    if (!msgsEl) return;
    var label = String(name || cid || "Agent");
    var content = text || "";
    var metaHtml = "";
    if (content.indexOf("[from=") === 0) {
      var ci = content.indexOf("]");
      if (ci > 0) {
        var metaRaw = content.slice(1, ci);
        content = content.slice(ci + 1).trim();
        metaHtml = buildPeerMetaHtml(metaRaw);
      }
    }
    var e = document.createElement("div");
    e.className = "peer-chat-row";
    var nameLine = IMM.escapeHtml(label);
    if (cid && cid !== label) {
      nameLine += ' <span class="peer-agent-cid">' + IMM.escapeHtml(String(cid).slice(0, 8)) + "</span>";
    }
    e.innerHTML =
      '<div class="peer-top">' +
      '<div class="peer-avatar" title="' +
      IMM.escapeHtml(label) +
      '">' +
      IMM.escapeHtml(peerAvatarLetter(label)) +
      "</div>" +
      '<div class="peer-top-text">' +
      '<div class="peer-agent-name">' +
      nameLine +
      "</div>" +
      (metaHtml || "") +
      "</div></div>" +
      '<div class="peer-bubble"><div class="peer-agent-body b a"></div></div>';
    var bodyEl = e.querySelector(".peer-agent-body");
    if (bodyEl) {
      bodyEl.className = "peer-agent-body b a";
      bodyEl.innerHTML = IMM.renderMarkdown(content || "");
    }
    msgsEl.appendChild(e);
    scrollMsgs(msgsEl);
  }

  function addAssistantMarkdown(msgsEl, md) {
    if (!msgsEl) return;
    var e = document.createElement("div");
    e.className = "b a";
    e.innerHTML = IMM.renderMarkdown(md || "");
    msgsEl.appendChild(e);
    scrollMsgs(msgsEl);
  }

  function ensureStreamBubble(col) {
    var s = col.s;
    var msgsEl = col.msgsEl;
    if (s.streamAssistantEl) return s.streamAssistantEl;
    if (!msgsEl) return null;
    var e = document.createElement("div");
    e.className = "b a";
    e.innerHTML = "";
    msgsEl.appendChild(e);
    scrollMsgs(msgsEl);
    s.streamAssistantEl = e;
    s.streamAssistantText = "";
    return e;
  }

  function appendDelta(col, text) {
    if (typeof text !== "string" || !text) return;
    var s = col.s;
    var e = ensureStreamBubble(col);
    if (!e) return;
    if (s.pendingDeltaSeparator && s.streamAssistantText) s.streamAssistantText += "\n\n";
    s.pendingDeltaSeparator = false;
    s.streamAssistantText += text;
    e.innerHTML = IMM.renderMarkdown(s.streamAssistantText);
    scrollMsgs(col.msgsEl);
  }

  function finalizeStream(col, content) {
    var s = col.s;
    if (s.streamAssistantEl) {
      if (typeof content === "string" && content) {
        s.streamAssistantText = String(content);
        s.streamAssistantEl.innerHTML = IMM.renderMarkdown(s.streamAssistantText);
      }
      s.streamAssistantEl = null;
      s.streamAssistantText = "";
      s.pendingDeltaSeparator = false;
      scrollMsgs(col.msgsEl);
      return true;
    }
    return false;
  }

  function resetTurnState(col) {
    var s = col.s;
    hideChatLoading(s);
    s.streamAssistantEl = null;
    s.streamAssistantText = "";
    s.roundReasoningText = "";
    s.pendingDeltaSeparator = false;
    s.anyToolThisTurn = false;
    IMM.userConfirmBlocking = false;
  }

  /** 模型只写 reasoning、不写 content 时，把推理流同步到主对话区（沉浸页无步骤面板）。 */
  function appendReasoningToChatIfEmpty(col, chunk, isFullSync) {
    if (typeof chunk !== "string" || !chunk) return;
    var s = col.s;
    if (isFullSync) s.roundReasoningText = chunk;
    else s.roundReasoningText = (s.roundReasoningText || "") + chunk;
  } function promoteReasoningToChatIfNeeded(col) {
    var s = col.s;
    var rt = (s.roundReasoningText || "").trim();
    if (!rt) return;
    if ((s.streamAssistantText || "").trim()) return;
    if (!finalizeStream(col, rt)) addAssistantMarkdown(col.msgsEl, rt);
  }

  function renderTodoInColumn(col, ev) {
    var items = Array.isArray(ev.items) ? ev.items : [];
    if (ev.close) {
      if (col && col.wrapEl) {
        var host = col.wrapEl.querySelector(".imm-col-todo");
        if (host) host.style.display = "none";
      }
      return;
    }
    if (!items.length) return;
    if (!col || !col.wrapEl) return;
    var wrap = col.wrapEl;
    var host = wrap.querySelector(".imm-col-todo");
    if (!host) {
      host = document.createElement("div");
      host.className = "imm-col-todo";
      var msgs = wrap.querySelector(".imm-msgs");
      if (msgs && msgs.nextSibling) wrap.insertBefore(host, msgs.nextSibling);
      else wrap.appendChild(host);
      host.addEventListener("click", function (e) {
        var hdr = e.target.closest(".imm-todo-hdr");
        if (!hdr) return;
        if (e.target.closest(".imm-todo-hdr-count")) return;
        host.classList.toggle("collapsed");
      });
    }
    host.style.display = "";
    var needCollapse = !!(ev.collapsed);
    var done = 0, html = '<div class="imm-todo-hdr"><span class="imm-todo-hdr-title">📋 Todo List</span><span class="imm-todo-hdr-count">0/0</span><span class="imm-todo-collapse-icon">▼</span></div><div class="imm-todo-body"><div class="imm-todo-scroll"><div class="imm-todo-items">';
    for (var i = 0; i < items.length; i++) {
      if (items[i].done) done++;
      var cls = "imm-todo-row" + (items[i].done ? " done" : "");
      html += '<div class="'+cls+'"><span class="imm-todo-cb'+(items[i].done?" done":"")+'"></span><span class="imm-todo-text'+(items[i].done?" done":"")+'">'+String(items[i].text||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")+'</span></div>';
    }
    html += '</div></div></div>';
    host.innerHTML = html;
    var cnt = host.querySelector(".imm-todo-hdr-count");
    if (cnt) cnt.textContent = done+"/"+items.length;
    if (ev.all_done) host.classList.add("imm-todo-all-done");
    else host.classList.remove("imm-todo-all-done");
    void host.offsetHeight;
    if (needCollapse) {
      host.classList.add("collapsed");
    } else {
      host.classList.remove("collapsed");
    }
  }
  IMM.renderTodoInColumn = renderTodoInColumn;
  function openUserConfirm(ev, col, CM, drainFn) {
    if (IMM.userConfirmCardHost || !ev.user_confirm_required) return;
    IMM.userConfirmBlocking = true;
    hideChatLoading(col.s);
    var opts = Array.isArray(ev.user_confirm_options) ? ev.user_confirm_options : [];
    var title = String(ev.user_confirm_title || "请确认");
    var multi = !!ev.user_confirm_multi;
    var tailIdx = opts.length;
    var msgsEl = col.msgsEl;
    var wrap = document.createElement("div");
    wrap.className = "b a user-confirm-card-outer";
    var card = document.createElement("div");
    card.className = "chat-diff-card user-confirm-card";
    var cap = document.createElement("div");
    cap.className = "chat-diff-cap user-confirm-cap user-confirm-cap";
    var capLine = document.createElement("div");
    capLine.className = "user-confirm-cap-line";
    var h = document.createElement("span");
    h.className = "uc-title";
    h.textContent = title;
    var badge = document.createElement("span");
    badge.className = "user-confirm-badge";
    badge.textContent = multi ? "待确认·多选" : "待确认·单选";
    capLine.appendChild(h);
    capLine.appendChild(badge);
    cap.appendChild(capLine);
    card.appendChild(cap);
    var body = document.createElement("div");
    body.className = "user-confirm-body";
    var btns = document.createElement("div");
    btns.className = "user-confirm-opts";
    var pickSingle = -1;
    var pickMulti = {};
    var rows = [];
    var customInputEl = null;
    function syncRows() {
      for (var si = 0; si < rows.length; si++) {
        var R = rows[si];
        var on = multi ? !!pickMulti[R.idx] : pickSingle === R.idx;
        R.icon.classList.toggle("uc-on", on);
        R.row.classList.toggle("uc-row-picked", on);
      }
    }
    function toggleIdx(idx) {
      if (multi) {
        if (pickMulti[idx]) delete pickMulti[idx];
        else pickMulti[idx] = 1;
        syncRows();
      } else {
        pickSingle = pickSingle === idx ? -1 : idx;
        syncRows();
      }
    }
    function buildFinal() {
      var pref = "自定义说明：";
      if (multi) {
        var keys = Object.keys(pickMulti)
          .map(function (x) {
            return parseInt(x, 10);
          })
          .filter(function (x) {
            return !isNaN(x) && x >= 0 && x <= tailIdx;
          });
        keys.sort(function (a, b) {
          return a - b;
        });
        if (!keys.length) return "";
        var parts = [];
        for (var k = 0; k < keys.length; k++) {
          var j = keys[k];
          if (j === tailIdx) {
            var ex2 = customInputEl ? String(customInputEl.value || "").trim() : "";
            if (ex2) parts.push(pref + ex2);
          } else parts.push(String(opts[j] || ""));
        }
        return parts.join("\n");
      }
      if (pickSingle < 0 || pickSingle > tailIdx) return "";
      if (pickSingle === tailIdx) {
        var ex = customInputEl ? String(customInputEl.value || "").trim() : "";
        return ex ? pref + ex : "";
      }
      return String(opts[pickSingle] || "");
    }
    function addRow(idx, label, isTail) {
      var row = document.createElement("div");
      row.className =
        "uc-opt-row" +
        (multi ? " uc-multi" : " uc-single") +
        (isTail ? " uc-has-custom" : "");
      var icon = document.createElement("span");
      icon.className = multi ? "uc-check" : "uc-radio";
      icon.setAttribute("aria-hidden", "true");
      row.appendChild(icon);
      if (isTail) {
        var inp = document.createElement("input");
        inp.type = "text";
        inp.className = "uc-opt-input";
        inp.placeholder = "自定义说明";
        inp.autocomplete = "off";
        customInputEl = inp;
        inp.addEventListener("focus", function () {
          if (multi) pickMulti[idx] = 1;
          else pickSingle = idx;
          syncRows();
        });
        row.appendChild(inp);
      } else {
        var lab = document.createElement("span");
        lab.className = "uc-opt-label";
        lab.textContent = label;
        row.appendChild(lab);
      }
      row.addEventListener("click", function (e) {
        if (e.target && e.target.closest && e.target.closest(".uc-opt-input")) return;
        toggleIdx(idx);
      });
      btns.appendChild(row);
      rows.push({ row: row, icon: icon, idx: idx });
    }
    for (var oi = 0; oi < opts.length; oi++) addRow(oi, String(opts[oi]), false);
    addRow(tailIdx, "", true);
    syncRows();
    body.appendChild(btns);
    var act = document.createElement("div");
    act.className = "user-confirm-actions";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "mode-plus";
    cancel.textContent = "取消";
    var ok = document.createElement("button");
    ok.type = "button";
    ok.className = "mode-plus";
    ok.textContent = "确认";
    act.appendChild(cancel);
    act.appendChild(ok);
    body.appendChild(act);
    card.appendChild(body);
    wrap.appendChild(card);
    msgsEl.appendChild(wrap);
    IMM.userConfirmCardHost = wrap;
    scrollMsgs(msgsEl);
    function cleanup() {
      if (IMM.userConfirmCardHost) {
        try {
          IMM.userConfirmCardHost.remove();
        } catch (e) {}
        IMM.userConfirmCardHost = null;
      }
      IMM.userConfirmBlocking = false;
      if (typeof IMM.updateComposerBusy === "function") IMM.updateComposerBusy();
    }
    var submit = async function (finalTxt) {
      var confirmCid = col.id;
      cleanup();
      showChatLoading(msgsEl, col.s);
      col.s.abortController = { global: true };
      try {
        var r = await fetch("/api/chat/user-confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: confirmCid,
            confirm: finalTxt,
            mode: col.s.selectedMode || IMM.selectedMode || "auto",
            model: col.s.selectedModel || IMM.selectedModel || "deepseek-v4-flash",
          }),
        });
        if (!r.ok) {
          var bt = "";
          try {
            bt = await r.text();
          } catch (e) {}
          addAssistantMarkdown(msgsEl, "确认请求 HTTP " + r.status + " " + String(bt || "").slice(0, 200));
          col.s.abortController = null;
          hideChatLoading(col.s);
          return;
        }
        var j = await r.json();
        if (j && j.run_id) col.s.activeRunId = String(j.run_id || "");
      } catch (err) {
        if (err && err.name === "AbortError") return;
        col.s.abortController = null;
        hideChatLoading(col.s);
        addAssistantMarkdown(msgsEl, "确认请求失败: " + (err && err.message ? err.message : String(err)));
      } finally {
        if (typeof IMM.updateComposerBusy === "function") IMM.updateComposerBusy();
      }
    };
    cancel.onclick = function () {
      void submit("");
    };
    ok.onclick = function () {
      void submit(buildFinal());
    };
  }

  function handleChatSseEvent(ev, streamCid, CM) {
    var packetCid = IMM.normalizeConversationId(ev.conversation_id);
    if (!packetCid) return "";
    var col = CM.byId(packetCid);
    if (!col) return "";
    var msgsEl = col.msgsEl;
    var s = col.s;
    if (ev.type === "conversation") {
      if (ev.mode) s.selectedMode = String(ev.mode).toLowerCase();
      if (ev.model) s.selectedModel = String(ev.model || "");
      if (col.id === CM.activeId && typeof IMM.applyModeModelUi === "function")
        IMM.applyModeModelUi(s.selectedMode, s.selectedModel);
    } else if (ev.type === "run_started") {
      s.activeRunId = String(ev.run_id || "");
      s.abortController = s.abortController || { global: true };
      showChatLoading(msgsEl, s);
    } else if (ev.type === "mode_changed" && ev.mode) {
      s.selectedMode = String(ev.mode).toLowerCase();
      if (col.id === CM.activeId && typeof IMM.applyModeModelUi === "function")
        IMM.applyModeModelUi(s.selectedMode, s.selectedModel);
    } else if (ev.type === "context_layout") {
      col.s.lastContextLayout = ev;
      if (col.id === CM.activeId && typeof IMM.updateImmersiveContextBar === "function")
        IMM.updateImmersiveContextBar();
    } else if (ev.type === "llm_round") {
      col.s.roundReasoningText = "";
    } else if (
      ev.type === "dispatch_title" ||
      ev.type === "llm_request" ||
      ev.type === "llm_response" ||
      ev.type === "llm_done" ||
      ev.type === "tool_start" ||
      ev.type === "tool_progress" ||
      ev.type === "tool_preview_update" ||
      ev.type === "usage" ||
      ev.type === "heartbeat" ||
      ev.type === "global_sse_ready"
    ) {
      
    } else if (ev.type === "tool_end") {
      s.pendingDeltaSeparator = true;
      s.anyToolThisTurn = true;
      if (ev.user_confirm_required) openUserConfirm(ev, col, CM, drainChatSseFromResponse);
      if (ev.todo_list && ev.todo_list_data) {
        var td = ev.todo_list_data;
        renderTodoInColumn(col, {
          items: td.items || [],
          all_done: Array.isArray(td.items) && td.items.every(function (it) {
            return !!it.done;
          }),
        });
      }
    } else if (ev.type === "assistant_delta") {
      if (s.anyToolThisTurn) s.anyToolThisTurn = false;
      appendDelta(col, ev.delta || "");
    } else if (ev.type === "peer_message") {
      addPeerUser(msgsEl, ev.content || "", ev.sender_name || "", ev.sender || "");
    } else if (ev.type === "assistant_markdown") {
      if (s.anyToolThisTurn) s.anyToolThisTurn = false;
      var md = ev.markdown;
      if (typeof md === "string" && md.trim()) {
        var e2 = ensureStreamBubble(col);
        if (e2) {
          if (s.streamAssistantText && !s.streamAssistantText.endsWith("\n")) s.streamAssistantText += "\n";
          s.streamAssistantText += md.trim() + "\n";
          e2.innerHTML = IMM.renderMarkdown(s.streamAssistantText);
          scrollMsgs(msgsEl);
        }
      }
    } else if (ev.type === "assistant") {
      if (s.anyToolThisTurn) s.anyToolThisTurn = false;
      if (!finalizeStream(col, ev.content || "")) addAssistantMarkdown(msgsEl, ev.content || "");
    } else if (ev.type === "done") {
      promoteReasoningToChatIfNeeded(col);
      finalizeStream(col, "");
      s.abortController = null;
      s.activeRunId = "";
      hideChatLoading(s);
      if (typeof IMM.refreshConversationTitle === "function") void IMM.refreshConversationTitle(packetCid);
      if (typeof IMM.updateComposerBusy === "function") IMM.updateComposerBusy();
    } else if (ev.type === "stopped") {
      resetTurnState(col);
      if (ev.message) addAssistantMarkdown(msgsEl, ev.message);
      s.abortController = null;
      s.activeRunId = "";
      hideChatLoading(s);
      if (typeof IMM.updateComposerBusy === "function") IMM.updateComposerBusy();
    } else if (ev.type === "paused_for_user_confirm") {
      hideChatLoading(s);
      return "paused_for_user_confirm";
    } else if (ev.type === "todo_list") {
      if (ev.close) {
        var todoHost = msgsEl && msgsEl.parentNode && msgsEl.parentNode.querySelector(".imm-col-todo");
        if (todoHost) todoHost.style.display = "none";
      } else {
        renderTodoInColumn(col, ev);
      }
    } else if (ev.type === "reasoning_delta") {
      appendReasoningToChatIfEmpty(col, ev.delta || "", false);
    } else if (ev.type === "reasoning_sync") {
      appendReasoningToChatIfEmpty(col, ev.text || "", true);
    } else if (ev.type === "error") {
      hideChatLoading(s);
      resetTurnState(col);
      s.abortController = null;
      s.activeRunId = "";
      addAssistantMarkdown(msgsEl, "错误: " + JSON.stringify(ev.detail || ev));
      if (typeof IMM.updateComposerBusy === "function") IMM.updateComposerBusy();
    } else if (ev.type === "inbox_queued") {
      addAssistantMarkdown(msgsEl, "已收到来自 " + (ev.from_name || ev.from || "其他 Agent") + " 的排队消息。");
    } else if (ev.type === "audio") {
      IMM.playAudio(ev.conversation_id, ev.audio);
    }
    return "";
  }

  function startGlobalSse(CM) {
    if (!CM || IMM.globalSseSource) return;
    try {
      var es = new EventSource("/api/events/stream");
      IMM.globalSseSource = es;
      es.onmessage = function (e) {
        if (!e || !e.data) return;
        var ev;
        try {
          ev = JSON.parse(e.data);
        } catch (_err) {
          return;
        }
        handleChatSseEvent(ev, "", CM);
      };
      es.onerror = function () {};
    } catch (_e) {}
  }

  async function drainChatSseFromResponse(r, streamCid, CM) {
    if (!r || !r.body) return;
    var rd = r.body.getReader();
    var de = new TextDecoder();
    var buf = "";
    var endedAwaitingUserConfirm = false;
    for (;;) {
      var x = await rd.read();
      if (x.done) {
        var sseCloseCid = IMM.normalizeConversationId(streamCid || "");
        if (sseCloseCid) {
          var col0 = CM.byId(sseCloseCid);
          if (col0) {
            if (endedAwaitingUserConfirm) {
              hideChatLoading(col0.s);
            } else if (!col0.s.streamAssistantEl) {
              hideChatLoading(col0.s);
            }
          }
        }
        break;
      }
      buf += de.decode(x.value, { stream: true });
      var i0;
      while ((i0 = buf.indexOf("\n\n")) >= 0) {
        var blk = buf.slice(0, i0);
        buf = buf.slice(i0 + 2);
        var lines = blk.split("\n");
        for (var li = 0; li < lines.length; li++) {
          var line = lines[li];
          if (line.indexOf("data:") !== 0) continue;
          var raw = line.slice(5).trim();
          var ev;
          try {
            ev = JSON.parse(raw);
          } catch (je) {
            continue;
          }
          var packetCid = IMM.normalizeConversationId(ev.conversation_id);
          if (!packetCid) continue;
          var col = CM.byId(packetCid);
          if (!col) continue;
          var msgsEl = col.msgsEl;
          var s = col.s;
          if (ev.type === "conversation") {
            if (ev.mode) s.selectedMode = String(ev.mode).toLowerCase();
            if (ev.model) s.selectedModel = String(ev.model || "");
            if (col.id === CM.activeId && typeof IMM.applyModeModelUi === "function")
              IMM.applyModeModelUi(s.selectedMode, s.selectedModel);
          } else if (ev.type === "run_started") {
            s.activeRunId = String(ev.run_id || "");
          } else if (ev.type === "mode_changed" && ev.mode) {
            s.selectedMode = String(ev.mode).toLowerCase();
            if (col.id === CM.activeId && typeof IMM.applyModeModelUi === "function")
              IMM.applyModeModelUi(s.selectedMode, s.selectedModel);
          } else if (ev.type === "usage") {
            /* 用量条在 1.1 可另接；当前仅上下文条 */
          } else if (ev.type === "context_layout") {
            col.s.lastContextLayout = ev;
            if (col.id === CM.activeId && typeof IMM.updateImmersiveContextBar === "function")
              IMM.updateImmersiveContextBar();
          } else if (ev.type === "llm_round") {
            col.s.roundReasoningText = "";
          } else if (
            ev.type === "dispatch_title" ||
            ev.type === "llm_request" ||
            ev.type === "llm_response" ||
            ev.type === "llm_done" ||
            ev.type === "tool_start" ||
            ev.type === "tool_progress" ||
            ev.type === "tool_preview_update"
          ) {
            
            /* 步骤 / LLM 卡片：不渲染 */
          } else if (ev.type === "tool_end") {
            s.pendingDeltaSeparator = true;
            s.anyToolThisTurn = true;
            if (ev.user_confirm_required) openUserConfirm(ev, col, CM, drainChatSseFromResponse);
            if (ev.todo_list && ev.todo_list_data) {
              var td = ev.todo_list_data;
              renderTodoInColumn(col, {
                items: td.items || [],
                all_done: Array.isArray(td.items) && td.items.every(function (it) {
                  return !!it.done;
                }),
              });
            }
          } else if (ev.type === "assistant_delta") {
            if (s.anyToolThisTurn) s.anyToolThisTurn = false;
            appendDelta(col, ev.delta || "");
          } else if (ev.type === "assistant_markdown") {
            if (s.anyToolThisTurn) s.anyToolThisTurn = false;
            var md = ev.markdown;
            if (typeof md === "string" && md.trim()) {
              var e2 = ensureStreamBubble(col);
              if (e2) {
                if (s.streamAssistantText && !s.streamAssistantText.endsWith("\n")) s.streamAssistantText += "\n";
                s.streamAssistantText += md.trim() + "\n";
                e2.innerHTML = IMM.renderMarkdown(s.streamAssistantText);
                scrollMsgs(msgsEl);
              }
            }
          } else if (ev.type === "assistant") {
            if (s.anyToolThisTurn) s.anyToolThisTurn = false;
            if (!finalizeStream(col, ev.content || "")) addAssistantMarkdown(msgsEl, ev.content || "");
          } else if (ev.type === "done") {
            promoteReasoningToChatIfNeeded(col);
            finalizeStream(col, "");
            if (typeof IMM.refreshConversationTitle === "function") void IMM.refreshConversationTitle(packetCid);
          } else if (ev.type === "stopped") {
            resetTurnState(col);
            if (ev.message) addAssistantMarkdown(msgsEl, ev.message);
          } else if (ev.type === "paused_for_user_confirm") {
            hideChatLoading(s);
            endedAwaitingUserConfirm = true;
          } else if (ev.type === "todo_list") {
            if (ev.close) {
              var todoHost = msgsEl && msgsEl.parentNode && msgsEl.parentNode.querySelector(".imm-col-todo");
              if (todoHost) todoHost.style.display = "none";
            } else {
              renderTodoInColumn(col, ev);
            }
          } else if (ev.type === "reasoning_delta") {
            appendReasoningToChatIfEmpty(col, ev.delta || "", false);
          } else if (ev.type === "reasoning_sync") {
            appendReasoningToChatIfEmpty(col, ev.text || "", true);
          } else if (ev.type === "error") {
            hideChatLoading(s);
            resetTurnState(col);
            addAssistantMarkdown(msgsEl, "错误: " + JSON.stringify(ev.detail || ev));
          } else if (ev.type === "audio") {
            IMM.playAudio(ev.conversation_id, ev.audio);
          }
        }
      }
    }
  }

  IMM.drainChatSseFromResponse = drainChatSseFromResponse;
  IMM.handleChatSseEvent = handleChatSseEvent;
  IMM.startGlobalSse = startGlobalSse;
  IMM.immShowChatLoading = showChatLoading;
  IMM.immHideChatLoading = hideChatLoading;
  IMM.immAddUser = addUser;
  IMM.immAddPeerUser = addPeerUser;
  IMM.immAddAssistantMarkdown = addAssistantMarkdown;
  IMM.userConfirmBlocking = false;
  IMM.userConfirmCardHost = null;
})(window);
