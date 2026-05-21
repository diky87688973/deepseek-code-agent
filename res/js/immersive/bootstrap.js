(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});

  async function loadHealthModel() {
    try {
      var r = await fetch("/health");
      if (!r.ok) return;
      var j = await r.json();
      var hdr = document.getElementById("immHdrModel");
      if (hdr && j.model) hdr.textContent = "模型: " + j.model;
      if (j.allowed_models && j.allowed_models.length) IMM.allowedModels = j.allowed_models.slice();
    } catch (e) {}
  }

  async function restoreColumns(CM) {
    var localLay = IMM.loadImmersiveLayout && IMM.loadImmersiveLayout();
    var serverState = null;
    try {
      var r = await fetch("/api/chat/ui-state");
      if (r.ok) {
        var j = await r.json();
        serverState = j.state;
      }
    } catch (e) {}

    var tabs = [];
    var activeId = "";
    if (serverState && Array.isArray(serverState.tabs) && serverState.tabs.length) {
      tabs = serverState.tabs.slice(-8);
      activeId = IMM.normalizeConversationId(serverState.active_conversation_id) || "";
    } else if (localLay && Array.isArray(localLay.tabs) && localLay.tabs.length) {
      for (var li = 0; li < localLay.tabs.length; li++) {
        var lt = localLay.tabs[li];
        var lid = IMM.normalizeConversationId(lt && lt.id);
        if (lid) tabs.push({ id: lid, title: String((lt && lt.title) || "") });
      }
      tabs = tabs.slice(-8);
      activeId = IMM.normalizeConversationId(localLay.activeId) || "";
    }

    var sessPrefer = "";
    try {
      sessPrefer = IMM.normalizeConversationId(
        sessionStorage.getItem("codeWebAgent.activeConversationId") || ""
      );
    } catch (e0) {
      sessPrefer = "";
    }

    if (!tabs.length) {
      if (sessPrefer) CM.addColumn(sessPrefer, "");
      else CM.addColumn(IMM.newConversationId(), "");
    } else {
      for (var i = 0; i < tabs.length; i++) {
        var t = tabs[i];
        var id = IMM.normalizeConversationId(t && t.id);
        if (!id) continue;
        CM.addColumn(id, String((t && t.title) || ""));
      }
      if (sessPrefer && !CM.byId(sessPrefer)) CM.addColumn(sessPrefer, "");
      if (
        localLay &&
        Array.isArray(localLay.colWidths) &&
        localLay.colWidths.length === CM.cols.length
      ) {
        CM.colWidths = localLay.colWidths.slice();
        CM._applyFlexWidths();
      }
      var pick =
        sessPrefer && CM.byId(sessPrefer)
          ? sessPrefer
          : activeId && CM.byId(activeId)
            ? activeId
            : CM.cols[0] && CM.cols[0].id;
      if (pick) CM.setActive(pick);
    }
  }

  function scheduleImmersiveHeaderRetract() {
    var slide = document.querySelector(".imm-header-slide");
    if (!slide || slide.getAttribute("data-imm-header-retract") === "1") return;
    slide.setAttribute("data-imm-header-retract", "1");
    w.setTimeout(function () {
      void slide.offsetHeight;
      slide.classList.remove("imm-header-slide-init");
    }, 1000);
  }

  async function boot() {
    if (w.themeUi) w.themeUi.apply();
    var th = document.getElementById("immThemeBtn");
    if (th && w.themeUi)
      th.addEventListener("click", function () {
        w.themeUi.toggle();
      });
    var row = document.getElementById("immColumnsRow");
    if (!row || !IMM.ColumnManager) return;
    var CM = new IMM.ColumnManager(row);
    IMM.CM = CM;
    await restoreColumns(CM);
    await loadHealthModel();
    if (typeof IMM.initComposer === "function") IMM.initComposer(CM);
    if (typeof IMM.startGlobalSse === "function") IMM.startGlobalSse(CM);
    if (typeof IMM.initKbOverlay === "function") IMM.initKbOverlay();
    for (var k = 0; k < CM.cols.length; k++) {
      if (typeof IMM.loadConversationHistory === "function")
        void IMM.loadConversationHistory(CM.cols[k]);
    }
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", function () {
      scheduleImmersiveHeaderRetract();
      void boot();
    });
  else {
    scheduleImmersiveHeaderRetract();
    void boot();
  }
})(window);
