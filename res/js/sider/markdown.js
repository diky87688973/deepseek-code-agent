/* 从经典 agent-ui.js 复制的 Markdown 渲染子集（沉浸模式独立维护） */
(function (w) {
  "use strict";
  w.IMM = w.IMM || {};
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function highlightJson(s) {
    if (w.codeHighlight) return w.codeHighlight.highlightCode(s, "json");
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
    if (w.codeHighlight) return w.codeHighlight.highlightCode(s, lang);
    var t = String(s || "");
    if (String(lang || "").toLowerCase() === "json" || (!lang && /^\s*[\[{]/.test(t))) return highlightJson(t);
    return escapeHtml(t);
  }
  function hljsCodeClass(lang) {
    return w.codeHighlight ? w.codeHighlight.hljsCodeClass(lang) : "";
  }
  function renderInlineMarkdown(s) {
    var t = escapeHtml(s || "");
    // 裸 Windows 路径 D:\...\workspace\... → 转 /kling-tasks/ 或 /ws/
    t = t.replace(/!\[([^\]]*)\]\(([a-zA-Z]:[\/\\]AI_DATA_ROOT[\/\\]workspace[\/\\]([^\s)]+))\)/g, function (_, alt, raw, p) {
      var localPath = raw.replace(/\//g, '\\');
      if (/\.(mp4|mov|webm|avi)(\?|$)/i.test(p)) {
        return '<video src="/workspace/' + p + '" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;" title="' + localPath + '" alt="' + localPath + '"></video>';
      }
      return '<img src="/workspace/' + p + '" alt="' + localPath + '" loading="lazy" style="max-width:300px;height:auto" title="' + localPath + '" />';
    });
    // file:/// 工作区路径 → 转 /kling-tasks/ 或 /ws/ 静态路由
    t = t.replace(/!\[([^\]]*)\]\(file:\/\/\/[a-zA-Z]:[\/\\]AI_DATA_ROOT[\/\\]workspace[\/\\]([^\s)]+)\)/g, function (_, alt, p) {
      var localPath = 'D:\\AI_DATA_ROOT\\workspace\\' + p.replace(/\//g, '\\');
      if (/\.(mp4|mov|webm|avi)(\?|$)/i.test(p)) {
        return '<video src="/workspace/' + p + '" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;" title="' + localPath + '" alt="' + localPath + '"></video>';
      }
      return '<img src="/workspace/' + p + '" alt="' + localPath + '" loading="lazy" style="max-width:300px;height:auto" title="' + localPath + '" />';
    });
    t = t.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+|\/[^\s)]+|[a-zA-Z]:[\/\\][^\s)]+)\)/g, function (_, alt, url) {
      if (/\.(mp4|mov|webm|avi)(\?|$)/i.test(url)) {
        return '<video src="' + url + '" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;"></video>';
      }
      return '<img src="' + url + '" alt="' + alt + '" loading="lazy" style="max-width:300px;height:auto" />';
    });
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, label, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });
    t = t.replace(/(`+?)([\s\S]*?)\1(?!`)/g, "<code>$2</code>");
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
        // 空行不关闭列表，保持 ol/ul 连续性
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
    // Unified diff block: keep hunks/blank lines together, render as diff card
    if (shouldStartUnifiedDiffBlock(lines, i)) {
      var _ud = collectUnifiedDiffBlock(lines, i);
      if (looksLikeUnifiedDiffBlock(_ud.lines)) {
        html += closeOpenLists(listState);
        html += renderUnifiedDiffBlockHtml(_ud.lines);
        i = _ud.next - 1;
        continue;
      }
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
  function isUnifiedDiffLine(line) {
    var s = String(line || "");
    if (!s) return false;
    if (/^---\s/.test(s) || /^\+\+\+\s/.test(s) || /^@@\s/.test(s)) return true;
    if (/^[-+]/.test(s)) return true;
    return false;
  }
  function shouldStartUnifiedDiffBlock(lines, i) {
    if (i >= lines.length || !isUnifiedDiffLine(lines[i])) return false;
    if (/^---\s/.test(lines[i]) || /^@@\s/.test(lines[i])) return true;
    if (i + 1 >= lines.length) return /^[-+]/.test(lines[i]);
    return isUnifiedDiffLine(lines[i + 1]);
  }
  function collectUnifiedDiffBlock(lines, start) {
    var block = [],
      i = start;
    while (i < lines.length) {
      var line = lines[i];
      if (isUnifiedDiffLine(line)) {
        block.push(line);
        i++;
        continue;
      }
      if (!String(line).trim() && i + 1 < lines.length && isUnifiedDiffLine(lines[i + 1])) {
        block.push(line);
        i++;
        continue;
      }
      break;
    }
    return { lines: block, next: i };
  }
  function looksLikeUnifiedDiffBlock(block) {
    if (!block || block.length < 2) return false;
    var hasMarker = false,
      hasChange = false;
    for (var j = 0; j < block.length; j++) {
      var L = block[j];
      if (/^---\s/.test(L) || /^\+\+\+\s/.test(L) || /^@@\s/.test(L)) hasMarker = true;
      if ((/^-/.test(L) && !/^---/.test(L)) || (/^\+/.test(L) && !/^\+\+\+/.test(L))) hasChange = true;
    }
    return hasMarker;
  }
  function renderUnifiedDiffBlockHtml(block) {
    var body = block.join("\n");
    var cards = renderUnifiedDiffBodyAsCardsHtml(body);
    if (cards) return cards;
    return "<pre><code>" + escapeHtml(body) + "</code></pre>";
  }
  function findDiffFenceCloseIndex(rest) {
    var reLine = /^[ \t]*```[ \t]*$/gm, m;
    while ((m = reLine.exec(rest)) !== null) {
      var after = rest.slice(m.index + m[0].length);
      var trimmed = after.replace(/^\r?\n/, "");
      if (!trimmed.trim()) return m.index;
      if (/^```(?:diff|patch)\b/i.test(trimmed)) return m.index;
      var lines = trimmed.split(/\r?\n/);
      var line0 = lines[0] || "";
      var firstLine = line0.trim();
      if (/^---\s/.test(firstLine) || /^\+\+\+\s/.test(firstLine) || /^@@\s/.test(firstLine)) continue;
      if (/^[-+]/.test(firstLine)) {
        for (var di = 1; di < Math.min(lines.length, 4); di++) {
          var l = (lines[di] || "").trim();
          if (!l) break;
          if (l.charAt(0) === "-" || l.charAt(0) === "+") continue;
          return m.index;
        }
        continue;
      }
      return m.index;
    }
    return -1;
  }
  function splitUnifiedDiffSections(body) {
    var lines = String(body || "").replace(/\r/g, "").split("\n");
    var sections = [],
      cur = [];
    for (var i = 0; i < lines.length; i++) {
      var L = lines[i];
      if (/^---\s/.test(L) && cur.some(function (x) {
        return /^---\s/.test(x);
      })) {
        sections.push(cur.join("\n"));
        cur = [L];
        continue;
      }
      cur.push(L);
    }
    if (cur.length) sections.push(cur.join("\n"));
    return sections.filter(function (s) {
      return String(s).trim();
    });
  }
  function renderUnifiedDiffBodyAsCardsHtml(body) {
    // 快速检查：没有任何 diff 标记行的内容不渲染为 diff 卡片
    if (!/^---\s/m.test(body) && !/^\+\+\+\s/m.test(body) && !/^@@\s/m.test(body)) return "";
    var sections = splitUnifiedDiffSections(body);
    if (!sections.length) return "";
    var html = "";
    for (var si = 0; si < sections.length; si++) {
      var sec = sections[si];
      var lines = sec.replace(/\r/g, "").split("\n");
      var fn = diffFileNameFromBody(sec);
      for (var fi = 0; fi < lines.length; fi++) {
        var L = lines[fi];
        if (L.indexOf("--- ") === 0 || L.indexOf("+++ ") === 0) {
          var mp = L.match(/[ab][\/](.+)/);
          if (mp) { fn = mp[1]; break; }
        }
      }
      var add = 0, del = 0;
      for (var li = 0; li < lines.length; li++) {
        var L = lines[li];
        if (L.indexOf("--- ") === 0 || L.indexOf("+++ ") === 0) continue;
        var c = L.charAt(0);
        if (c === "+") add++; else if (c === "-") del++;
      }
      var cap = '<div class="chat-diff-cap">' + escapeHtml(fn) + ' · diff' + (del > 0 ? ' <span class="chat-diff-neg">-' + del + '</span>' : '') + (add > 0 ? ' <span class="chat-diff-pos">+' + add + '</span>' : '') + '</div>';
      var box = '<div class="diff-unified diff-surface-adaptive">';
      for (var li = 0; li < lines.length; li++) {
        var L = lines[li];
        var ch0 = L.charAt(0);
        if (ch0 === "-" || ch0 === "+") {
          if (L.indexOf("--- ") === 0 || L.indexOf("+++ ") === 0) continue;
          box += '<div class="' + (ch0 === "-" ? "d-del" : "d-add") + '">' + diffRowInnerHtml({ t: ch0, l: L.substring(1) }) + '</div>';
        } else if (/^@@/.test(L)) {
          box += '<div class="d-meta">' + escapeHtml(L) + '</div>';
        } else if (ch0 === " ") {
          box += '<div class="d-eq">' + diffRowInnerHtml({ t: " ", l: L.replace(/^\s/, "") }) + '</div>';
        } else if (L.trim()) {
          box += '<div class="d-eq">' + diffRowInnerHtml({ t: " ", l: L }) + '</div>';
        }
      }
      box += '</div>';
      html += '<div class="chat-diff-card">' + cap + box + '</div>';
    }
    return html;
  }
  function iterMarkdownCodeFences(text, visit) {
    var src = String(text || "").replace(/\r\n/g, "\n");
    var i = 0;
    while (i < src.length) {
      var open = src.indexOf("```", i);
      if (open < 0) { visit("text", src.slice(i)); break; }
      // 只认行首的 ```，行内的 ``` 当作普通文本跳过
      var lineStart = src.lastIndexOf("\n", open) + 1;
      if (open > lineStart) { visit("text", src.slice(i, open + 3)); i = open + 3; continue; }
      if (open > i) visit("text", src.slice(i, open));
      var langEnd = src.indexOf("\n", open + 3);
      if (langEnd < 0) {
        visit("text", src.slice(open));
        break;
      }
      var lang = src.slice(open + 3, langEnd);
      var langLow = lang.trim().toLowerCase();
      var pos = langEnd + 1;
      var closed = false;
      if (langLow === "diff" || langLow === "patch") {
        var rest = src.slice(pos);
        var closeAt = findDiffFenceCloseIndex(rest);
        if (closeAt >= 0) {
          var closeMatch = rest.slice(closeAt).match(/^[ \t]*```/);
          visit("fence", lang, rest.slice(0, closeAt).replace(/\n$/, ""));
          i = pos + closeAt + closeMatch[0].length;
          var nl = src.indexOf("\n", i);
          i = nl < 0 ? src.length : nl + 1;
          closed = true;
        }
      } else {
        while (pos < src.length) {
          var idx = src.indexOf("```", pos);
          if (idx < 0) break;
          var lineStart = src.lastIndexOf("\n", idx - 1) + 1;
          if (lineStart !== idx) {
            pos = idx + 3;
            continue;
          }
          var lineEnd = src.indexOf("\n", idx);
          if (lineEnd < 0) lineEnd = src.length;
          var line = src.slice(lineStart, lineEnd);
          if (/^[ \t]*```[ \t]*$/.test(line)) {
            visit("fence", lang, src.slice(langEnd + 1, lineStart).replace(/\n$/, ""));
            i = lineEnd < src.length ? lineEnd + 1 : src.length;
            closed = true;
            break;
          }
          pos = idx + 3;
        }
      }
      if (!closed) {
        visit("text", src.slice(open));
        break;
      }
    }
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
      else if (/^ /.test(L)) {
        var c = L.slice(1);
        oldL.push(c);
        newL.push(c);
      }
    }
    return { oldT: oldL.join("\n"), newT: newL.join("\n") };
  }
  function baseNameOnly(p) {
    if (!p || typeof p !== "string") return "";
    var parts = p.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || p;
  }
  function isDevNullPath(p) {
    var s = String(p || "")
      .trim()
      .replace(/\\/g, "/")
      .toLowerCase();
    if (!s || s === "null") return true;
    return s === "/dev/null" || s === "dev/null" || s.endsWith("/dev/null");
  }
  function parseDiffHeaderPath(line) {
    var m = /^(---|\+\+\+)\s+(.+)$/.exec(String(line || "").trim());
    if (!m) return "";
    return m[2].split("\t")[0].trim();
  }
  function diffFileNameFromBody(body) {
    var Lns = String(body || "").replace(/\r/g, "").split("\n");
    var fromRaw = "",
      toRaw = "";
    for (var i = 0; i < Lns.length; i++) {
      var L = Lns[i];
      if (!fromRaw && L.indexOf("--- ") === 0) fromRaw = parseDiffHeaderPath(L);
      if (!toRaw && L.indexOf("+++ ") === 0) toRaw = parseDiffHeaderPath(L);
    }
    if (toRaw && !isDevNullPath(toRaw)) return baseNameOnly(toRaw);
    if (fromRaw && !isDevNullPath(fromRaw)) return baseNameOnly(fromRaw);
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
  function diffRowInnerHtml(r) {
    var g = r.t === "-" ? "−" : r.t === "+" ? "+" : " ";
    var gs = r.t === "-" ? "d-gutter-del" : r.t === "+" ? "d-gutter-add" : "d-gutter-eq";
    return (
      '<span class="d-gutter ' +
      gs +
      '">' +
      escapeHtml(g) +
      '</span><span class="d-code">' +
      escapeHtml(r.l) +
      "</span>"
    );
  }
  function buildDiffRowsHtml(oldT, newT) {
    var rows = linesDiff(oldT, newT);
    var html = "";
    for (var ri = 0; ri < rows.length; ri++) {
      var r = rows[ri];
      var cls = r.t === "-" ? "d-del" : r.t === "+" ? "d-add" : "d-eq";
      html += '<div class="' + cls + '">' + diffRowInnerHtml(r) + "</div>";
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
    var box = '<div class="diff-unified diff-surface-adaptive">' + buildDiffRowsHtml(oldT, newT) + "</div>";
    return '<div class="chat-diff-card">' + cap + box + "</div>";
}
function renderSimpleDiffCard(inner,lang){
var lines=String(inner||'').split('\n');
var add=0,del=0;
for(var _i=0;_i<lines.length;_i++){var _c=lines[_i].charAt(0);if(_c==='+')add++;else if(_c==='-')del++;}
var cap='<div class="chat-diff-cap">'+escapeHtml(lang||'diff')+(del?' <span class="chat-diff-neg">-'+del+'</span>':'')+(add?' <span class="chat-diff-pos">+'+add+'</span>':'')+'</div>';
var box='<div class="diff-unified diff-surface-adaptive">';
for(var _i=0;_i<lines.length;_i++){var L=lines[_i];var ch0=L.charAt(0);
if(ch0==='-'){box+='<div class="d-del">'+diffRowInnerHtml({t:'-',l:L.substring(1)})+'</div>';}
else if(ch0==='+'){box+='<div class="d-add">'+diffRowInnerHtml({t:'+',l:L.substring(1)})+'</div>';}
else if(ch0===' '){box+='<div class="d-eq">'+diffRowInnerHtml({t:' ',l:L.replace(/^ /,'')})+'</div>';}
else if(L.trim()){box+='<div class="d-eq">'+diffRowInnerHtml({t:' ',l:L})+'</div>';}}
box+='</div>';
return '<div class="chat-diff-card">'+cap+box+'</div>';
}
  function renderMarkdown(md) {
    var text = String(md || "").replace(/\r\n/g, "\n");
    var html = "";
    iterMarkdownCodeFences(text, function (kind, a, b) {
      if (kind === "text") {
        html += renderMarkdownBlocks(a);
        return;
      }
      var lang = String(a || "").trim();
      var inner = b;
      var langLow = lang.toLowerCase();
      if ((langLow === "diff" || langLow === "patch") && inner) {
        var cards = renderUnifiedDiffBodyAsCardsHtml(inner);
        if (cards) {
          html += cards;
        } else {
          html += renderSimpleDiffCard(inner, lang);
        }
      } else {
        var _hc2 = hljsCodeClass(lang);
        html +=
          "<pre><code" +
          (_hc2 ? ' class="' + escapeHtml(_hc2) + '"' : "") +
          (lang ? ' data-lang="' + escapeHtml(lang) + '"' : "") +
          ">" +
          highlightCode(inner, lang) +
          "</code></pre>";
      }
    });
    return html || "<p></p>";
  }
  w.IMM.escapeHtml = escapeHtml;
  w.IMM.renderMarkdown = renderMarkdown;
})(window);
