/* 消息图片点击预览大图（经典 / 沉浸 / sider 共用） */
(function (global) {
  var CWA = global.CWA || (global.CWA = {});

  function attachmentUrl(cid, id) {
    return (
      "/api/chat/attachment?" +
      new URLSearchParams({
        conversation_id: String(cid || "").trim(),
        id: String(id || "").trim(),
      }).toString()
    );
  }

  function ensureLightbox() {
    var el = document.getElementById("cwaImgLightbox");
    if (el) return el;
    el = document.createElement("div");
    el.id = "cwaImgLightbox";
    el.className = "img-lightbox hidden";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.innerHTML =
      '<div class="img-lightbox-backdrop" title="关闭"></div>' +
      '<img class="img-lightbox-img" alt="预览"/>' +
      '<button type="button" class="img-lightbox-close" aria-label="关闭">×</button>';
    document.body.appendChild(el);
    function close() {
      el.classList.add("hidden");
      var img = el.querySelector(".img-lightbox-img");
      if (img) {
        img.removeAttribute("src");
        img.alt = "预览";
      }
    }
    el.querySelector(".img-lightbox-backdrop").addEventListener("click", close);
    el.querySelector(".img-lightbox-close").addEventListener("click", close);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !el.classList.contains("hidden")) close();
    });
    el._cwaClose = close;
    return el;
  }

  function openImageLightbox(url) {
    var src = String(url || "").trim();
    if (!src) return;
    var el = ensureLightbox();
    var img = el.querySelector(".img-lightbox-img");
    img.src = src;
    el.classList.remove("hidden");
  }

  function bindThumbClick(img) {
    if (!img || img._cwaLbBound) return;
    img._cwaLbBound = true;
    img.classList.add("msg-attach-thumb");
    img.title = img.title || "点击预览大图";
    img.style.cursor = "zoom-in";
    img.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openImageLightbox(img.currentSrc || img.src);
    });
  }

  /** 构建消息区附件条：优先服务端 attachments，否则用本地 pending 项 */
  function buildMsgAttachStrip(cid, attachments, localItems) {
    var wrap = document.createElement("div");
    wrap.className = "attach-strip msg-attach-strip";
    var server = Array.isArray(attachments) ? attachments : [];
    var local = Array.isArray(localItems) ? localItems : [];
    var n = Math.max(server.length, local.length);
    for (var i = 0; i < n; i++) {
      var a = server[i];
      var loc = local[i];
      var img = document.createElement("img");
      img.alt = (a && a.name) || (loc && loc.name) || "图片";
      if (a && a.id && cid) {
        img.src = attachmentUrl(cid, a.id);
      } else if (loc) {
        if (loc.data_base64) {
          img.src =
            "data:" +
            (loc.mime || "image/png") +
            ";base64," +
            String(loc.data_base64);
        } else if (loc.previewUrl) {
          img.src = loc.previewUrl;
        }
      }
      if (!img.src) continue;
      bindThumbClick(img);
      wrap.appendChild(img);
    }
    return wrap;
  }

  function appendHadImagesLostTip(mount) {
    if (!mount) return;
    var tip = document.createElement("div");
    tip.className = "attach-lost-tip";
    tip.textContent = "图片预览已失效；若需再看请重新粘贴后发送。";
    mount.appendChild(tip);
  }

  CWA.attachmentUrl = attachmentUrl;
  CWA.openImageLightbox = openImageLightbox;
  CWA.bindThumbClick = bindThumbClick;
  CWA.buildMsgAttachStrip = buildMsgAttachStrip;
  CWA.appendHadImagesLostTip = appendHadImagesLostTip;
  global.openImageLightbox = openImageLightbox;
})(window);
