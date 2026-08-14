/* 沉浸模式：会话 ID 规范化（与经典 agent-ui 一致） */
(function (w) {
  "use strict";
  w.IMM = w.IMM || {};
  function normalizeConversationId(id) {
    id = String(id || "").trim();
    return /^[A-Za-z0-9_-]{8,128}$/.test(id) ? id : "";
  }
  function newConversationId() {
    if (w.crypto && w.crypto.randomUUID) return w.crypto.randomUUID();
    return "xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }
  function readCookieValue(name) {
    var m = document.cookie.match(
      new RegExp("(?:^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]+)")
    );
    return m ? decodeURIComponent(m[1]) : "";
  }
  function clearCookie(name) {
    document.cookie = name + "=; path=/; max-age=0; SameSite=Lax";
  }
  w.IMM.normalizeConversationId = normalizeConversationId;
  w.IMM.newConversationId = newConversationId;
  w.IMM.readCookieValue = readCookieValue;
  w.IMM.clearCookie = clearCookie;
})(window);
