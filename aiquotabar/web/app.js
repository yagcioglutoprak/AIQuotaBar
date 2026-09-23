/* AIQuotaBar UI.
 *
 * Python owns all data and decisions; this file only draws what it is given
 * (AIQ.render(state)) and reports clicks back over the native bridge
 * (window.webkit.messageHandlers.aiq). Every string from the outside world
 * goes through textContent — never innerHTML — so provider error messages
 * can't inject markup.
 */
(function () {
  "use strict";

  var BOOT = window.AIQ_BOOT || {};
  var params = new URLSearchParams(location.search);
  var VIEW = BOOT.view || params.get("view") || "panel";
  var NATIVE = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.aiq);
  var root = document.documentElement;
  root.classList.add("view-" + VIEW);
  if (NATIVE) root.classList.add("native");
  if (params.get("theme")) root.setAttribute("data-theme", params.get("theme"));

  var ASSETS = { icons: {} };
  var STATE = null;
  var pendingRender = null;
  var ui = { menuOpen: false, tab: null, range: 30, drafts: {}, cookieOpen: false };
  window.AIQ_OUTBOX = [];

  // ── bridge ────────────────────────────────────────────────────────────────
  function send(action, data) {
    var msg = Object.assign({ action: action }, data || {});
    var json = JSON.stringify(msg);
    if (NATIVE) {
      try { window.webkit.messageHandlers.aiq.postMessage(json); } catch (e) { /* ignore */ }
    } else {
      window.AIQ_OUTBOX.push(msg);
    }
  }

  // ── DOM helpers ───────────────────────────────────────────────────────────
  function h(tag, props) {
    var el = document.createElement(tag);
    if (props) {
      for (var k in props) {
        var v = props[k];
        if (v == null || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "style") el.setAttribute("style", v);
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { append(el, k); }); return; }
    el.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs) {
    var el = document.createElementNS(SVGNS, tag);
    for (var k in attrs || {}) el.setAttribute(k, attrs[k]);
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) el.appendChild(arguments[i]);
    return el;
  }

  // Trusted, constant icon paths (16×16 grid).
  var ICONS = {
    refresh: "M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3",
    share: "M8 10V2.5M5.5 5 8 2.5 10.5 5M5 7.5H4a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1h-1",
    sliders: "M2.5 4.5h6.5M12.5 4.5h1M2.5 11.5h1M7 11.5h6.5M11 3v3M5.2 10v3",
    chevron: "M6 3.5 10.5 8 6 12.5",
    external: "M9.5 2.5h4v4M13.5 2.5 8 8M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3",
    warn: "M8 2.5 14 13.2H2ZM8 6.6v3M8 11.3v.1",
    check: "M3.5 8.5 6.5 11.5 12.5 4.5",
    x: "M4 4l8 8M12 4l-8 8",
    terminal: "M2.5 3.5h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1ZM4.5 6.5l2 2-2 2M8 10.5h3.5",
    key: "M7.3 8.7 13 3M11 5l1.5 1.5M8 10.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z",
    star: "M8 2.2l1.8 3.7 4 .6-2.9 2.8.7 4L8 11.4l-3.6 1.9.7-4-2.9-2.8 4-.6Z",
    clock: "M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0ZM8 5v3l2 1.5",
    bolt: "M9 1.5 3.5 9H8l-1 5.5L12.5 7H8Z",
    plus: "M8 3.5v9M3.5 8h9",
    chart: "M2.5 13.5h11M4.5 11V8M7.5 11V4.5M10.5 11V6.5",
    copy: "M5.5 5.5V3.5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2M3.5 5.5h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z",
    download: "M8 2.5v8M5 7.5l3 3 3-3M3 12.5v1h10v-1",
    xlogo: "M3 2.5h2.6l7.4 11H10.4ZM12.8 2.5 8.9 7M3.2 13.5 7.1 9",
    menubar: "M1.5 4.5h13v7h-13ZM1.5 6.5h13M10.5 5.5h2",
    person: "M10.5 5.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM3 13.5c.8-2.4 2.8-3.5 5-3.5s4.2 1.1 5 3.5",
    bell: "M4 11V7a4 4 0 0 1 8 0v4l1.2 1.5H2.8ZM6.5 14.3h3",
    info: "M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0ZM8 7.3V11M8 5.2v.1",
    doc: "M4 1.5h5.5l3 3v10H4ZM9.5 1.5v3h3M6 8h4.5M6 10.5h4.5",
    power: "M8 2v5.5M4.6 4.2a5 5 0 1 0 6.8 0",
    arrowup: "M8 13V3M4 7l4-4 4 4",
  };
  function icon(name, cls) {
    var el = s("svg", { viewBox: "0 0 16 16", class: "i" + (cls ? " " + cls : ""), "aria-hidden": "true" });
    el.appendChild(s("path", { d: ICONS[name] || "" }));
    return el;
  }

  function logo() {
    // A three-quarter gauge made of the four provider colours.
    var el = s("svg", { viewBox: "0 0 32 32", class: "logo", "aria-hidden": "true" });
    var defs = s("defs");
    var g = s("linearGradient", { id: "lg" + Math.random().toString(36).slice(2, 7), x1: "0", y1: "1", x2: "1", y2: "0" });
    [["0", "#D4704A"], ["0.4", "#B04BB8"], ["0.7", "#3A7BD5"], ["1", "#189E73"]].forEach(function (st) {
      g.appendChild(s("stop", { offset: st[0], "stop-color": st[1] }));
    });
    defs.appendChild(g);
    el.appendChild(defs);
    el.appendChild(s("rect", { x: "1", y: "1", width: "30", height: "30", rx: "8", fill: "url(#" + g.id + ")" }));
    el.appendChild(s("path", { d: "M9.6 21.6a9 9 0 1 1 12.8 0", fill: "none", stroke: "rgba(255,255,255,0.35)",
                                "stroke-width": "3.2", "stroke-linecap": "round" }));
    el.appendChild(s("path", { d: "M9.6 21.6a9 9 0 0 1 8.9-14.6", fill: "none", stroke: "#fff",
                                "stroke-width": "3.2", "stroke-linecap": "round" }));
    el.appendChild(s("circle", { cx: "16", cy: "16", r: "2.2", fill: "#fff" }));
    return el;
  }

  // ── colour + formatting ──────────────────────────────────────────────────
  var SEV = { warn: "#F0A020", crit: "#D8403C" };
  function fillColor(sev, brand) { return SEV[sev] || brand; }
  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hhmm(d) { return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function nowSec() { return Date.now() / 1000; }
  function fmtDuration(secs) {
    secs = Math.max(0, Math.floor(secs));
    if (secs < 3600) return Math.max(1, Math.floor(secs / 60)) + "m";
    if (secs < 86400) {
      var hh = Math.floor(secs / 3600), mm = Math.floor(secs % 3600 / 60);
      return mm ? hh + "h " + mm + "m" : hh + "h";
    }
    var d = Math.floor(secs / 86400), r = Math.floor(secs % 86400 / 3600);
    return r ? d + "d " + r + "h" : d + "d";
  }
  // Mirrors providers.fmt_reset_ts so live countdowns match the Python text.
  function resetText(ts, fallback) {
    if (!ts) return fallback || "";
    var secs = ts - nowSec();
    if (secs <= 0) return "Resets soon";
    if (secs < 20 * 3600) {
      var hh = Math.floor(secs / 3600), mm = Math.floor(secs % 3600 / 60);
      return hh > 0 ? "Resets in " + hh + "h " + mm + "m" : "Resets in " + Math.max(1, mm) + "m";
    }
    if (secs < 6 * 86400) {
      var d = new Date(ts * 1000);
      return "Resets " + DAYS[d.getDay()] + " " + hhmm(d);
    }
    return "Resets in " + Math.floor(secs / 86400) + "d";
  }
  function withPrefix(text, prefix) {
    return prefix && text ? text.replace(/^Resets/, prefix) : text;
  }
  function ago(ts) {
    if (!ts) return "Not updated yet";
    var secs = nowSec() - ts;
    if (secs < 45) return "Updated just now";
    if (secs < 3600) return "Updated " + Math.round(secs / 60) + "m ago";
    return "Updated " + fmtDuration(secs) + " ago";
  }

  // ── shared components ────────────────────────────────────────────────────
  function providerIcon(p, cls) {
    var src = ASSETS.icons[p.id] || "";
    if (p.glyph) {
      return h("span", { class: "glyph " + (cls || ""), style: "background:" + p.color }, icon(p.glyph));
    }
    if (p.mask) {
      var st = "background-color:" + p.color + ";-webkit-mask-image:url('" + src + "');mask-image:url('" + src + "')";
      return h("span", { class: "picon mask " + (cls || ""), style: st, role: "img", "aria-label": p.name });
    }
    return h("span", { class: "picon " + (cls || ""), style: "background-image:url('" + src + "')", role: "img", "aria-label": p.name });
  }

  function chip(tone, text) {
    var ic = tone === "crit" || tone === "warn" ? icon("warn") : tone === "good" ? icon("check") : null;
    return h("span", { class: "chip " + tone }, ic, text);
  }

  function ring(pct, color, pace, size) {
    size = size || 68;
    var r = 28, c = 2 * Math.PI * r;
    var svg = s("svg", { viewBox: "0 0 68 68" });
    svg.appendChild(s("circle", { cx: "34", cy: "34", r: r, fill: "none", stroke: rgba(color, 0.18), "stroke-width": "7" }));
    var arc = s("circle", { cx: "34", cy: "34", r: r, fill: "none", stroke: color, "stroke-width": "7",
                            "stroke-linecap": "round", "stroke-dasharray": (c * Math.min(100, pct) / 100).toFixed(1) + " " + c.toFixed(1) });
    if (pct <= 0) arc.setAttribute("stroke-dasharray", "0 " + c);
    svg.appendChild(arc);
    if (pace != null) {
      var a = pace / 100 * 2 * Math.PI;
      var x1 = 34 + (r - 6) * Math.cos(a), y1 = 34 + (r - 6) * Math.sin(a);
      var x2 = 34 + (r + 6) * Math.cos(a), y2 = 34 + (r + 6) * Math.sin(a);
      svg.appendChild(s("line", { x1: x1, y1: y1, x2: x2, y2: y2, stroke: "var(--text-2)", "stroke-width": "2", "stroke-linecap": "round" }));
    }
    var box = h("div", { class: "ring", style: "width:" + size + "px;height:" + size + "px",
                         title: pace != null ? "The tick marks an even pace (" + Math.round(pace) + "% of the window has passed)" : null });
    box.appendChild(svg);
    box.appendChild(h("div", { class: "val num" + (pct >= 100 ? " full" : "") }, String(pct), h("small", null, "%")));
    return box;
  }

  function meter(m, brand) {
    var color = fillColor(m.severity, brand);
    var bar = h("div", { class: "bar", style: "background:" + rgba(color, 0.16),
                         role: "progressbar", "aria-valuenow": m.pct, "aria-valuemin": "0", "aria-valuemax": "100",
                         "aria-label": m.label });
    bar.appendChild(h("div", { class: "fill", style: "width:" + Math.min(100, m.pct) + "%;background:" + color }));
    if (m.pace_pct != null) {
      bar.appendChild(h("div", { class: "pace", style: "left:" + m.pace_pct + "%",
                                 title: "Even pace: " + Math.round(m.pace_pct) + "% of this window has passed" }));
    }
    var el = h("div", { class: "meter" },
      h("div", { class: "meter-top" },
        h("span", { class: "meter-label", title: m.sublabel || null }, m.label),
        h("span", { class: "meter-reset", "data-reset-ts": m.reset_ts || null, "data-reset-fallback": m.reset_text || "" },
          resetText(m.reset_ts, m.reset_text)),
        h("span", { class: "meter-pct num " + (m.severity !== "ok" ? m.severity : "") }, m.pct + "%")),
      bar);
    if (m.note && !m.hero) {
      el.appendChild(h("div", { class: "meter-note " + m.note.tone },
        icon(m.note.tone === "good" ? "check" : m.note.tone === "info" ? "clock" : "bolt", "xs"), m.note.text));
    }
    return el;
  }

  function trendChart(trend, color) {
    var pts = trend.points;
    var W = 300, H = 34;
    var t0 = trend.since, t1 = trend.until;
    function x(t) { return (t - t0) / (t1 - t0) * W; }
    function y(p) { return H - 2 - p / 100 * (H - 4); }
    var d = "", peak = 0;
    pts.forEach(function (p, i) {
      d += (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1);
      peak = Math.max(peak, p[1]);
    });
    var area = d + "L" + x(pts[pts.length - 1][0]).toFixed(1) + " " + H + "L" + x(pts[0][0]).toFixed(1) + " " + H + "Z";
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" },
      s("line", { x1: "0", y1: H - 0.5, x2: W, y2: H - 0.5, stroke: "var(--hairline-strong)", "stroke-width": "1", "vector-effect": "non-scaling-stroke" }),
      s("path", { d: area, fill: rgba(color, 0.12) }),
      s("path", { d: d, fill: "none", stroke: color, "stroke-width": "1.75", "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
    var xh = h("div", { class: "xh" });
    var tip = h("div", { class: "tip" });
    var wrap = h("div", { class: "trend" },
      h("div", { class: "trend-cap" }, h("span", null, "Last 24 hours"), h("span", { class: "num" }, "Peak " + peak + "%")),
      svg, xh, tip);
    var hit = h("div", { class: "hit" });
    hit.addEventListener("mousemove", function (ev) {
      var r = hit.getBoundingClientRect();
      var fx = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      var t = t0 + fx * (t1 - t0), best = pts[0];
      pts.forEach(function (p) { if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; });
      var px = x(best[0]) / W * r.width;
      xh.style.display = "block"; xh.style.left = px + "px";
      tip.textContent = "";
      append(tip, [h("b", null, best[1] + "%"), " at " + hhmm(new Date(best[0] * 1000))]);
      tip.style.display = "block";
      tip.style.top = "-8px";
      tip.style.left = Math.min(r.width - 90, Math.max(0, px - 40)) + "px";
    });
    hit.addEventListener("mouseleave", function () { xh.style.display = "none"; tip.style.display = "none"; });
    wrap.appendChild(hit);
    return wrap;
  }

  // ── PANEL ─────────────────────────────────────────────────────────────────
  function renderPanel(st) {
    var fetching = st.footer && st.footer.fetching;
    var shareBtn = h("button", { class: "icon-btn", title: "Share", "aria-haspopup": "menu",
      onclick: function (e) { e.stopPropagation(); ui.menuOpen = !ui.menuOpen; menu.classList.toggle("open", ui.menuOpen); } },
      icon("share"));
    var menu = h("div", { class: "menu" + (ui.menuOpen ? " open" : ""), role: "menu" },
      h("button", { role: "menuitem", onclick: function () { closeMenu(); shareImage("copy"); } }, icon("copy"), "Copy image"),
      h("button", { role: "menuitem", onclick: function () { closeMenu(); shareImage("save"); } }, icon("download"), "Save image…"),
      h("button", { role: "menuitem", onclick: function () { closeMenu(); shareImage("x"); } }, icon("xlogo"), "Post on X"),
      h("hr"),
      h("button", { role: "menuitem", onclick: function () { closeMenu(); send("star"); } }, icon("star"), "Star on GitHub"));
    var head = h("header", { class: "p-head" },
      h("div", { class: "brand" }, logo(), "AIQuotaBar"),
      h("div", { class: "p-actions" },
        h("button", { class: "icon-btn" + (fetching ? " spin" : ""), title: "Refresh  ⌘R", onclick: function () { send("refresh"); } }, icon("refresh")),
        shareBtn,
        h("button", { class: "icon-btn", title: "Settings  ⌘,", onclick: function () { send("open_settings"); } }, icon("sliders")),
        menu));

    var body = [];
    if (st.state === "empty" || (st.state === "loading" && !st.cards.length)) {
      body.push(emptyState(st));
    } else {
      if (st.hero) body.push(hero(st.hero));
      body.push(h("section", { class: "cards" }, st.cards.map(card)));
      if (st.extras && st.extras.length) body.push(h("section", { class: "extras" }, st.extras.map(extra)));
      if (st.connect && st.connect.length && st.cards.length) {
        body.push(h("button", { class: "connect", onclick: function () { send("open_settings", { tab: "accounts" }); } },
          h("span", { class: "stack" }, st.connect.map(function (p) { return providerIcon(p); })),
          "Connect " + joinNames(st.connect.map(function (p) { return p.name; })),
          h("span", { style: "margin-left:auto;display:flex" }, icon("plus", "sm"))));
      }
    }

    var f = st.footer || {};
    var foot = h("footer", { class: "p-foot" + (f.stale ? " stale" : "") },
      f.stale ? icon("warn", "xs") : null,
      h("span", { class: "when", "data-ago-ts": f.updated_ts || "" }, ago(f.updated_ts)),
      h("span", null, "·"), h("span", null, "every " + (f.interval_text || "5 min")),
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", onclick: function () { send("open_history"); } }, "History", icon("chevron", "xs")));
    return h("div", { class: "panel" }, head, body, foot);
  }

  function joinNames(names) {
    if (names.length <= 1) return names.join("");
    return names.slice(0, -1).join(", ") + " or " + names[names.length - 1];
  }

  function closeMenu() {
    ui.menuOpen = false;
    var m = document.querySelector(".menu");
    if (m) m.classList.remove("open");
  }
  document.addEventListener("click", function () { if (ui.menuOpen) closeMenu(); });

  function hero(hr) {
    var color = fillColor(hr.severity, hr.color);
    var ins = hr.insight;
    return h("section", { class: "hero", style: "--glow:" + rgba(color, 0.35) },
      ring(hr.pct, color, hr.pace_pct, 64),
      h("div", null,
        h("div", { class: "hero-eyebrow" }, providerIcon({ id: hr.provider, name: hr.name, color: hr.color, mask: hr.mask }),
          hr.name + " · " + hr.label),
        h("div", { class: "hero-title" }, hr.headline),
        h("div", { class: "hero-sub" }, icon("clock", "xs"),
          h("span", { "data-reset-ts": hr.reset_ts || null, "data-reset-fallback": hr.reset_text || "",
                      "data-reset-prefix": hr.pct >= 100 ? "Back" : null },
            withPrefix(resetText(hr.reset_ts, hr.reset_text), hr.pct >= 100 ? "Back" : null) || "No reset time reported"))),
      ins ? h("div", { class: "insight " + ins.tone },
        icon(ins.tone === "good" ? "check" : ins.tone === "info" ? "clock" : "bolt", "sm"), ins.text) : null);
  }

  function card(c) {
    var head = h("div", { class: "card-head" },
      providerIcon(c), h("span", { class: "card-name" }, c.name),
      c.status ? chip(c.status.tone, c.status.text) : null,
      h("span", { class: "spacer" }),
      c.summary ? h("span", { class: "summary num" }, c.summary) : null,
      c.usage_url ? h("button", { class: "icon-btn open", title: "Open " + c.name + " usage page",
        onclick: function () { send("open_url", { url: c.usage_url }); } }, icon("external", "sm")) : null);
    var el = h("article", { class: "card" + (c.stale ? " stale" : ""), style: "--brand:" + c.color }, head);
    if (c.state === "loading") {
      el.appendChild(h("div", { class: "skeleton" }));
      el.appendChild(h("div", { class: "skeleton short" }));
      return el;
    }
    if (c.state === "error" && c.error) {
      var act = c.error.action;
      el.appendChild(h("div", { class: "card-error" }, icon("warn"),
        h("div", null, h("div", { class: "t" }, c.error.title), h("div", { class: "d" }, c.error.detail)),
        act ? h("button", { class: "btn", onclick: function () { send(act.id, { provider: act.provider }); } }, act.label) : null));
      return el;
    }
    el.appendChild(h("div", { class: "meters" }, c.meters.map(function (m) { return meter(m, c.color); })));
    if (c.trend) el.appendChild(trendChart(c.trend, c.color));
    if (c.hits) {
      el.appendChild(h("div", { class: "card-foot" }, icon("warn", "xs"),
        "Hit the limit " + c.hits + "× this week"));
    }
    return el;
  }

  function extra(x) {
    return h("div", { class: "extra" },
      providerIcon(x),
      h("div", { class: "extra-body" },
        h("div", { class: "extra-name" }, x.name),
        h("div", { class: "extra-rows" }, x.rows.map(function (r) {
          return h("span", null, r.label + " ", h("b", null, r.value));
        }))),
      x.meter ? h("span", { class: "meter-pct num " + (x.meter.severity !== "ok" ? x.meter.severity : "") }, x.meter.pct + "%") : null);
  }

  function emptyState(st) {
    var spin = s("svg", { viewBox: "0 0 44 44" },
      s("circle", { cx: "22", cy: "22", r: "18", fill: "none", stroke: "var(--fill-2)", "stroke-width": "4" }),
      s("path", { d: "M22 4a18 18 0 0 1 18 18", fill: "none", stroke: "#3A7BD5", "stroke-width": "4", "stroke-linecap": "round" }));
    var loading = st.state === "loading";
    return h("div", { class: "empty" },
      h("div", { class: "ring-spin" }, loading ? spin : logo()),
      h("h2", null, loading ? "Looking for your AI accounts…" : "No accounts connected yet"),
      h("p", null, loading
        ? "Reading your existing browser sessions for Claude, ChatGPT, Cursor and Copilot. Nothing leaves your Mac except the usage requests to each service."
        : "Sign in to claude.ai, chatgpt.com, cursor.com or github.com in your browser, then detect again."),
      h("div", { class: "btns" },
        loading ? null : h("button", { class: "btn primary", onclick: function () { send("detect", { provider: "all" }); } }, "Detect accounts"),
        h("button", { class: "btn", onclick: function () { send("open_settings", { tab: "accounts" }); } }, "Open Settings")));
  }

  // ── share card (canvas) ──────────────────────────────────────────────────
  function loadImage(src) {
    return new Promise(function (res) {
      if (!src) return res(null);
      var im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { res(null); };
      im.src = src;
    });
  }
  function tinted(im, color, size) {
    var c = document.createElement("canvas");
    c.width = c.height = size;
    var x = c.getContext("2d");
    x.drawImage(im, 0, 0, size, size);
    x.globalCompositeOperation = "source-in";
    x.fillStyle = color;
    x.fillRect(0, 0, size, size);
    return c;
  }
  function roundRect(x, X, Y, W, H, r) {
    x.beginPath();
    x.moveTo(X + r, Y); x.arcTo(X + W, Y, X + W, Y + H, r); x.arcTo(X + W, Y + H, X, Y + H, r);
    x.arcTo(X, Y + H, X, Y, r); x.arcTo(X, Y, X + W, Y, r); x.closePath();
  }

  function shareCard(share) {
    var items = (share.items || []).slice(0, 4);
    var W = 1200, H = 630, S = 2;
    var cv = document.createElement("canvas");
    cv.width = W * S; cv.height = H * S;
    var x = cv.getContext("2d");
    x.scale(S, S);
    var font = getComputedStyle(document.body).fontFamily;
    function f(w, px) { return w + " " + px + "px " + font; }

    var bg = x.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, "#15151B"); bg.addColorStop(1, "#1D1A24");
    x.fillStyle = bg; x.fillRect(0, 0, W, H);
    items.forEach(function (it, i) {
      var g = x.createRadialGradient(150 + i * 300, 640, 0, 150 + i * 300, 640, 420);
      g.addColorStop(0, rgba(it.color, 0.22)); g.addColorStop(1, rgba(it.color, 0));
      x.fillStyle = g; x.fillRect(0, 0, W, H);
    });

    return Promise.all(items.map(function (it) { return loadImage(ASSETS.icons[it.id]); })).then(function (imgs) {
      // header
      var lg = x.createLinearGradient(64, 88, 108, 44);
      lg.addColorStop(0, "#D4704A"); lg.addColorStop(0.4, "#B04BB8"); lg.addColorStop(0.7, "#3A7BD5"); lg.addColorStop(1, "#189E73");
      roundRect(x, 64, 52, 40, 40, 11); x.fillStyle = lg; x.fill();
      x.strokeStyle = "#fff"; x.lineWidth = 4; x.lineCap = "round";
      x.beginPath(); x.arc(84, 72, 11, Math.PI * 0.75, Math.PI * 1.55); x.stroke();
      x.fillStyle = "#fff"; x.beginPath(); x.arc(84, 72, 2.8, 0, Math.PI * 2); x.fill();
      x.fillStyle = "#fff"; x.font = f(700, 26); x.textBaseline = "middle";
      x.fillText("AIQuotaBar", 118, 73);
      var d = new Date();
      x.font = f(500, 18); x.fillStyle = "rgba(255,255,255,0.5)"; x.textAlign = "right";
      x.fillText(DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()] + " · " + hhmm(d), W - 64, 73);
      x.textAlign = "left";
      x.font = f(700, 50); x.fillStyle = "#fff"; x.textBaseline = "alphabetic";
      x.fillText(items.length ? "My AI limits right now" : "Track your AI limits", 64, 178);

      // tiles
      var n = Math.max(1, items.length), gap = 20, tw = (W - 128 - gap * (n - 1)) / n, top = 222, th = 300;
      items.forEach(function (it, i) {
        var X = 64 + i * (tw + gap);
        roundRect(x, X, top, tw, th, 22);
        x.fillStyle = "rgba(255,255,255,0.055)"; x.fill();
        x.strokeStyle = "rgba(255,255,255,0.09)"; x.lineWidth = 1; x.stroke();
        var im = imgs[i];
        if (im) x.drawImage(it.mask ? tinted(im, it.color, 64) : im, X + 26, top + 26, 30, 30);
        x.fillStyle = "#fff"; x.font = f(650, 24); x.textBaseline = "middle";
        x.fillText(it.name, X + (im ? 66 : 26), top + 42);
        // ring
        var color = fillColor(it.severity, it.color);
        var cx = X + tw / 2, cy = top + 146, r = 58;
        x.lineWidth = 14; x.lineCap = "round";
        x.strokeStyle = rgba(color, 0.2); x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.stroke();
        if (it.pct > 0) {
          x.strokeStyle = color; x.beginPath();
          x.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(100, it.pct) / 100); x.stroke();
        }
        x.textAlign = "center"; x.fillStyle = "#fff"; x.font = f(700, 36);
        x.fillText(it.pct + "%", cx, cy + 2);
        x.font = f(500, 17); x.fillStyle = "rgba(255,255,255,0.72)";
        x.fillText(it.label, cx, top + 240);
        x.font = f(400, 15); x.fillStyle = "rgba(255,255,255,0.45)";
        x.fillText(resetText(null, it.reset_text) || " ", cx, top + 266);
        x.textAlign = "left";
      });
      if (!items.length) {
        x.font = f(500, 24); x.fillStyle = "rgba(255,255,255,0.6)";
        x.fillText("Claude · ChatGPT · Cursor · Copilot — live in the macOS menu bar", 64, 300);
      }
      // footer
      x.textBaseline = "middle";
      x.font = f(500, 19); x.fillStyle = "rgba(255,255,255,0.62)";
      x.fillText("Live Claude, ChatGPT, Cursor & Copilot limits in the macOS menu bar", 64, 578);
      x.textAlign = "right"; x.fillStyle = "#fff"; x.font = f(600, 19);
      x.fillText("github.com/yagcioglutoprak/AIQuotaBar", W - 64, 578);
      x.textAlign = "left";
      return cv;
    });
  }

  function shareImage(mode) {
    if (!STATE || !STATE.share) return;
    shareCard(STATE.share).then(function (cv) {
      var png = cv.toDataURL("image/png");
      send("share_image", { mode: mode, png: png });
      if (mode === "copy") toast("Image copied — paste it anywhere");
      if (mode === "x") toast("Image copied — paste it into your post (⌘V)");
    }).catch(function () { toast("Couldn't create the image"); });
  }

  var toastTimer = null;
  function toast(text) {
    var t = document.getElementById("toast");
    t.textContent = "";
    append(t, [icon("check", "sm"), text]);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2200);
  }

  // ── SETTINGS ─────────────────────────────────────────────────────────────
  var TABS = [["general", "General", "sliders"], ["menubar", "Menu bar", "menubar"],
              ["accounts", "Accounts", "person"], ["notifications", "Notifications", "bell"],
              ["about", "About", "info"]];

  function toggle(on, onchange, label) {
    return h("button", { class: "toggle" + (on ? " on" : ""), role: "switch", "aria-checked": on ? "true" : "false",
                         "aria-label": label, onclick: function () { onchange(!on); } });
  }
  function setting(key, value) { send("set", { key: key, value: value }); }
  function row(left, right, desc) {
    return h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "title" }, left),
      desc ? h("div", { class: "desc" }, desc) : null), right);
  }

  function renderSettings(st) {
    var tab = ui.tab || st.tab || "general";
    var nav = h("nav", { class: "s-nav" }, TABS.map(function (t) {
      return h("button", { class: tab === t[0] ? "on" : "", onclick: function () { ui.tab = t[0]; render(STATE); } }, icon(t[2]), t[1]);
    }));
    var pane = ({ general: sGeneral, menubar: sMenubar, accounts: sAccounts, notifications: sNotifs, about: sAbout }[tab] || sGeneral)(st);
    return h("div", { class: "settings" },
      h("aside", { class: "s-side" }, h("div", { class: "brand" }, logo(), "AIQuotaBar"), nav),
      h("section", { class: "s-content" }, pane));
  }

  function sGeneral(st) {
    var g = st.general;
    function rangeRow(label, key, val, min, max, desc) {
      var num = h("span", { class: "num" }, val + "%");
      var inp = h("input", { type: "range", min: min, max: max, step: "1", value: val,
        oninput: function () { num.textContent = inp.value + "%"; },
        onchange: function () { setting(key, parseInt(inp.value, 10)); } });
      return row(label, h("div", { class: "range" }, inp, num), desc);
    }
    return [
      h("h1", null, "General"),
      h("p", { class: "lead" }, "How often AIQuotaBar checks your limits and when it speaks up."),
      h("div", { class: "group" },
        row("Launch at login", toggle(g.launch_at_login, function (v) { send("set_login", { value: v }); }, "Launch at login"),
          "Start AIQuotaBar automatically when you log in."),
        row("Refresh every", h("div", { class: "seg", role: "radiogroup" }, g.intervals.map(function (iv) {
          return h("button", { class: iv.secs === g.refresh_interval ? "on" : "", role: "radio",
            "aria-checked": iv.secs === g.refresh_interval ? "true" : "false",
            onclick: function () { setting("refresh_interval", iv.secs); } }, iv.label);
        })), "Faster refresh makes pace predictions more accurate.")),
      h("div", { class: "group-title" }, "Alert thresholds"),
      h("div", { class: "group" },
        rangeRow("Warning", "warn_threshold", g.warn, 50, 94, "Turns amber and sends a heads-up."),
        rangeRow("Critical", "crit_threshold", g.crit, 60, 100, "Turns red and sends an urgent alert.")),
      h("p", { class: "note" }, icon("info", "sm"),
        "Pace alerts fire when your recent usage would hit a limit within 30 minutes, before it resets."),
    ];
  }

  function sMenubar(st) {
    var mb = st.menubar;
    var chosen = mb.providers.filter(function (p) { return p.on; }).map(function (p) { return p.name; });
    var preview = h("div", { class: "mb-preview", "aria-label": "Preview" },
      h("span", { class: "dim" }, "Wed 14:32"));
    var demo = { Claude: [78, "2h 14m"], ChatGPT: [64, "2d 6h"], Cursor: [48, "11d"], Copilot: [62, "8d"] };
    mb.providers.filter(function (p) { return p.on; }).forEach(function (p, i) {
      var d = demo[p.name] || [0, ""];
      preview.insertBefore(h("span", { class: "seg-i" }, providerIcon(p),
        d[0] + "%" + (mb.show_reset && i === 0 ? " · " + d[1] : "")), preview.lastChild);
    });
    if (mb.show_cc) preview.insertBefore(h("span", { class: "seg-i dim" }, "◆ 1.2k"), preview.lastChild);
    return [
      h("h1", null, "Menu bar"),
      h("p", { class: "lead" }, "Choose what stays visible in the menu bar."),
      preview,
      h("div", { class: "group-title" }, "Show"),
      h("div", { class: "group" },
        row("Pick automatically", toggle(mb.auto, function (v) {
          setting("bar_providers", v ? [] : chosen);
        }, "Pick automatically"), "Shows the first two services you use."),
        h("div", { class: "row" }, h("div", { class: "checks" }, mb.providers.map(function (p) {
          return h("button", { class: "check" + (p.on ? " on" : ""), "aria-pressed": p.on ? "true" : "false",
            onclick: function () {
              var next = p.on ? chosen.filter(function (n) { return n !== p.name; }) : chosen.concat([p.name]);
              setting("bar_providers", next);
            } }, providerIcon(p), p.name);
        })))),
      h("div", { class: "group-title" }, "Details"),
      h("div", { class: "group" },
        row("Time until reset", toggle(mb.show_reset, function (v) { setting("bar_show_reset", v); }, "Time until reset"),
          "Adds a countdown next to the first service, e.g. 78% · 2h 14m."),
        row("Claude Code messages", toggle(mb.show_cc, function (v) { setting("bar_show_cc", v); }, "Claude Code messages"),
          "Your Claude Code messages this week, read from ~/.claude.")),
      h("p", { class: "note" }, icon("info", "sm"),
        "Percentages turn amber and red at your alert thresholds so trouble is visible without opening anything."),
    ];
  }

  function sAccounts(st) {
    var rows = st.accounts.map(function (a) {
      var dot = h("span", { class: "status-dot " + a.status });
      var actions = h("div", { style: "display:flex;gap:6px" });
      if (a.status === "busy") {
        actions.appendChild(h("button", { class: "btn", disabled: true }, "Detecting…"));
      } else if (a.status === "off") {
        actions.appendChild(h("button", { class: "btn", onclick: function () { send("enable", { provider: a.id }); } }, "Turn on"));
      } else {
        actions.appendChild(h("button", { class: "btn", onclick: function () { send("detect", { provider: a.id }); } },
          a.status === "on" ? "Detect again" : a.status === "error" ? "Reconnect" : "Detect"));
        actions.appendChild(h("button", { class: "btn ghost quiet", title: "Stop tracking " + a.name,
          onclick: function () { send("disable", { provider: a.id }); } }, "Turn off"));
      }
      return h("div", { class: "row" }, providerIcon(a),
        h("div", { class: "grow" }, h("div", { class: "title" }, a.name),
          h("div", { class: "desc" }, dot, a.detail)), actions);
    });
    var manual = h("div", { class: "row", style: "display:block" },
      h("button", { class: "link", onclick: function () { ui.cookieOpen = !ui.cookieOpen; render(STATE); } },
        (ui.cookieOpen ? "Hide" : "Paste a Claude cookie manually…")));
    if (ui.cookieOpen) {
      var ta = h("textarea", { placeholder: "sessionKey=…; lastActiveOrg=…", spellcheck: "false",
        oninput: function () { ui.drafts.cookie = ta.value; } });
      ta.value = ui.drafts.cookie || "";
      manual.appendChild(h("div", { class: "field" }, ta,
        h("button", { class: "btn primary", onclick: function () {
          if (ta.value.trim()) { send("set_cookie", { provider: "claude", value: ta.value.trim() }); ui.drafts.cookie = ""; ta.value = ""; toast("Cookie saved — refreshing"); }
        } }, "Save")));
      manual.appendChild(h("div", { class: "desc", style: "margin-top:6px" },
        "Open claude.ai/settings/usage → Developer Tools → Network → any request → copy the “cookie” header."));
    }
    var keys = st.api_keys.map(function (k) {
      var inp = h("input", { type: "password", placeholder: k.set ? k.masked : "Paste API key", autocomplete: "off",
        oninput: function () { ui.drafts[k.key] = inp.value; } });
      inp.value = ui.drafts[k.key] || "";
      return h("div", { class: "row", style: "display:block" },
        h("div", { style: "display:flex;align-items:center;gap:10px" },
          h("span", { class: "glyph", style: "background:#8A8A8E" }, icon("key")),
          h("div", { class: "grow" }, h("div", { class: "title" }, k.name + " API"),
            h("div", { class: "desc" }, h("span", { class: "status-dot " + (k.set ? "on" : "") }), k.set ? "Key saved" : "Optional — shows spend or balance"))),
        h("div", { class: "field" }, inp,
          h("button", { class: "btn", onclick: function () {
            if (inp.value.trim()) { send("set_api_key", { key: k.key, value: inp.value.trim() }); ui.drafts[k.key] = ""; inp.value = ""; }
          } }, "Save"),
          k.set ? h("button", { class: "btn ghost danger", onclick: function () { send("set_api_key", { key: k.key, value: "" }); } }, "Remove") : null));
    });
    return [
      h("h1", null, "Accounts"),
      h("p", { class: "lead" }, "AIQuotaBar reuses the sessions already in your browser — no passwords, no copy-pasting."),
      h("div", { class: "group" }, rows, manual),
      h("p", { class: "note" }, icon("info", "sm"),
        "Cookies are read locally and stored only in ~/.claude_bar_config.json. Requests go straight to each provider — never to a third party."),
      h("div", { class: "group-title" }, "API spend (optional)"),
      h("div", { class: "group" }, keys),
    ];
  }

  function sNotifs(st) {
    var grid = h("div", { class: "notif-grid" },
      h("div", { class: "h" }), h("div", { class: "h" }, "Usage warnings"), h("div", { class: "h" }, "Pace alerts"),
      h("div", { class: "h" }, "Reset alerts"));
    st.notifications.forEach(function (g) {
      grid.appendChild(h("div", { class: "p" }, providerIcon({ id: g.provider, name: g.name, color: g.color,
        mask: g.provider !== "claude" }), g.name));
      ["Usage warnings", "Pace alerts", "Reset alerts"].forEach(function (lbl) {
        var it = g.items.filter(function (i) { return i.label === lbl; })[0];
        grid.appendChild(h("div", { class: "c" }, it ? toggle(it.on, function (v) { setting("notifications." + it.key, v); }, g.name + " " + lbl)
          : h("span", { class: "faint" }, "—")));
      });
    });
    return [
      h("h1", null, "Notifications"),
      h("p", { class: "lead" }, "Get a heads-up before you're cut off — and a nudge when you're back."),
      h("div", { class: "group" }, grid),
      h("div", { style: "margin-top:14px" }, h("button", { class: "btn", onclick: function () { send("test_notification"); } }, icon("bell", "sm"), "Send a test notification")),
    ];
  }

  function sAbout(st) {
    return [
      h("div", { class: "about-hero" }, logo(),
        h("div", null, h("h1", null, "AIQuotaBar"), h("div", { class: "muted" }, "Version " + (st.version || "dev") + " · MIT licensed"))),
      h("div", { class: "star-cta" }, icon("star"),
        h("div", { class: "grow" }, h("b", null, "Enjoying AIQuotaBar?"),
          h("span", null, "A GitHub star helps other developers find it.")),
        h("button", { class: "btn primary", onclick: function () { send("star"); } }, "Star on GitHub")),
      h("div", { class: "group-title" }, "Desktop widget"),
      h("div", { class: "group" },
        row(st.widget.installed ? "Installed" : "Not installed",
          h("button", { class: "btn", onclick: function () { send(st.widget.installed ? "open_widget" : "install_widget"); } },
            st.widget.installed ? "Open" : "How to install"),
          st.widget.installed ? "Right-click the desktop → Edit Widgets → search “AI Quota”." : "A native WidgetKit widget for your desktop and Notification Center.")),
      h("div", { class: "group-title" }, "Troubleshooting"),
      h("div", { class: "group" },
        row("Log file", h("button", { class: "btn", onclick: function () { send("open_logs"); } }, "Show"), "~/.claude_bar.log"),
        row("Raw Claude API response", h("button", { class: "btn", onclick: function () { send("show_raw"); } }, "Show"), "Useful when reporting a bug."),
        row("Report an issue", h("button", { class: "btn", onclick: function () { send("open_url", { url: st.repo_url + "/issues" }); } }, "Open"), null)),
      h("div", { style: "margin-top:16px;display:flex;gap:8px" },
        h("button", { class: "btn", onclick: function () { send("quit"); } }, icon("power", "sm"), "Quit AIQuotaBar")),
    ];
  }

  // ── HISTORY ──────────────────────────────────────────────────────────────
  function isoDay(d) { return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); }
  function dayLabel(iso) {
    var d = new Date(iso + "T12:00:00Z");
    return DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
  }

  function attachTip(host, el, html) {
    el.addEventListener("mouseenter", function () {
      var tip = host.querySelector(".tip");
      tip.textContent = ""; append(tip, html());
      var r = el.getBoundingClientRect(), hr = host.getBoundingClientRect();
      tip.style.display = "block";
      tip.style.left = Math.max(0, Math.min(hr.width - 150, r.left - hr.left + r.width / 2 - 60)) + "px";
      tip.style.top = (r.top - hr.top - 30) + "px";
    });
    el.addEventListener("mouseleave", function () { host.querySelector(".tip").style.display = "none"; });
  }

  function renderHistory(st) {
    var range = ui.range;
    var today = new Date(st.now * 1000);
    var days = [];
    for (var i = range - 1; i >= 0; i--) days.push(isoDay(new Date(today.getTime() - i * 86400000)));
    var series = st.series.map(function (sr) {
      var byDay = {};
      sr.days.forEach(function (d) { byDay[d.date] = d; });
      var list = days.map(function (dd) { return byDay[dd] || null; });
      var have = list.filter(Boolean);
      return { sr: sr, list: list, have: have };
    }).filter(function (x) { return x.have.length; });

    var head = h("div", { class: "h-head" },
      h("div", null, h("h1", null, "Usage history"),
        h("p", null, "Daily peaks for every limit AIQuotaBar tracks. Hover any bar for details.")),
      h("div", { class: "seg" }, [7, 30, 90].map(function (n) {
        return h("button", { class: n === range ? "on" : "", onclick: function () { ui.range = n; render(STATE); } }, n + " days");
      })));
    if (!series.length && !(st.trends || []).length) {
      return h("div", { class: "history" }, head, h("div", { class: "h-empty" },
        h("h2", null, "No history yet"), h("p", null, "Leave AIQuotaBar running for a day and your trends will appear here.")));
    }

    // tiles
    var tracked = {}, hits = 0, busiest = null, peakSum = 0, peakN = 0;
    series.forEach(function (x) {
      x.have.forEach(function (d) {
        tracked[d.date] = 1; hits += d.hits;
        if (x.sr.key === "claude" || series.length === 1) { peakSum += d.peak; peakN++; }
        if (!busiest || d.peak > busiest.peak) busiest = { peak: d.peak, date: d.date, label: x.sr.label };
      });
    });
    var claudeLbl = series.some(function (x) { return x.sr.key === "claude"; }) ? "Claude session" : series[0].sr.label;
    var tiles = h("div", { class: "tiles" },
      tile("Days tracked", Object.keys(tracked).length, "of the last " + range),
      tile("Avg daily peak", peakN ? Math.round(peakSum / peakN) + "%" : "—", claudeLbl),
      tile("Limit hits", hits, hits ? "samples at ≥95%" : "none in this range"),
      tile("Busiest day", busiest ? busiest.peak + "%" : "—", busiest ? dayLabel(busiest.date) + " · " + busiest.label : ""));

    var out = [head, tiles];
    if ((st.trends || []).length) out.push(last24(st));
    out.push(multiples(series, days));
    out.push(heatmap(st, series));
    return h("div", { class: "history" }, out);
  }

  function tile(l, v, sub) {
    return h("div", { class: "tile" }, h("div", { class: "l" }, l), h("div", { class: "v" }, String(v)), h("div", { class: "s" }, sub));
  }

  function last24(st) {
    var W = 640, H = 150, L = 28, B = 18, T = 6;
    var t1 = st.now, t0 = t1 - 86400;
    function x(t) { return L + (t - t0) / (t1 - t0) * (W - L - 40); }
    function y(p) { return T + (1 - p / 100) * (H - T - B); }
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, height: H });
    [0, 50, 100].forEach(function (g) {
      svg.appendChild(s("line", { class: g === 0 ? "base" : "grid", x1: L, x2: W - 40, y1: y(g), y2: y(g) }));
      var t = s("text", { x: L - 6, y: y(g) + 3, "text-anchor": "end" }); t.textContent = g + "%"; svg.appendChild(t);
    });
    for (var k = 0; k <= 4; k++) {
      var tt = t0 + k * 6 * 3600;
      var lab = s("text", { x: x(tt), y: H - 3, "text-anchor": k === 0 ? "start" : k === 4 ? "end" : "middle" });
      lab.textContent = k === 4 ? "now" : hhmm(new Date(tt * 1000));
      svg.appendChild(lab);
    }
    var trends = st.trends.slice(0, 4);
    trends.forEach(function (tr) {
      var d = tr.points.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1); }).join("");
      svg.appendChild(s("path", { d: d, fill: "none", stroke: tr.color, "stroke-width": "2", "stroke-linejoin": "round", "stroke-linecap": "round" }));
      var last = tr.points[tr.points.length - 1];
      svg.appendChild(s("circle", { cx: x(last[0]), cy: y(last[1]), r: "4", fill: tr.color, stroke: "var(--pane-bg)", "stroke-width": "2" }));
      var lbl = s("text", { x: x(last[0]) + 8, y: y(last[1]) + 3, style: "fill:var(--text-2);font-weight:600" });
      lbl.textContent = last[1] + "%";
      svg.appendChild(lbl);
    });
    var xh = s("line", { y1: T, y2: H - B, stroke: "var(--text-3)", "stroke-width": "1", style: "display:none" });
    svg.appendChild(xh);
    var host = h("div", { class: "chart" }, svg, h("div", { class: "tip" }));
    svg.addEventListener("mousemove", function (ev) {
      var r = svg.getBoundingClientRect();
      var vx = (ev.clientX - r.left) / r.width * W;
      var t = t0 + (vx - L) / (W - L - 40) * (t1 - t0);
      if (t < t0 || t > t1) return;
      xh.setAttribute("x1", vx); xh.setAttribute("x2", vx); xh.style.display = "";
      var tip = host.querySelector(".tip");
      tip.textContent = "";
      append(tip, h("b", null, hhmm(new Date(t * 1000))));
      trends.forEach(function (tr) {
        var best = tr.points[0];
        tr.points.forEach(function (p) { if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; });
        append(tip, h("div", null, h("span", { style: "display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;background:" + tr.color }), tr.label + "  ", h("b", null, best[1] + "%")));
      });
      tip.style.display = "block";
      tip.style.left = Math.min(r.width - 190, (vx / W) * r.width + 12) + "px";
      tip.style.top = "10px";
    });
    svg.addEventListener("mouseleave", function () { xh.style.display = "none"; host.querySelector(".tip").style.display = "none"; });
    return h("div", { class: "section" },
      h("h2", null, "Last 24 hours"),
      h("div", { class: "sub" }, "Live samples. Session limits reset every five hours, so they climb and drop."),
      h("div", { class: "legend" }, trends.map(function (tr) { return h("span", null, h("i", { style: "background:" + tr.color }), tr.label); })),
      host);
  }

  function multiples(series, days) {
    var grid = h("div", { class: "multiples" });
    series.forEach(function (x) {
      var W = 300, H = 84, B = 14, n = days.length;
      var slot = W / n, bw = Math.min(24, Math.max(2, slot - 2));
      var svg = s("svg", { viewBox: "0 0 " + W + " " + H, height: H });
      svg.appendChild(s("line", { class: "grid", x1: 0, x2: W, y1: (H - B) / 2, y2: (H - B) / 2 }));
      svg.appendChild(s("line", { class: "base", x1: 0, x2: W, y1: H - B + 0.5, y2: H - B + 0.5 }));
      var host = h("div", { class: "chart" });
      x.list.forEach(function (d, i) {
        if (!d) return;
        var bh = Math.max(d.peak > 0 ? 2 : 0, d.peak / 100 * (H - B - 2));
        var X = i * slot + (slot - bw) / 2, Y = H - B - bh, r = Math.min(4, bw / 2, bh);
        var path = "M" + X + " " + (H - B) + "V" + (Y + r) + "Q" + X + " " + Y + " " + (X + r) + " " + Y +
                   "H" + (X + bw - r) + "Q" + (X + bw) + " " + Y + " " + (X + bw) + " " + (Y + r) + "V" + (H - B) + "Z";
        var color = d.peak >= 95 ? SEV.crit : x.sr.color;
        var bar = s("path", { d: path, fill: color });
        var hitbox = s("rect", { x: i * slot, y: 0, width: slot, height: H - B, fill: "transparent" });
        svg.appendChild(bar); svg.appendChild(hitbox);
        attachTip(host, hitbox, function () {
          return [h("b", null, dayLabel(d.date)), h("div", null, "Peak " + d.peak + "% · avg " + d.avg + "%"),
                  d.hits ? h("div", { style: "color:var(--crit-text)" }, "Hit the limit") : null];
        });
      });
      [[0, days[0]], [n - 1, days[n - 1]]].forEach(function (p, j) {
        var t = s("text", { x: j ? W : 0, y: H - 2, "text-anchor": j ? "end" : "start" });
        t.textContent = j ? "Today" : dayLabel(p[1]);
        svg.appendChild(t);
      });
      host.appendChild(svg); host.appendChild(h("div", { class: "tip" }));
      var peaks = x.have.map(function (d) { return d.peak; });
      var avg = Math.round(peaks.reduce(function (a, b) { return a + b; }, 0) / peaks.length);
      var provider = x.sr.provider;
      grid.appendChild(h("div", null,
        h("div", { class: "mult-head" },
          provider ? providerIcon({ id: provider, name: x.sr.label, color: x.sr.color, mask: provider !== "claude" }) : null,
          x.sr.label, h("span", { class: "muted num" }, "avg peak " + avg + "%")),
        host));
    });
    return h("div", { class: "section" },
      h("h2", null, "Daily peak"),
      h("div", { class: "sub" }, "Highest usage reached each day. Red bars reached the limit."),
      grid);
  }

  function heatmap(st, series) {
    var today = new Date(st.now * 1000);
    var byDay = {};
    series.forEach(function (x) { x.sr.days.forEach(function (d) { byDay[d.date] = Math.max(byDay[d.date] || 0, d.peak); }); });
    var start = new Date(today.getTime() - 90 * 86400000);
    start = new Date(start.getTime() - ((start.getUTCDay() + 6) % 7) * 86400000);   // back to Monday
    var steps = ["var(--fill-1)", "var(--heat-1)", "var(--heat-2)", "var(--heat-3)", "var(--heat-4)", "var(--heat-5)"];
    function level(p) { return p == null ? 0 : p < 20 ? 1 : p < 45 ? 2 : p < 70 ? 3 : p < 90 ? 4 : 5; }
    var grid = h("div", { class: "heat" });
    var months = h("div", { class: "heat-months" });
    var host = h("div", { class: "chart", style: "display:inline-block" });
    var lastMonth = -1, col = 0;
    for (var t = start.getTime(); t <= today.getTime(); t += 86400000) {
      var dt = new Date(t);
      var iso = isoDay(dt);
      if (dt.getUTCDay() === 1) {
        if (dt.getUTCMonth() !== lastMonth) {
          months.appendChild(h("span", { style: "grid-column:" + (col + 1) }, MONTHS[dt.getUTCMonth()]));
          lastMonth = dt.getUTCMonth();
        }
        col++;
      }
      var p = byDay[iso];
      var cell = h("div", { style: "background:" + steps[level(p)] });
      (function (iso, p) {
        attachTip(host, cell, function () { return [h("b", null, dayLabel(iso)), h("div", null, p == null ? "No data" : "Highest peak " + p + "%")]; });
      })(iso, p);
      grid.appendChild(cell);
    }
    host.appendChild(h("div", { class: "heat-wrap" },
      h("div", { class: "heat-days" }, ["Mon", "", "Wed", "", "Fri", "", "Sun"].map(function (d) { return h("span", null, d); })),
      h("div", null, months, grid)));
    host.appendChild(h("div", { class: "tip" }));
    return h("div", { class: "section" },
      h("h2", null, "Activity"),
      h("div", { class: "sub" }, "Your highest peak across all services, each day for the last 90 days."),
      host,
      h("div", { class: "heat-legend" }, "Less", steps.map(function (c) { return h("div", { style: "background:" + c }); }), "More"));
  }

  // ── WELCOME ──────────────────────────────────────────────────────────────
  function renderWelcome(st) {
    var bar = h("div", { class: "wl-bar" },
      h("span", { class: "hl" }, providerIcon({ id: "claude", name: "Claude", color: "#D4704A", mask: false }), "78%",
        h("span", { style: "width:6px" }), providerIcon({ id: "chatgpt", name: "ChatGPT", color: "#fff", mask: true }), "64%"),
      h("span", { style: "opacity:.7" }, "Wed 14:32"),
      h("span", { class: "arrow" }, "↑ AIQuotaBar lives here"));
    var list = st.accounts.map(function (a) {
      var right = a.status === "busy" ? h("span", { class: "chip info" }, "Looking…")
        : a.status === "on" ? chip("good", "Found")
        : a.status === "error" ? chip("warn", "Needs sign-in")
        : h("button", { class: "btn", onclick: function () { send("detect", { provider: a.id }); } }, "Detect");
      return h("div", { class: "row" }, providerIcon(a),
        h("div", { class: "grow" }, h("div", { class: "title" }, a.name), h("div", { class: "desc" }, a.detail)), right);
    });
    return h("div", { class: "welcome" },
      logo(),
      h("h1", null, "Welcome to AIQuotaBar"),
      h("p", { class: "lead" }, "Your Claude, ChatGPT, Cursor and Copilot limits — always one glance away, with a warning before you get cut off."),
      bar,
      h("div", { class: "wl-list group" }, list),
      h("div", { class: "wl-foot" },
        h("button", { class: "btn primary lg", onclick: function () { send("open_panel"); } }, "Show my usage"),
        h("button", { class: "btn lg", onclick: function () { send("open_settings"); } }, "Settings")),
      h("p", { class: "wl-privacy" }, "Everything stays on your Mac. AIQuotaBar reads your browser sessions locally and only talks to the services you use."),
      h("button", { class: "btn ghost", style: "margin-top:6px", onclick: function () { send("star"); } }, icon("star", "sm"), "Star on GitHub"));
  }

  // ── render loop ──────────────────────────────────────────────────────────
  var VIEWS = { panel: renderPanel, settings: renderSettings, history: renderHistory, welcome: renderWelcome };
  var app = document.getElementById("app");

  function render(st) {
    STATE = st;
    var active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA") && active.type !== "range") {
      pendingRender = st;   // don't yank the field out from under the user
      return;
    }
    pendingRender = null;
    var scroller = document.querySelector(".s-content");
    var top = scroller ? scroller.scrollTop : 0;
    var view = VIEWS[VIEW] || renderPanel;
    app.textContent = "";
    app.appendChild(view(st));
    scroller = document.querySelector(".s-content");
    if (scroller) scroller.scrollTop = top;
    reportSize();
  }
  document.addEventListener("focusout", function () {
    setTimeout(function () { if (pendingRender) render(pendingRender); }, 0);
  });

  var lastHeight = 0;
  function reportSize() {
    if (VIEW !== "panel") return;
    var hgt = Math.ceil(app.getBoundingClientRect().height);
    if (hgt && hgt !== lastHeight) { lastHeight = hgt; send("resize", { height: hgt }); }
  }

  // Keep countdowns and "updated … ago" live without a round-trip to Python.
  setInterval(function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-reset-ts]"), function (el) {
      var ts = parseFloat(el.getAttribute("data-reset-ts"));
      if (ts) el.textContent = withPrefix(resetText(ts, el.getAttribute("data-reset-fallback")), el.getAttribute("data-reset-prefix"));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-ago-ts]"), function (el) {
      el.textContent = ago(parseFloat(el.getAttribute("data-ago-ts")) || null);
    });
  }, 15000);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { if (ui.menuOpen) closeMenu(); else if (VIEW === "panel") send("close"); }
    if (e.metaKey && (e.key === "r" || e.key === "R")) { e.preventDefault(); send("refresh"); }
    if (e.metaKey && e.key === ",") { e.preventDefault(); send("open_settings"); }
    if (e.metaKey && (e.key === "w" || e.key === "W")) { e.preventDefault(); send("close"); }
  });
  if (NATIVE) {
    // No "Reload" context menu and no navigating away by dropping files onto the window.
    document.addEventListener("contextmenu", function (e) {
      if (!/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) e.preventDefault();
    });
    ["dragover", "drop"].forEach(function (t) { document.addEventListener(t, function (e) { e.preventDefault(); }); });
  }

  window.AIQ = {
    boot: function (assets) { ASSETS = assets || ASSETS; if (STATE) render(STATE); },
    render: render,
    shareCard: shareCard,
    toast: toast,
    setView: function (v) { VIEW = v; root.className = root.className.replace(/view-\w+/, "view-" + v); },
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { send("ready", { view: VIEW }); });
  } else {
    send("ready", { view: VIEW });
  }
})();
