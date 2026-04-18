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
      // Editorial overrides: shrink lede/analysis copy next to headlines by one step.
      ".spread .lede{font-size:1.15rem !important;line-height:1.55 !important;}",
      ".spread .story-meta{font-size:0.9rem !important;letter-spacing:0.22em !important;}",
      "@media (min-width:900px){.spread .lede{font-size:1.2rem !important;}}"
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

  function boot() {
    if (document.querySelector(".gtd-pref")) return;
    injectCSS();
    mountToggle();
    var view = detectView();
    var pref = getPref();
    if (view === "calendar") {
      if (pref) {
        updateCalendarLinks(pref);
        updateCrossEditionLinks(pref);
      }
    } else {
      if (pref) updateCrossEditionLinks(pref);
      mountBanner();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
