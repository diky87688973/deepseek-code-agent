(function (w) {
  "use strict";
  var docEl = document.documentElement;
  var btn = document.getElementById("immFsBtn");

  function isElementFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }

  function enter() {
    var p = docEl.requestFullscreen || docEl.webkitRequestFullscreen;
    if (p) return p.call(docEl).catch(function () {});
    return Promise.resolve();
  }

  function exit() {
    var x = document.exitFullscreen || document.webkitExitFullscreen;
    if (x) return x.call(document).catch(function () {});
    return Promise.resolve();
  }

  function toggle() {
    if (isElementFullscreen()) void exit();
    else void enter();
  }

  function syncLabel() {
    if (!btn) return;
    btn.textContent = isElementFullscreen() ? "退出全屏" : "全屏";
  }

  if (btn) {
    btn.addEventListener("click", function () {
      toggle();
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "F11") return;
    e.preventDefault();
    toggle();
  });
  document.addEventListener("fullscreenchange", syncLabel);
  document.addEventListener("webkitfullscreenchange", syncLabel);
  syncLabel();
})(window);
