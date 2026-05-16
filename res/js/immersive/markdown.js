/* 从经典 agent-ui.js 复制的 Markdown 渲染子集（沉浸模式独立维护） */
(function (w) {
  "use strict";
  w.IMM = w.IMM || {};
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function highlightJson(s) {
    var t = String(s || "");
    try {
      var o = JSON.parse(t);
      t = JSON.stringify(o, null, 2);
    } catch (e) {}
    return t
      .replace(/("(?:\\.|[^"\\])*")\s*:/g, '<span style="color:#ce9178;">$1</span>:')
      .replace(/:\s*("(?:\\.|[^"\\])*")/g, ':<span style="color:#ce9178;">$1</span>')
      .replace(/:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g, ':<span style="color:#b5cea8;">$1</span>')
      .replace(/:\s*(true|false|null)/g, ':<span style="color:#569cd6;">$1</span>');
  }
  function highlightCode(s, lang) {
    var t = String(s || "");
    if (String(lang || "").toLowerCase() === "json" || (!lang && /^\s*[\[{]/.test(t))) return highlightJson(t);
    var langMap = {
      py: "python",
      js: "javascript",
      ts: "typescript",
      html: "html",
      css: "css",
      sh: "bash",
      bash: "bash",
      shell: "bash",
      json: "json",
      yaml: "yaml",
      yml: "yaml",
      md: "markdown",
      xml: "xml",
      sql: "sql",
      c: "c",
      cpp: "cpp",
      java: "java",
      go: "go",
      rust: "rust",
      diff: "diff",
      patch: "diff",
    };
    var langNorm = langMap[String(lang || "").toLowerCase()] || "";
    if (langNorm === "diff") {
      var lines = t.split("\n");
      return lines
        .map(function (line) {
          var e = escapeHtml(line);
          if (/^---/.test(line)) return '<span style="color:#808080;">' + e + "</span>";
          if (/^\+\+\+/.test(line)) return '<span style="color:#808080;">' + e + "</span>";
          if (/^@@/.test(line)) return '<span style="color:#569cd6;">' + e + "</span>";
          if (/^\+/.test(line)) return '<span style="color:#6a9955;">' + e + "</span>";
          if (/^-/.test(line)) return '<span style="color:#f14c4c;">' + e + "</span>";
          return e;
        })
        .join("\n");
    }
    return escapeHtml(t);
  }
  function renderInlineMarkdown(s) {
    var t = escapeHtml(s || "");
    t = t.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, function (_, alt, url) {
      if (/\.(mp4|mov|webm|avi)(\?|$)/i.test(url)) {
        return '<video src="' + url + '" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;"></video>';
      }
      return '<img src="' + url + '" alt="' + alt + '" loading="lazy" />';
    });
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, label, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    t = t.replace(/(^|[\s>]|[([{])_([a-zA-Z0-9]+)_([\s<.,!?;:)\]}>-]|$)/g, "$1<em>$2</em>$3");
    return t;
  }
  function closeOpenLists(state) {
    var html = "";
    while (state.stack.length) {
      html += "</" + state.stack.pop() + ">";
    }
    return html;
  }
  function ensureListLevel(state, targetTag, targetDepth) {
    var html = "";
    while (state.stack.length > targetDepth) {
      html += "</" + state.stack.pop() + ">";
    }
    while (state.stack.length < targetDepth) {
      state.stack.push(targetTag);
      html += "<" + targetTag + ">";
    }
    if (state.stack.length && state.stack[state.stack.length - 1] !== targetTag) {
      html += "</" + state.stack.pop() + ">";
      state.stack.push(targetTag);
      html += "<" + targetTag + ">";
    }
    return html;
  }
  function parseTable(lines, i) {
    if (i + 1 >= lines.length) return null;
    var head = lines[i];
    var sep = lines[i + 1];
    if (head.indexOf("|") < 0 || sep.indexOf("|") < 0) return null;
    var sepClean = sep.trim().replace(/^\||\|$/g, "");
    var cols = sepClean.split("|").map(function (x) {
      return x.trim();
    });
    if (
      !cols.length ||
      !cols.every(function (c) {
        return /^:?-{1,}:?$/.test(c);
      })
    ) {
      return null;
    }
    var headers = head
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map(function (x) {
        return x.trim();
      });
    var j = i + 2;
    var rows = [];
    while (j < lines.length && lines[j].indexOf("|") >= 0 && lines[j].trim() !== "") {
      rows.push(
        lines[j]
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map(function (x) {
            return x.trim();
          })
      );
      j++;
    }
    var html = "<table><thead><tr>";
    for (var k = 0; k < headers.length; k++) {
      html += "<th>" + renderInlineMarkdown(headers[k] || "") + "</th>";
    }
    html += "</tr></thead>";
    if (rows.length) {
      html += "<tbody>";
      for (var r = 0; r < rows.length; r++) {
        html += "<tr>";
        for (var k2 = 0; k2 < headers.length; k2++) {
          html += "<td>" + renderInlineMarkdown((rows[r] && rows[r][k2]) || "") + "</td>";
        }
        html += "</tr>";
      }
      html += "</tbody>";
    }
    html += "</table>";
    return { html: html, next: j };
  }
  function renderMarkdownBlocks(seg) {
    var lines = String(seg || "").replace(/\r/g, "").split("\n");
    var html = "";
    var listState = { stack: [] };
    var quoteDepth = 0;
    function closeQuotes(target) {
      var out = "";
      while (quoteDepth > target) {
        out += "</blockquote>";
        quoteDepth--;
      }
      while (quoteDepth < target) {
        out += "<blockquote>";
        quoteDepth++;
      }
      return out;
    }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line.trim()) {
        html += closeOpenLists(listState);
        html += closeQuotes(0);
        continue;
      }
      var quote = 0;
      while (/^\s*>/.test(line)) {
        line = line.replace(/^\s*>\s?/, "");
        quote++;
      }
      html += closeQuotes(quote);
      if (/^\s*([-*_])\s*\1\s*\1[-*_\s]*$/.test(line)) {
        html += closeOpenLists(listState);
        html += "<hr/>";
        continue;
      }
      var table = parseTable(lines, i);
      if (table) {
        html += closeOpenLists(listState);
        html += table.html;
        i = table.next - 1;
        continue;
      }
      var m = line.match(/^(#{1,6})\s+(.+)$/);
      if (m) {
        html += closeOpenLists(listState);
        var lv = m[1].length;
        html += "<h" + lv + ">" + renderInlineMarkdown(m[2]) + "</h" + lv + ">";
        continue;
      }
      m = line.match(/^\s*(\d+)\.\s+\[( |x|X)\]\s+(.+)$/);
      if (m) {
        var depth1 = Math.floor((line.match(/^\s*/) || [""])[0].length / 2) + 1;
        html += ensureListLevel(listState, "ol", depth1);
        var checked1 = m[2].toLowerCase() === "x" ? " checked" : "";
        html +=
          '<li class="task-item"><input type="checkbox" disabled' +
          checked1 +
          "/><span>" +
          renderInlineMarkdown(m[3]) +
          "</span></li>";
        continue;
      }
      m = line.match(/^\s*[-*+]\s+\[( |x|X)\]\s+(.+)$/);
      if (m) {
        var depth2 = Math.floor((line.match(/^\s*/) || [""])[0].length / 2) + 1;
        html += ensureListLevel(listState, "ul", depth2);
        var checked2 = m[1].toLowerCase() === "x" ? " checked" : "";
        html +=
          '<li class="task-item"><input type="checkbox" disabled' +
          checked2 +
          "/><span>" +
          renderInlineMarkdown(m[2]) +
          "</span></li>";
        continue;
      }
      m = line.match(/^\s*[-*+]\s+(.+)$/);
      if (m) {
        var depth3 = Math.floor((line.match(/^\s*/) || [""])[0].length / 2) + 1;
        html += ensureListLevel(listState, "ul", depth3);
        html += "<li>" + renderInlineMarkdown(m[1]) + "</li>";
        continue;
      }
      m = line.match(/^\s*\d+\.\s+(.+)$/);
      if (m) {
        var depth4 = Math.floor((line.match(/^\s*/) || [""])[0].length / 2) + 1;
        html += ensureListLevel(listState, "ol", depth4);
        html += "<li>" + renderInlineMarkdown(m[1]) + "</li>";
        continue;
      }
      html += closeOpenLists(listState);
      html += "<p>" + renderInlineMarkdown(line) + "</p>";
    }
    html += closeOpenLists(listState);
    html += closeQuotes(0);
    return html;
  }
  function parseUnifiedDiffBodyForRows(body) {
    var Lns = String(body || "").replace(/\r/g, "").split("\n");
    var oldL = [],
      newL = [];
    for (var i = 0; i < Lns.length; i++) {
      var L = Lns[i];
      if (/^---/.test(L) || /^\+\+\+/.test(L)) continue;
      if (/^@@/.test(L)) continue;
      if (/^\s*#/.test(L)) continue;
      if (/^-/.test(L) && !/^---/.test(L)) oldL.push(L.slice(1));
      else if (/^\+/.test(L) && !/^\+\+\+/.test(L)) newL.push(L.slice(1));
    }
    return { oldT: oldL.join("\n"), newT: newL.join("\n") };
  }
  function baseNameOnly(p) {
    if (!p || typeof p !== "string") return "";
    var parts = p.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || p;
  }
  function diffFileNameFromBody(body) {
    var Lns = String(body || "").replace(/\r/g, "").split("\n");
    for (var i = 0; i < Lns.length; i++) {
      var L = Lns[i];
      var m = /^---\s+(.+?)(?:\s+·|\s*$)/.exec(L);
      if (m) return baseNameOnly(m[1].trim());
      m = /^\+\+\+\s+(.+?)(?:\s+·|\s*$)/.exec(L);
      if (m) return baseNameOnly(m[1].trim());
    }
    return "文件";
  }
  function linesDiff(oldT, newT) {
    var A = String(oldT || "").split("\n");
    var B = String(newT || "").split("\n");
    var out = [];
    var i = 0,
      j = 0;
    while (i < A.length || j < B.length) {
      if (i < A.length && j < B.length && A[i] === B[j]) {
        out.push({ t: " ", l: A[i] });
        i++;
        j++;
      } else if (j < B.length && (i >= A.length || A[i] !== B[j])) {
        out.push({ t: "+", l: B[j] });
        j++;
      } else if (i < A.length) {
        out.push({ t: "-", l: A[i] });
        i++;
      } else {
        out.push({ t: "+", l: B[j] });
        j++;
      }
    }
    return out;
  }
  function calcDiffStats(oldT, newT) {
    var A = String(oldT || "").split("\n"),
      B = String(newT || "").split("\n");
    var a = 0,
      d = 0,
      i = 0,
      j = 0;
    while (i < A.length || j < B.length) {
      if (i < A.length && j < B.length && A[i] === B[j]) {
        i++;
        j++;
      } else if (j < B.length && (i >= A.length || A[i] !== B[j])) {
        a++;
        j++;
      } else if (i < A.length) {
        d++;
        i++;
      } else {
        a++;
        j++;
      }
    }
    return { add: a, del: d };
  }
  function buildDiffRowsHtml(oldT, newT) {
    var rows = linesDiff(oldT, newT);
    var html = "";
    for (var ri = 0; ri < rows.length; ri++) {
      var r = rows[ri];
      var cls = r.t === "-" ? "d-del" : r.t === "+" ? "d-add" : "d-eq";
      html += '<div class="' + cls + '">' + escapeHtml(r.t + " " + r.l) + "</div>";
    }
    return html;
  }
  function buildChatDiffCardHtml(oldT, newT, nameHint) {
    var st = calcDiffStats(oldT, newT);
    var fn = String(nameHint || "").trim() || "文件";
    var cap = '<div class="chat-diff-cap">' + escapeHtml(fn) + " · diff";
    if (st.del > 0) cap += ' <span class="chat-diff-neg">-' + st.del + "</span>";
    if (st.add > 0) cap += ' <span class="chat-diff-pos">+' + st.add + "</span>";
    cap += "</div>";
    var box = '<div class="diff-unified">' + buildDiffRowsHtml(oldT, newT) + "</div>";
    return '<div class="chat-diff-card">' + cap + box + "</div>";
  }
  function renderMarkdown(md) {
    var text = String(md || "").replace(/\r\n/g, "\n");
    var re = /```([^\n`]*)\n([\s\S]*?)```/g;
    var html = "";
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      html += renderMarkdownBlocks(text.slice(last, m.index));
      var lang = String(m[1] || "").trim();
      var inner = m[2];
      var langLow = lang.toLowerCase();
      if ((langLow === "diff" || langLow === "patch") && inner) {
        var pr = parseUnifiedDiffBodyForRows(inner);
        if (pr.oldT !== "" || pr.newT !== "") {
          html += buildChatDiffCardHtml(pr.oldT, pr.newT, diffFileNameFromBody(inner));
        } else {
          html +=
            "<pre><code" +
            (lang ? ' data-lang="' + escapeHtml(lang) + '"' : "") +
            ">" +
            highlightCode(inner, lang) +
            "</code></pre>";
        }
      } else {
        html +=
          "<pre><code" +
          (lang ? ' data-lang="' + escapeHtml(lang) + '"' : "") +
          ">" +
          highlightCode(inner, lang) +
          "</code></pre>";
      }
      last = re.lastIndex;
    }
    html += renderMarkdownBlocks(text.slice(last));
    return html || "<p></p>";
  }
  w.IMM.escapeHtml = escapeHtml;
  w.IMM.renderMarkdown = renderMarkdown;
})(window);
