(function () {
  var STYLE_KEY = "gtd:style_pref";

  function getPref() {
    try { return localStorage.getItem(STYLE_KEY); } catch (e) { return null; }
  }
  function setPref(v) {
    try { localStorage.setItem(STYLE_KEY, v); } catch (e) {}
  }

  // Classify page: "morning" (daily index), "classic" (daily classic), or "calendar"
  function detectView() {
    var path = location.pathname;
    if (/\/classic\.html$/.test(path)) return "classic";
    if (/\/\d{4}-\d{2}-\d{2}\/(?:index\.html)?$/.test(path)) return "morning";
    return "calendar";
  }

  function siblingUrl(targetView) {
    var path = location.pathname;
    if (targetView === "classic") {
      return path.replace(/index\.html$/, "").replace(/\/$/, "") + "/classic.html";
    }
    return path.replace(/classic\.html$/, "");
  }

  function injectCSS() {
    var css = [
      ".gtd-pref{position:fixed;top:16px;right:16px;z-index:9999;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;}",
      ".gtd-pref-toggle{display:inline-flex;background:rgba(15,15,15,0.78);color:#fff;border-radius:999px;padding:3px;font-size:11px;font-weight:700;letter-spacing:0.15em;text-transform:uppercase;box-shadow:0 2px 12px rgba(0,0,0,0.28);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}",
      ".gtd-pref-toggle button{background:transparent;color:inherit;border:0;padding:6px 14px;cursor:pointer;border-radius:999px;letter-spacing:inherit;text-transform:inherit;font:inherit;transition:background .15s ease,color .15s ease;}",
      ".gtd-pref-toggle button.active{background:#fff;color:#111;}",
      ".gtd-pref-toggle button:not(.active):hover{background:rgba(255,255,255,0.12);}",
      ".gtd-pref-toggle button:focus-visible{outline:2px solid #fff;outline-offset:2px;}",
      ".gtd-pref-banner{position:fixed;top:64px;right:16px;z-index:9998;background:#111;color:#fff;border-radius:10px;padding:14px 16px;max-width:340px;box-shadow:0 8px 24px rgba(0,0,0,0.32);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45;animation:gtd-slide-in .35s cubic-bezier(0.23,1,0.32,1);}",
      ".gtd-pref-banner p{margin:0 0 10px;}",
      ".gtd-pref-banner-actions{display:flex;gap:8px;}",
      ".gtd-pref-banner button{flex:1;padding:7px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.22);background:transparent;color:#fff;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;}",
      ".gtd-pref-banner button.primary{background:#fff;color:#111;border-color:#fff;}",
      ".gtd-pref-banner button:hover{opacity:0.88;}",
      "@keyframes gtd-slide-in{from{opacity:0;transform:translateY(-8px);}to{opacity:1;transform:translateY(0);}}",
      "@media (max-width:640px){.gtd-pref{top:8px;right:8px;}.gtd-pref-banner{right:8px;top:48px;max-width:calc(100% - 16px);}}",
      // When the day-nav is mounted, drop the pref toggle below it so the 'next →' button isn't hidden.
      "body.gtd-has-daynav .gtd-pref{top:54px;}",
      "body.gtd-has-daynav .gtd-pref-banner{top:102px;}",
      "@media (max-width:640px){body.gtd-has-daynav .gtd-pref{top:44px;}body.gtd-has-daynav .gtd-pref-banner{top:84px;}}",
      // Editorial overrides: shrink lede/analysis copy next to headlines by one step.
      ".spread .lede{font-size:1.15rem !important;line-height:1.55 !important;}",
      ".spread .story-meta{font-size:0.9rem !important;letter-spacing:0.22em !important;}",
      "@media (min-width:900px){.spread .lede{font-size:1.2rem !important;}}",
      // Day navigation bar (prev/next daily page). Sticks to top of page.
      ".gtd-daynav{position:sticky;top:0;z-index:9997;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 16px;background:rgba(15,15,15,0.9);color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:0.08em;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid rgba(255,255,255,0.08);}",
      ".gtd-daynav-btn{display:inline-flex;align-items:center;padding:6px 14px;border-radius:999px;text-decoration:none;color:#fff;background:rgba(255,255,255,0.08);transition:background .15s ease,color .15s ease;white-space:nowrap;}",
      ".gtd-daynav-btn:hover:not(.disabled){background:#fff;color:#111;}",
      ".gtd-daynav-btn.disabled{opacity:0.32;cursor:default;pointer-events:none;background:transparent;border:1px solid rgba(255,255,255,0.12);}",
      ".gtd-daynav-label{font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;letter-spacing:0.16em;opacity:0.78;text-transform:uppercase;}",
      "@media (max-width:640px){.gtd-daynav{font-size:12px;padding:8px 12px;}.gtd-daynav-btn{padding:5px 10px;}.gtd-daynav-label{display:none;}}"
    ].join("");
    var style = document.createElement("style");
    style.setAttribute("data-gtd-pref", "1");
    style.textContent = css;
    document.head.appendChild(style);
  }

  function updateToggleActive(pref) {
    var buttons = document.querySelectorAll(".gtd-pref-toggle button");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (b.dataset.choice === pref) b.classList.add("active");
      else b.classList.remove("active");
    }
  }

  function onToggleClick(choice) {
    setPref(choice);
    var view = detectView();
    if (view === "calendar") {
      updateCalendarLinks(choice);
      updateCrossEditionLinks(choice);
      updateToggleActive(choice);
      return;
    }
    if (view !== choice) {
      location.href = siblingUrl(choice);
    } else {
      updateToggleActive(choice);
      updateCalendarLinks(choice);
      updateCrossEditionLinks(choice);
      var banner = document.querySelector(".gtd-pref-banner");
      if (banner) banner.remove();
    }
  }

  function mountToggle() {
    var view = detectView();
    var pref = getPref();
    var active = pref || (view === "classic" ? "classic" : "morning");

    var wrap = document.createElement("div");
    wrap.className = "gtd-pref";
    wrap.innerHTML =
      '<div class="gtd-pref-toggle" role="group" aria-label="Style preference">' +
        '<button type="button" data-choice="classic" aria-label="Classic view">Classic</button>' +
        '<button type="button" data-choice="morning" aria-label="Morning edition">Morning</button>' +
      '</div>';
    document.body.appendChild(wrap);
    updateToggleActive(active);

    var buttons = wrap.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () { onToggleClick(btn.dataset.choice); });
      })(buttons[i]);
    }
  }

  function mountBanner() {
    var view = detectView();
    if (view !== "morning" && view !== "classic") return;
    var pref = getPref();
    if (!pref || pref === view) return;

    var prefName = pref === "morning" ? "Morning Edition" : "classic view";
    var banner = document.createElement("div");
    banner.className = "gtd-pref-banner";
    banner.setAttribute("role", "status");
    banner.innerHTML =
      '<p>You prefer the <strong>' + prefName + '</strong>. Switch to it?</p>' +
      '<div class="gtd-pref-banner-actions">' +
        '<button type="button" class="primary" data-action="switch">Switch</button>' +
        '<button type="button" data-action="stay">Stay here</button>' +
      '</div>';
    document.body.appendChild(banner);
    banner.querySelector('[data-action="switch"]').addEventListener("click", function () {
      location.href = siblingUrl(pref);
    });
    banner.querySelector('[data-action="stay"]').addEventListener("click", function () {
      banner.remove();
    });
  }

  // Rewrite calendar date links (<a data-date="YYYY-MM-DD" href="...">) to point
  // at the preferred style. Leaves non-date links untouched.
  function updateCalendarLinks(pref) {
    var links = document.querySelectorAll('a[data-date]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var href = a.getAttribute("href");
      if (!href) continue;
      var base = href.replace(/(?:index\.html|classic\.html)$/, "").replace(/\/$/, "");
      if (pref === "classic") {
        a.setAttribute("href", base + "/classic.html");
      } else {
        a.setAttribute("href", base + "/");
      }
    }
  }

  // Rewrite cross-edition links (GH → HN or HN → GH) so clicking them lands on
  // the user's preferred style on the other edition's daily page.
  function updateCrossEditionLinks(pref) {
    var curPath = location.pathname;
    var curIsHn = /(^|\/)hn\//.test(curPath);
    var anchors = document.querySelectorAll('a[href]');
    for (var i = 0; i < anchors.length; i++) {
      var a = anchors[i];
      // Skip links with data-date (handled separately) and internal hash links.
      if (a.hasAttribute("data-date")) continue;
      var href = a.getAttribute("href");
      if (!href || href.charAt(0) === "#") continue;
      var resolved;
      try { resolved = new URL(href, location.href).pathname; } catch (e) { continue; }
      var targetIsHn = /(^|\/)hn\//.test(resolved);
      if (targetIsHn === curIsHn) continue; // same edition or calendar-within-edition
      var m = resolved.match(/\/(\d{4}-\d{2}-\d{2})\/(?:index\.html|classic\.html)?$/);
      if (!m) continue;
      // Preserve the original prefix style (../ or ../../) by working off href, not resolved.
      var prefix = href.match(/^((?:\.\.\/)+|\/)/);
      prefix = prefix ? prefix[0] : "";
      // Rebuild href: take relative path minus style suffix, append preferred suffix.
      var relTail = href
        .replace(/^((?:\.\.\/)+|\/)/, "")
        .replace(/(?:index\.html|classic\.html)$/, "")
        .replace(/\/$/, "");
      var suffix = pref === "classic" ? "/classic.html" : "/";
      a.setAttribute("href", prefix + relTail + suffix);
    }
  }

  // Locate dates.json relative to preference.js's own <script src="...">.
  // This works across all page depths without hardcoding relative paths.
  function datesManifestUrl() {
    var scripts = document.querySelectorAll('script[src]');
    for (var i = 0; i < scripts.length; i++) {
      var src = scripts[i].src || "";
      if (/(^|\/)preference\.js(\?|$)/.test(src)) {
        try { return new URL("dates.json", src).href; } catch (e) { return null; }
      }
    }
    return null;
  }

  function renderDayNav(edition, current, prev, next, pref) {
    // `pref` (or current view) determines the style suffix appended to nav hrefs.
    var styleSuffix = pref === "classic" ? "classic.html" : "";
    function btn(dateStr, direction, fallback) {
      var arrow = direction === "prev" ? "\u2190" : "\u2192";
      var label = direction === "prev" ? (arrow + " " + (dateStr || fallback)) : ((dateStr || fallback) + " " + arrow);
      if (!dateStr) {
        return '<span class="gtd-daynav-btn ' + direction + ' disabled" aria-disabled="true">' + label + "</span>";
      }
      var href = "../" + dateStr + "/" + styleSuffix;
      return '<a class="gtd-daynav-btn ' + direction + '" href="' + href + '" data-date="' + dateStr + '">' + label + "</a>";
    }
    var nav = document.createElement("nav");
    nav.className = "gtd-daynav";
    nav.setAttribute("aria-label", "Day navigation");
    nav.dataset.gtdEdition = edition;
    nav.dataset.gtdDate = current;
    nav.innerHTML =
      btn(prev, "prev", "earlier") +
      '<span class="gtd-daynav-label">' + current + "</span>" +
      btn(next, "next", "later");
    return nav;
  }

  function mountDayNav() {
    var body = document.body;
    var edition = body.dataset.gtdEdition;
    var current = body.dataset.gtdDate;
    if (!edition || !current) return;
    if (document.querySelector(".gtd-daynav")) return; // avoid double-mount
    var url = datesManifestUrl();
    if (!url) return;
    fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("manifest fetch failed");
      return r.json();
    }).then(function (manifest) {
      var all = manifest[edition] || [];
      var idx = all.indexOf(current);
      var prev = "", next = "";
      if (idx >= 0) {
        if (idx > 0) prev = all[idx - 1];
        if (idx < all.length - 1) next = all[idx + 1];
      } else {
        // Current day isn't in manifest (e.g., stale cache or unlisted page) —
        // fall back to nearest neighbours by date comparison.
        for (var i = 0; i < all.length; i++) {
          if (all[i] < current) prev = all[i];
          else if (all[i] > current && !next) { next = all[i]; break; }
        }
      }
      var pref = getPref();
      var view = detectView();
      var style = pref || (view === "classic" ? "classic" : "morning");
      var nav = renderDayNav(edition, current, prev, next, style);
      body.insertBefore(nav, body.firstChild);
      body.classList.add("gtd-has-daynav");
    }).catch(function () {});
  }

  function boot() {
    if (document.querySelector(".gtd-pref")) return;
    injectCSS();
    mountToggle();
    mountDayNav();
    var view = detectView();
    var pref = getPref();
    if (pref) {
      updateCalendarLinks(pref);
      updateCrossEditionLinks(pref);
    }
    if (view !== "calendar") mountBanner();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
