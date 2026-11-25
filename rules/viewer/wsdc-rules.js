/*!
 * WSDC rules viewer and diff
 * https://github.com/andreaskasper/wsdc  (rules/viewer)
 *
 * Plain JavaScript, no build step, no dependencies.
 *
 *   WSDCRules.mount(element, {
 *     base: "https://raw.githubusercontent.com/andreaskasper/wsdc/main/rules/",  // where versions.json lives
 *     url: function (path) { return "..."; },  // optional: turns "html/2025.1A.html" into a URL (overrides base)
 *     version: "2025.1A",          // optional start version (default: the version in force)
 *     compare: "2024.2B",          // optional: start in compare mode against this version
 *     hash: true,                  // keep #v=…&cmp=…&r=… in the address bar (default true)
 *     onRule: function (versionId, ruleId) {}   // optional: called when a rule's "history" button is clicked
 *   });
 *
 * The viewer can also be used without UI:
 *   WSDCRules.load(opts) -> Promise<{versions, current}>
 *   WSDCRules.units(htmlString) -> array of units
 *   WSDCRules.diffUnits(oldUnits, newUnits) -> alignment
 */
(function (global) {
  "use strict";

  // ------------------------------------------------------------ helpers
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "text") e.textContent = attrs[k];
        else if (k === "html") e.innerHTML = attrs[k];
        else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }

  function clean(s) { return (s || "").replace(/\s+/g, " ").trim(); }
  function words(s) { return (s || "").toLowerCase().match(/[\w$%’'+-]+/g) || []; }

  function lcsLen(a, b) {
    if (!a.length || !b.length) return 0;
    var prev = new Array(b.length + 1).fill(0), cur = new Array(b.length + 1).fill(0);
    for (var i = 1; i <= a.length; i++) {
      for (var j = 1; j <= b.length; j++) {
        cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
      }
      var t = prev; prev = cur; cur = t;
    }
    return prev[b.length];
  }

  function similarity(a, b) {
    if (a === b) return 1;
    var wa = words(a), wb = words(b);
    if (!wa.length || !wb.length) return 0;
    if (wa.length * wb.length > 250000) { // very long units: fall back to set similarity
      return jaccard(new Set(wa), new Set(wb));
    }
    return 2 * lcsLen(wa, wb) / (wa.length + wb.length);
  }

  function jaccard(sa, sb) {
    if (!sa.size || !sb.size) return 0;
    var inter = 0;
    sa.forEach(function (w) { if (sb.has(w)) inter++; });
    return inter / (sa.size + sb.size - inter);
  }

  // word diff -> [[op, text]] with op "=", "-", "+"
  function wordDiff(a, b) {
    var wa = a.split(/\s+/).filter(Boolean), wb = b.split(/\s+/).filter(Boolean);
    var n = wa.length, m = wb.length;
    if (n * m > 400000) return [["-", a], ["+", b]];
    var dp = [];
    for (var i = 0; i <= n; i++) { dp.push(new Uint16Array(m + 1)); }
    for (i = n - 1; i >= 0; i--) {
      for (var j = m - 1; j >= 0; j--) {
        dp[i][j] = wa[i] === wb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    var out = [];
    function push(op, w) {
      if (out.length && out[out.length - 1][0] === op) out[out.length - 1][1] += " " + w;
      else out.push([op, w]);
    }
    i = 0; j = 0;
    while (i < n && j < m) {
      if (wa[i] === wb[j]) { push("=", wa[i]); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { push("-", wa[i]); i++; }
      else { push("+", wb[j]); j++; }
    }
    while (i < n) push("-", wa[i++]);
    while (j < m) push("+", wb[j++]);
    return out;
  }

  // ------------------------------------------------------------ units
  var SKIP = { OL: 1, UL: 1, TABLE: 1, FIGURE: 1 };

  function ownText(node) {
    var parts = [];
    (function walk(n, top) {
      if (n.nodeType === 3) { parts.push(n.nodeValue); return; }
      if (n.nodeType !== 1) return;
      if (!top && SKIP[n.tagName]) return;
      for (var c = n.firstChild; c; c = c.nextSibling) walk(c, false);
    })(node, true);
    return clean(parts.join(" "));
  }

  function tableText(fig) {
    var cap = fig.querySelector("figcaption");
    var rows = Array.prototype.map.call(fig.querySelectorAll("tr"), function (tr) {
      return Array.prototype.map.call(tr.children, function (c) { return clean(c.textContent); }).join(" | ");
    });
    return (cap ? clean(cap.textContent) + "\n" : "") + rows.join("\n");
  }

  /** Split a rules fragment (string or element) into units with their DOM nodes. */
  function units(src) {
    var root = typeof src === "string" ? parseFragment(src) : src;
    var out = [], stack = [], auto = {};
    function anchor() { return stack.length ? stack[stack.length - 1].k : "top"; }
    function autokey(kind) {
      var a = anchor(), id = a + "|" + kind;
      auto[id] = (auto[id] || 0) + 1;
      return a + "~" + kind + auto[id];
    }
    function path() { return stack.map(function (s) { return s.k; }); }
    function visit(n) {
      if (n.nodeType !== 1) return;
      var tag = n.tagName, cls = n.className || "";
      if (tag === "H2") { out.push({ k: "title", t: "meta", path: [], text: clean(n.textContent), node: n }); return; }
      if (/^H[3-6]$/.test(tag)) {
        var lvl = +tag[1];
        while (stack.length && stack[stack.length - 1].lvl >= lvl) stack.pop();
        var k = n.id || autokey("h");
        out.push({ k: k, t: "heading", lvl: lvl, path: path(), text: clean(n.textContent), node: n });
        stack.push({ lvl: lvl, k: k });
        return;
      }
      if ((tag === "DIV" && /rules-meta/.test(cls)) || tag === "FOOTER") {
        out.push({ k: tag === "FOOTER" ? "footer" : "meta", t: "meta", path: [], text: clean(n.textContent), node: n });
        return;
      }
      if (tag === "P" && /rules-letterhead/.test(cls)) return;
      if (tag === "FIGURE" || tag === "TABLE") {
        var tbl = tag === "TABLE" ? n : n.querySelector("table");
        out.push({ k: n.id || (tbl && tbl.id) || autokey("t"), t: "table", path: path(), text: tableText(n), node: n });
        return;
      }
      if (tag === "LI") {
        var legend = n.parentNode && /rules-legend/.test(n.parentNode.className || "");
        out.push({ k: n.id || autokey("i"), t: legend ? "note" : "item", path: path(),
          label: n.getAttribute("data-list-text") || "", text: ownText(n), node: n });
        for (var c = n.firstElementChild; c; c = c.nextElementSibling) {
          if (SKIP[c.tagName] || c.tagName === "P" || c.tagName === "DIV") visit(c);
        }
        return;
      }
      if (tag === "P") {
        var t = clean(n.textContent);
        if (t) out.push({ k: n.id || autokey("p"), t: "para", path: path(), text: t, node: n });
        return;
      }
      for (var ch = n.firstElementChild; ch; ch = ch.nextElementSibling) visit(ch);
    }
    var sec = root.querySelector ? (root.matches && root.matches("section.wsdc-rules") ? root : root.querySelector("section.wsdc-rules")) : null;
    var start = sec || root;
    for (var c = start.firstElementChild; c; c = c.nextElementSibling) visit(c);
    return out;
  }

  // Parsed in a separate, inert document: images in the fragment are not loaded
  // until the nodes are inserted into the page (paths may not be rewritten yet).
  var inertDoc = null;
  function parseFragment(html) {
    if (!inertDoc) inertDoc = document.implementation.createHTMLDocument("");
    var div = inertDoc.createElement("div");
    div.innerHTML = html;
    return div;
  }

  var KIND = { heading: "h", item: "x", para: "x", note: "n", table: "t", meta: "m" };

  /** Align old and new units. Returns {pairs: [[oldIdx|null, newIdx|null, sim]], byNew: {}, byOld: {}} */
  function diffUnits(oldU, newU) {
    var byNew = {}, byOld = {}, oldByKey = {};
    oldU.forEach(function (u, i) { if (!(u.k in oldByKey)) oldByKey[u.k] = i; });
    newU.forEach(function (u, j) {
      if (u.k.indexOf("~") >= 0) return;
      var i = oldByKey[u.k];
      if (i === undefined || byOld[i] !== undefined || KIND[oldU[i].t] !== KIND[u.t]) return;
      var s = similarity(oldU[i].text, u.text);
      if (s >= 0.35 || (u.t === "heading" && s >= 0.2)) { byNew[j] = [i, s]; byOld[i] = j; }
    });
    var oldSets = oldU.map(function (u) { return new Set(words(u.text)); });
    var cands = [];
    newU.forEach(function (u, j) {
      if (byNew[j]) return;
      var ns = new Set(words(u.text));
      oldU.forEach(function (o, i) {
        if (byOld[i] !== undefined || KIND[o.t] !== KIND[u.t]) return;
        if (jaccard(oldSets[i], ns) < 0.3) return;
        var s = similarity(o.text, u.text);
        if (s >= 0.55) cands.push([s + ((o.path[0] === u.path[0]) ? 0.02 : 0), s, i, j]);
      });
    });
    cands.sort(function (a, b) { return b[0] - a[0]; });
    cands.forEach(function (c) {
      if (byOld[c[2]] !== undefined || byNew[c[3]]) return;
      byNew[c[3]] = [c[2], c[1]]; byOld[c[2]] = c[3];
    });
    return { byNew: byNew, byOld: byOld };
  }

  // ------------------------------------------------------------ data loading
  var cache = {};

  function makeUrl(opts, path) {
    if (opts.url) return opts.url(path);
    return (opts.base || "") + path;
  }

  function fetchText(opts, path) {
    var u = makeUrl(opts, path);
    if (!cache[u]) {
      cache[u] = fetch(u, { credentials: "same-origin" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " for " + path);
        return r.text();
      });
    }
    return cache[u];
  }

  function load(opts) {
    return fetchText(opts, "versions.json").then(function (t) {
      var meta = JSON.parse(t);
      return { versions: meta.versions, current: meta.current, meta: meta };
    });
  }

  function loadHtml(opts, v) {
    return fetchText(opts, v.html).then(function (html) {
      // image paths are relative to rules/html/
      return html.replace(/src="\.\.\/img\/([^"]+)"/g, function (_, f) { return 'src="' + makeUrl(opts, "img/" + f) + '"'; });
    });
  }

  // ------------------------------------------------------------ rendering
  function fmtDate(d) {
    if (!d) return "";
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
    if (!m) return d;
    var months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    return months[+m[2] - 1] + " " + (+m[3]) + ", " + m[1];
  }

  function versionLabel(v, current) {
    var name = v.version || v.id.replace(/-(RPR|RER|ADD)$/, "");
    var doc = { RPR: "Points Registry Rules", RER: "Registry Event Rules", ADD: "Addendum", COMBINED: "" }[v.document] || "";
    var parts = [name];
    if (doc && v.document !== "COMBINED") parts.push("– " + doc);
    if (v.draft) parts.push("(draft)");
    if (v.withdrawn) parts.push("(withdrawn)");
    if (v.id === current) parts.push("(in force)");
    return parts.join(" ");
  }

  function buildToc(content) {
    var heads = content.querySelectorAll("h3[id], h4[id], h5[id], figure[id] > figcaption");
    var ul = el("ul", { class: "wsdc-rv-toc-list" });
    Array.prototype.forEach.call(heads, function (h) {
      var target = h.tagName === "FIGCAPTION" ? h.parentNode : h;
      var lvl = h.tagName === "FIGCAPTION" ? 5 : +h.tagName[1];
      ul.appendChild(el("li", { class: "lvl" + lvl }, [
        el("a", { href: "#" + target.id, text: clean(h.textContent), onclick: function (ev) {
          ev.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          flash(target);
        } })
      ]));
    });
    return ul;
  }

  function flash(node) {
    node.classList.add("wsdc-rv-flash");
    setTimeout(function () { node.classList.remove("wsdc-rv-flash"); }, 1600);
  }

  function diffSpan(ops) {
    var span = el("span", { class: "wsdc-rv-diff" });
    ops.forEach(function (o) {
      if (o[0] === "=") span.appendChild(document.createTextNode(o[1] + " "));
      else span.appendChild(el(o[0] === "+" ? "ins" : "del", { text: o[1] }));
      if (o[0] !== "=") span.appendChild(document.createTextNode(" "));
    });
    return span;
  }

  /** Replace the "own" content of a unit node with a word diff (nested lists/tables stay). */
  function replaceOwn(node, ops) {
    var first = null;
    for (var c = node.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 1 && SKIP[c.tagName]) { first = c; break; }
    }
    var kill = [];
    for (c = node.firstChild; c && c !== first; c = c.nextSibling) kill.push(c);
    kill.forEach(function (k) { node.removeChild(k); });
    node.insertBefore(diffSpan(ops), first);
  }

  function renderCompare(newRoot, oldRoot, newV, oldV) {
    var nu = units(newRoot).filter(function (u) { return u.t !== "meta"; });
    var ou = units(oldRoot).filter(function (u) { return u.t !== "meta"; });
    var al = diffUnits(ou, nu);
    var counts = { same: 0, changed: 0, added: 0, removed: 0, moved: 0 };
    nu.forEach(function (u, j) {
      var m = al.byNew[j];
      if (!m) {
        u.node.classList.add("wsdc-rv-added");
        u.node.setAttribute("data-wsdc-status", "added");
        counts.added++;
        return;
      }
      var o = ou[m[0]];
      if (o.k !== u.k) { u.node.setAttribute("data-wsdc-was", o.k); counts.moved++; }
      if (o.text === u.text) {
        u.node.setAttribute("data-wsdc-status", "same");
        counts.same++;
        return;
      }
      counts.changed++;
      u.node.classList.add("wsdc-rv-changed");
      u.node.setAttribute("data-wsdc-status", "changed");
      if (u.t === "table") {
        u.node.appendChild(el("details", { class: "wsdc-rv-tablediff" }, [
          el("summary", { text: "Show the changes in this table" }),
          el("pre", {}, [diffSpan(wordDiff(o.text, u.text))])
        ]));
      } else {
        replaceOwn(u.node, wordDiff(o.text, u.text));
      }
    });
    // removed units: insert after the new counterpart of the preceding old unit
    ou.forEach(function (o, i) {
      if (al.byOld[i] !== undefined) return;
      counts.removed++;
      var prevNew = null;
      for (var p = i - 1; p >= 0; p--) { if (al.byOld[p] !== undefined) { prevNew = nu[al.byOld[p]]; break; } }
      var box = el("div", { class: "wsdc-rv-removed", "data-wsdc-status": "removed" }, [
        el("span", { class: "wsdc-rv-removed-label", text: "Removed" + (o.k.indexOf("~") < 0 ? " (" + o.k + ")" : "") }),
        el("del", { text: (o.label ? o.label + " " : "") + o.text })
      ]);
      var anchorNode = prevNew ? prevNew.node : null;
      if (anchorNode && anchorNode.parentNode) {
        if (anchorNode.tagName === "LI") {
          var li = el("li", { class: "wsdc-rv-removed-li", "data-wsdc-status": "removed" }, [box]);
          anchorNode.parentNode.insertBefore(li, anchorNode.nextSibling);
        } else {
          anchorNode.parentNode.insertBefore(box, anchorNode.nextSibling);
        }
      } else {
        newRoot.insertBefore(box, newRoot.firstChild);
      }
    });
    return counts;
  }

  function markOnlyChanges(root, on) {
    root.classList.toggle("wsdc-rv-only-changes", on);
    if (!on) return;
    // keep headings and ancestors of changed nodes visible
    var keep = root.querySelectorAll("[data-wsdc-status=changed],[data-wsdc-status=added],[data-wsdc-status=removed]");
    root.querySelectorAll(".wsdc-rv-keep").forEach(function (n) { n.classList.remove("wsdc-rv-keep"); });
    Array.prototype.forEach.call(keep, function (n) {
      for (var p = n; p && p !== root; p = p.parentNode) p.classList && p.classList.add("wsdc-rv-keep");
      // heading of the section
      var h = n;
      while (h && h.previousElementSibling) {
        h = h.previousElementSibling;
        if (/^H[2-6]$/.test(h.tagName)) { h.classList.add("wsdc-rv-keep"); break; }
      }
    });
  }

  function readHash() {
    var o = {};
    (location.hash || "").replace(/^#/, "").split("&").forEach(function (kv) {
      var p = kv.split("=");
      if (p[0] && p.length === 2) o[p[0]] = decodeURIComponent(p[1]);
    });
    return o;
  }

  function writeHash(state) {
    var parts = [];
    if (state.v) parts.push("v=" + encodeURIComponent(state.v));
    if (state.cmp) parts.push("cmp=" + encodeURIComponent(state.cmp));
    if (state.r) parts.push("r=" + encodeURIComponent(state.r));
    var h = "#" + parts.join("&");
    if (history.replaceState) history.replaceState(null, "", h); else location.hash = h;
  }

  // ------------------------------------------------------------ mount
  function mount(target, opts) {
    opts = opts || {};
    if (typeof target === "string") target = document.querySelector(target);
    var useHash = opts.hash !== false;
    var state = { v: opts.version || null, cmp: opts.compare || null, r: null, marks: true, only: false };
    if (useHash) {
      var h = readHash();
      if (h.v) state.v = h.v;
      if (h.cmp) state.cmp = h.cmp;
      if (h.r) state.r = h.r;
    }
    target.classList.add("wsdc-rv");
    target.innerHTML = "";
    var status = el("div", { class: "wsdc-rv-status", text: "Loading the rules…" });
    target.appendChild(status);

    return load(opts).then(function (d) {
      var all = d.versions;
      var withHtml = all.filter(function (v) { return v.html && v.lang === "en"; });
      var byId = {};
      all.forEach(function (v) { byId[v.id] = v; });
      if (!state.v || !byId[state.v] || !byId[state.v].html) state.v = d.current || withHtml[withHtml.length - 1].id;
      if (state.cmp && (!byId[state.cmp] || !byId[state.cmp].html)) state.cmp = null;

      var ordered = withHtml.slice().sort(function (a, b) {
        var ka = (a.effective || a.published || "9999") + (a.published || ""), kb = (b.effective || b.published || "9999") + (b.published || "");
        if (a.draft !== b.draft) return a.draft ? -1 : 1;
        return ka < kb ? 1 : ka > kb ? -1 : 0;
      });

      function options(selected, withNone) {
        var opts2 = [];
        if (withNone) opts2.push(el("option", { value: "", text: "— no comparison —" }));
        var groups = [["Drafts", function (v) { return v.draft; }],
          ["Combined rules (2023 –)", function (v) { return !v.draft && v.document === "COMBINED"; }],
          ["Registry Event Rules (until 2020)", function (v) { return v.document === "RER"; }],
          ["Points Registry Rules (until 2020)", function (v) { return v.document === "RPR"; }],
          ["Addenda", function (v) { return v.document === "ADD"; }]];
        groups.forEach(function (g) {
          var items = ordered.filter(g[1]);
          if (!items.length) return;
          opts2.push(el("optgroup", { label: g[0] }, items.map(function (v) {
            return el("option", { value: v.id, text: versionLabel(v, d.current), selected: v.id === selected ? "selected" : null });
          })));
        });
        return opts2;
      }

      var selV = el("select", { class: "wsdc-rv-select", "aria-label": "Rules version" }, options(state.v, false));
      var selC = el("select", { class: "wsdc-rv-select", "aria-label": "Compare with" }, options(state.cmp || "", true));
      var chkMarks = el("input", { type: "checkbox", checked: "checked" });
      var chkOnly = el("input", { type: "checkbox" });
      var onlyWrap = el("label", { class: "wsdc-rv-check" }, [chkOnly, " Only changes"]);
      var info = el("div", { class: "wsdc-rv-info" });
      var summary = el("div", { class: "wsdc-rv-summary" });
      var toc = el("nav", { class: "wsdc-rv-toc", "aria-label": "Contents" });
      var content = el("div", { class: "wsdc-rv-content" });

      var bar = el("div", { class: "wsdc-rv-bar" }, [
        el("label", { class: "wsdc-rv-field" }, [el("span", { text: "Version" }), selV]),
        el("label", { class: "wsdc-rv-field" }, [el("span", { text: "Compare with" }), selC]),
        el("label", { class: "wsdc-rv-check", title: "Show the WSDC's own yellow/green change highlights" }, [chkMarks, " Highlights"]),
        onlyWrap
      ]);
      target.innerHTML = "";
      target.appendChild(bar);
      target.appendChild(info);
      target.appendChild(summary);
      target.appendChild(el("div", { class: "wsdc-rv-body" }, [toc, content]));

      function renderInfo(v, c) {
        info.innerHTML = "";
        var bits = [];
        if (v.effective) bits.push("effective " + fmtDate(v.effective));
        if (v.published) bits.push("published " + fmtDate(v.published));
        if (v.draft) bits.push("DRAFT – not in force");
        if (v.withdrawn) bits.push("withdrawn – not in force");
        if (v.id === d.current) bits.push("in force");
        info.appendChild(el("p", {}, [el("strong", { text: v.title + (v.version ? " " + v.version : "") }), " · " + bits.join(" · ")]));
        var links = [];
        if (v.pdf) links.push(el("a", { href: makeUrl(opts, v.pdf), target: "_blank", rel: "noopener", text: "Original PDF" + (v.pdf_pages ? " (pages " + v.pdf_pages + ")" : "") }));
        if (v.summary_pdf) links.push(el("a", { href: makeUrl(opts, v.summary_pdf), target: "_blank", rel: "noopener", text: "Summary of changes (PDF)" }));
        var tr = all.filter(function (x) { return x.translation_of === v.id && x.pdf; });
        tr.forEach(function (x) { links.push(el("a", { href: makeUrl(opts, x.pdf), target: "_blank", rel: "noopener", text: "Translation (" + x.lang + ", PDF)" })); });
        if (links.length) {
          var p = el("p", { class: "wsdc-rv-links" });
          links.forEach(function (l, i) { if (i) p.appendChild(document.createTextNode(" · ")); p.appendChild(l); });
          info.appendChild(p);
        }
        if (v.notes) info.appendChild(el("p", { class: "wsdc-rv-note", text: v.notes }));
        if (c) info.appendChild(el("p", { class: "wsdc-rv-note" }, ["Compared with ", el("strong", { text: versionLabel(c, d.current) }),
          ". Inserted words are underlined in green, removed words are struck through in red."]));
      }

      function render() {
        var v = byId[state.v], c = state.cmp ? byId[state.cmp] : null;
        content.innerHTML = "";
        toc.innerHTML = "";
        summary.innerHTML = "";
        content.appendChild(el("div", { class: "wsdc-rv-status", text: "Loading " + versionLabel(v, d.current) + "…" }));
        renderInfo(v, c);
        onlyWrap.style.display = c ? "" : "none";
        var jobs = [loadHtml(opts, v)];
        if (c) jobs.push(loadHtml(opts, c));
        return Promise.all(jobs).then(function (res) {
          var root = parseFragment(res[0]);
          content.innerHTML = "";
          content.appendChild(root);
          if (c) {
            var counts = renderCompare(root, parseFragment(res[1]), v, c);
            summary.appendChild(el("p", {}, [
              el("span", { class: "wsdc-rv-chip changed", text: counts.changed + " changed" }),
              el("span", { class: "wsdc-rv-chip added", text: counts.added + " added" }),
              el("span", { class: "wsdc-rv-chip removed", text: counts.removed + " removed" }),
              el("span", { class: "wsdc-rv-chip", text: counts.moved + " renumbered" }),
              el("span", { class: "wsdc-rv-chip", text: counts.same + " unchanged" })
            ]));
            markOnlyChanges(content, state.only);
          }
          content.classList.toggle("wsdc-rv-nomarks", !state.marks);
          toc.appendChild(el("div", { class: "wsdc-rv-toc-title", text: "Contents" }));
          toc.appendChild(buildToc(content));
          decorate(content, v);
          if (state.r) {
            var t = content.querySelector("[id='" + state.r.replace(/'/g, "") + "']");
            if (t) { t.scrollIntoView({ block: "start" }); flash(t); }
          }
          if (useHash) writeHash(state);
          if (opts.onRender) opts.onRender(state, content);
        }).catch(function (e) {
          content.innerHTML = "";
          content.appendChild(el("div", { class: "wsdc-rv-error", text: "Could not load the rules: " + e.message }));
        });
      }

      // rule anchors: copy link / history
      function decorate(root, v) {
        root.querySelectorAll("[id]").forEach(function (n) {
          if (!/^(H[3-6]|LI|FIGURE)$/.test(n.tagName) || /^(legend|mark)-/.test(n.id)) return;
          var tools = el("span", { class: "wsdc-rv-tools" }, [
            el("a", { href: "#", class: "wsdc-rv-anchor", title: "Link to this rule", text: "#", onclick: function (ev) {
              ev.preventDefault(); ev.stopPropagation();
              state.r = n.id;
              if (useHash) writeHash(state);
              flash(n);
              if (navigator.clipboard) navigator.clipboard.writeText(location.href).catch(function () {});
            } }),
            opts.onRule ? el("a", { href: "#", class: "wsdc-rv-history", title: "History of this rule across all versions", text: "history", onclick: function (ev) {
              ev.preventDefault(); ev.stopPropagation(); opts.onRule(v.id, n.id);
            } }) : null
          ]);
          if (n.tagName === "LI") n.insertBefore(tools, n.firstChild);
          else if (n.tagName === "FIGURE") { var cap = n.querySelector("figcaption"); (cap || n).appendChild(tools); }
          else n.appendChild(tools);
        });
      }

      selV.addEventListener("change", function () { state.v = selV.value; state.r = null; render(); });
      selC.addEventListener("change", function () { state.cmp = selC.value || null; render(); });
      chkMarks.addEventListener("change", function () { state.marks = chkMarks.checked; content.classList.toggle("wsdc-rv-nomarks", !state.marks); });
      chkOnly.addEventListener("change", function () { state.only = chkOnly.checked; markOnlyChanges(content, state.only); });

      var api = {
        show: function (vid, cmp, rule) {
          state.v = vid; state.cmp = cmp || null; state.r = rule || null;
          selV.value = vid; selC.value = cmp || "";
          return render();
        },
        state: state,
        data: d
      };
      render();
      return api;
    }).catch(function (e) {
      status.textContent = "Could not load the rules: " + e.message;
      status.className = "wsdc-rv-error";
    });
  }

  global.WSDCRules = {
    mount: mount,
    load: load,
    loadHtml: loadHtml,
    units: units,
    diffUnits: diffUnits,
    wordDiff: wordDiff,
    similarity: similarity,
    parseFragment: parseFragment,
    versionLabel: versionLabel,
    fmtDate: fmtDate
  };
})(window);
