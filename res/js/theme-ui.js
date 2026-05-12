/* 经典页与沉浸页共用：明暗主题，localStorage codeWebAgent.uiTheme */
(function (w) {
  "use strict";
  var KEY = "codeWebAgent.uiTheme";
  function get() {
    try {
      return localStorage.getItem(KEY) === "light" ? "light" : "dark";
    } catch (e) {
      return "dark";
    }
  }
  function set(t) {
    t = t === "light" ? "light" : "dark";
    try {
      localStorage.setItem(KEY, t);
    } catch (e) {}
    document.documentElement.setAttribute("data-ui-theme", t);
    syncButtons();
  }
  function toggle() {
    set(get() === "light" ? "dark" : "light");
  }
  function apply() {
    document.documentElement.setAttribute("data-ui-theme", get());
    syncButtons();
  }
  function syncButtons() {
    var dark = get() === "dark";
    var label = dark ? "白天模式" : "夜间模式";
    var title = dark ? "切换为白天界面" : "切换为夜间界面";
    ["hdrThemeBtn", "immThemeBtn"].forEach(function (id) {
      var b = document.getElementById(id);
      if (b) {
        b.textContent = label;
        b.title = title;
      }
    });
  }
  w.themeUi = { get: get, set: set, toggle: toggle, apply: apply, syncButtons: syncButtons };
})(window);
