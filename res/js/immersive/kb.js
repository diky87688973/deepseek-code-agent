(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});

  function getCid() {
    var CM = IMM.CM;
    var c = CM && CM.getActive();
    return c ? c.id : "";
  }

  function persistKbChecked(cid) {
    var checked = [];
    var body = document.getElementById("immKbBody");
    if (!body) return;
    body.querySelectorAll(".kb-cb.checked").forEach(function (cb) {
      var p = cb.getAttribute("data-path");
      if (p) checked.push(p);
    });
    fetch("/api/kb/checked", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: cid, checked: checked }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        if (!j || !j.ok || !Array.isArray(j.checked)) return;
        var set = {};
        j.checked.forEach(function (p) {
          set[p] = true;
        });
        var body = document.getElementById("immKbBody");
        if (!body) return;
        body.querySelectorAll(".kb-cb").forEach(function (cb) {
          var p = cb.getAttribute("data-path");
          var row = cb.closest(".kb-file-row");
          if (!row) return;
          if (set[p]) {
            cb.classList.add("checked");
            row.classList.add("checked");
          } else {
            cb.classList.remove("checked");
            row.classList.remove("checked");
          }
        });
      })
      .catch(function () {});
  }

  function renderKbFileList(container, files, checkedSet, cid) {
    var esc = IMM.escapeHtml || function (s) {
      return String(s || "");
    };
    var html =
      '<div class="kb-header"><span class="kb-count">共 ' +
      files.length +
      " 个文件</span></div>" +
      '<div class="kb-hint-bar">💡 勾选的文件将作为当前会话的上下文参考内容</div><div class="kb-list">';
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      var ch = checkedSet[f.path] ? " checked" : "";
      html += '<label class="kb-file-row' + ch + '">';
      html +=
        '<span class="kb-cb' +
        (checkedSet[f.path] ? " checked" : "") +
        '" data-path="' +
        esc(f.path) +
        '"></span>';
      html += '<span class="kb-fpath">' + esc(f.path) + "</span>";
      html += "</label>";
    }
    html += "</div>";
    container.innerHTML = html;
    container.querySelectorAll(".kb-file-row").forEach(function (row) {
      row.addEventListener("click", function () {
        var cb = row.querySelector(".kb-cb");
        if (!cb) return;
        var was = cb.classList.contains("checked");
        if (was) {
          cb.classList.remove("checked");
          row.classList.remove("checked");
        } else {
          cb.classList.add("checked");
          row.classList.add("checked");
        }
        persistKbChecked(cid);
      });
    });
  }

  function positionKbPanel() {
    var overlay = document.getElementById("immKbOverlay");
    var btn = document.getElementById("immKbBtn");
    if (!overlay || !btn) return;
    var rect = btn.getBoundingClientRect();
    overlay.style.paddingLeft = Math.max(8, Math.floor(rect.left)) + "px";
  }

  function openKb() {
    var overlay = document.getElementById("immKbOverlay");
    var body = document.getElementById("immKbBody");
    if (!overlay || !body) return;
    var cid = getCid();
    if (!cid) {
      alert("请先选中一个会话栏。");
      return;
    }
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    positionKbPanel();
    requestAnimationFrame(function () {
      positionKbPanel();
    });
    body.innerHTML = '<div class="kb-loading">📂 加载知识库…</div>';
    fetch("/api/kb/files")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok || !data.enabled) {
          body.innerHTML =
            '<div class="kb-empty">⚠️ 知识库未启用<br><span class="kb-hint">请在 config.json 中配置 KNOWLEDGE_BASE_DIR</span></div>';
          positionKbPanel();
          return;
        }
        if (!data.files || !data.files.length) {
          body.innerHTML = '<div class="kb-empty">📭 知识库目录为空</div>';
          positionKbPanel();
          return;
        }
        fetch("/api/kb/checked?conversation_id=" + encodeURIComponent(cid))
          .then(function (r2) {
            return r2.json();
          })
          .then(function (sd) {
            var cs = {};
            if (sd.ok && sd.checked) sd.checked.forEach(function (p) {
              cs[p] = true;
            });
            renderKbFileList(body, data.files, cs, cid);
          })
          .catch(function () {
            renderKbFileList(body, data.files, {}, cid);
          })
          .finally(function () {
            positionKbPanel();
          });
      })
      .catch(function (err) {
        body.innerHTML =
          '<div class="kb-empty">❌ 加载失败: ' + (err.message || String(err)) + "</div>";
        positionKbPanel();
      });
  }

  function closeKb() {
    var overlay = document.getElementById("immKbOverlay");
    if (!overlay) return;
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
    overlay.style.paddingLeft = "";
  }

  IMM.initKbOverlay = function () {
    var btn = document.getElementById("immKbBtn");
    var close = document.getElementById("immKbClose");
    var overlay = document.getElementById("immKbOverlay");
    if (btn) btn.addEventListener("click", openKb);
    if (close) close.addEventListener("click", closeKb);
    if (overlay) {
      var bd = overlay.querySelector(".imm-kb-backdrop");
      if (bd) bd.addEventListener("click", closeKb);
    }
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (overlay && !overlay.classList.contains("hidden")) closeKb();
    });
    window.addEventListener("resize", function () {
      if (overlay && !overlay.classList.contains("hidden")) positionKbPanel();
    });
  };
})(window);
