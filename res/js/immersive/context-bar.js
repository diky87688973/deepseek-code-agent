/* 沉浸：底部「上下文视图」条 + 与经典一致的悬浮说明（线段颜色、tooltip 结构） */
(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});
  var COLORS = {
    system: "#a68b32",
    knowledge: "#0d3a66",
    summary: "#7a1f1f",
    skill: "#9c27b0",
    pure: "#156b4a",
    full_recent: "#124a3a",
    mode: "#455a64",
    remaining: "#4a4e55",
  };

  function formatContextPct(p) {
    var x = Number(p) || 0;
    if (x <= 0) return "0.00";
    var v = Math.ceil(x * 100) / 100;
    return v.toFixed(2);
  }

  function buildCtxLayoutTooltipHtml(segs, colors) {
    var esc = IMM.escapeHtml || function (s) {
      return String(s || "");
    };
    var rows = [];
    for (var i = 0; i < segs.length; i++) {
      if (segs[i]) rows.push(segs[i]);
    }
    var maxTok = 0;
    for (var j = 0; j < rows.length; j++)
      maxTok = Math.max(maxTok, Math.max(0, Math.floor(Number(rows[j].tokens) || 0)));
    if (maxTok < 1) maxTok = 1;
    var trackW = 120;
    var parts = [];
    for (var m = 0; m < rows.length; m++) {
      var sg = rows[m];
      var key = String(sg.key || "");
      var lab = String(sg.label || key);
      var tok = Math.max(0, Math.floor(Number(sg.tokens) || 0));
      var pct = formatContextPct(sg.pct);
      var col = colors[key] || "#5c5c5c";
      var csafe = String(col).replace(/[^#0-9a-fA-F]/g, "").slice(0, 9) || "#5c5c5c";
      var wpx = tok <= 0 ? 0 : Math.max(2, Math.round((trackW * tok) / maxTok));
      var mid = "—";
      if (
        key === "knowledge" ||
        key === "summary" ||
        key === "pure" ||
        key === "full_recent"
      ) {
        var cnt =
          sg.count !== undefined && sg.count !== null
            ? Math.max(0, Math.floor(Number(sg.count) || 0))
            : null;
        if (cnt !== null) mid = String(cnt) + "个";
      }
      if (key === "skill") {
        var cnt =
          sg.count !== undefined && sg.count !== null
            ? Math.max(0, Math.floor(Number(sg.count) || 0))
            : 0;
        var ac =
          sg.auto_load_count !== undefined
            ? Math.max(0, Math.floor(Number(sg.auto_load_count) || 0))
            : 0;
        mid = ac + "/" + cnt;
      }
      var meta = pct + "% | " + mid + " | " + tok.toLocaleString() + " tokens";
      parts.push(
        '<div class="ctx-tip-line">' +
          '<span class="ctx-tip-br">' +
          esc(lab) +
          "</span>" +
          '<span class="ctx-tip-track">' +
          '<span class="ctx-tip-track-fill" style="width:' +
          wpx +
          "px;background-color:" +
          csafe +
          '"></span>' +
          "</span>" +
          '<span class="ctx-tip-rest">' +
          esc(meta) +
          "</span>" +
          "</div>"
      );
    }
    return parts.join("");
  }

  var ctxTipEl = null;
  var ctxTipHideTimer = null;
  var ctxTipInited = false;
  var liveSegs = [];
  var liveColors = COLORS;

  function scheduleHideCtxTip() {
    var tt = ctxTipEl;
    if (!tt) return;
    if (ctxTipHideTimer) clearTimeout(ctxTipHideTimer);
    ctxTipHideTimer = setTimeout(function () {
      ctxTipHideTimer = null;
      tt.style.display = "none";
    }, 160);
  }

  function ensureCtxTip() {
    if (ctxTipEl) return ctxTipEl;
    ctxTipEl = document.createElement("div");
    ctxTipEl.id = "imm-ctx-layout-tooltip";
    ctxTipEl.className = "ctx-layout-tooltip";
    ctxTipEl.style.display = "none";
    ctxTipEl.setAttribute("role", "tooltip");
    document.body.appendChild(ctxTipEl);
    if (!ctxTipInited) {
      ctxTipInited = true;
      ctxTipEl.addEventListener("mouseenter", function () {
        if (ctxTipHideTimer) {
          clearTimeout(ctxTipHideTimer);
          ctxTipHideTimer = null;
        }
      });
      ctxTipEl.addEventListener("mouseleave", function () {
        scheduleHideCtxTip();
      });
    }
    return ctxTipEl;
  }

  function positionCtxTip(tt, anchorEl) {
    if (!tt || !anchorEl) return;
    tt.style.position = "fixed";
    tt.style.transform = "";
    tt.style.bottom = "auto";
    var r = anchorEl.getBoundingClientRect();
    var left = r.left;
    var gap = 10;
    tt.style.left = left + "px";
    tt.style.zIndex = "10050";
    requestAnimationFrame(function () {
      void tt.offsetHeight;
      var h = tt.offsetHeight || 0;
      var top = r.top - h - gap;
      if (top < 8) top = 8;
      tt.style.top = top + "px";
      var vw = document.documentElement.clientWidth || window.innerWidth;
      var tw = tt.offsetWidth || 0;
      if (left + tw > vw - 8) tt.style.left = Math.max(8, vw - tw - 8) + "px";
    });
  }

  function showCtxTip(anchor) {
    if (ctxTipHideTimer) {
      clearTimeout(ctxTipHideTimer);
      ctxTipHideTimer = null;
    }
    if (!liveSegs || !liveSegs.length) {
      if (ctxTipEl) {
        ctxTipEl.style.display = "none";
        ctxTipEl.innerHTML = "";
      }
      return;
    }
    var tt = ensureCtxTip();
    tt.innerHTML = buildCtxLayoutTooltipHtml(liveSegs, liveColors);
    tt.style.display = "block";
    positionCtxTip(tt, anchor);
  }

  function bindCtxBarHost(host) {
    if (host.dataset.immCtxBound === "1") return;
    host.dataset.immCtxBound = "1";
    host.addEventListener("mouseenter", function () {
      showCtxTip(host);
    });
    host.addEventListener("mouseleave", function () {
      scheduleHideCtxTip();
    });
  }

  function renderSegments(host, segs) {
    while (host.firstChild) host.removeChild(host.firstChild);
    if (!segs || !segs.length) {
      var empty = document.createElement("span");
      empty.className = "ctx-bar-empty";
      empty.textContent = "—";
      host.appendChild(empty);
      return;
    }
    var bar = document.createElement("div");
    bar.className = "ctx-bar";
    for (var si = 0; si < segs.length; si++) {
      var sg = segs[si];
      if (!sg) continue;
      var tok = Math.max(0, Math.floor(Number(sg.tokens) || 0));
      var col = COLORS[String(sg.key)] || "#5c5c5c";
      var colEl = document.createElement("span");
      colEl.className = "ctx-seg-col" + (tok > 0 ? "" : " ctx-seg-col-zero");
      if (tok > 0) {
        colEl.style.flex = String(tok) + " 1 0";
        colEl.style.minWidth = "2px";
      } else {
        colEl.style.flex = "0 0 2px";
        colEl.style.minWidth = "2px";
        colEl.style.maxWidth = "2px";
      }
      var fill = document.createElement("span");
      fill.className =
        "ctx-seg ctx-seg-" + String(sg.key || "").replace(/[^a-z0-9_-]/gi, "");
      fill.style.background = col;
      fill.style.height = "10px";
      fill.style.borderRadius = "2px";
      fill.style.flexShrink = "0";
      fill.style.width = "100%";
      fill.style.display = "block";
      fill.style.boxSizing = "border-box";
      colEl.appendChild(fill);
      bar.appendChild(colEl);
    }
    host.appendChild(bar);
  }

  IMM.updateImmersiveContextBar = function () {
    var CM = IMM.CM;
    var host = document.getElementById("immCtxBarHost");
    if (!host || !CM) return;
    if (ctxTipEl) ctxTipEl.style.display = "none";
    if (ctxTipHideTimer) {
      clearTimeout(ctxTipHideTimer);
      ctxTipHideTimer = null;
    }
    var col = CM.getActive();
    var lay = col && col.s && col.s.lastContextLayout;
    var segs = lay && Array.isArray(lay.segments) ? lay.segments : [];
    liveSegs = [];
    for (var i = 0; i < segs.length; i++) {
      if (segs[i]) liveSegs.push(segs[i]);
    }
    liveColors = COLORS;
    renderSegments(host, liveSegs);
    bindCtxBarHost(host);
  };
})(window);
