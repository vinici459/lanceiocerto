(function () {
  "use strict";

  const NAV_CACHE_FIX_VERSION = "20260608-nav-cache-v2";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  }

  function isInternalNavigableLink(link) {
    if (!link || !link.href) return false;
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    if (link.dataset.noPrefetch === "true" || link.dataset.noTransition === "true") return false;

    let url;
    try {
      url = new URL(link.href, window.location.href);
    } catch (_) {
      return false;
    }

    if (url.origin !== window.location.origin) return false;
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;

    const path = url.pathname.toLowerCase();
    if (path === "/logout") return false;
    if (path.startsWith("/static/")) return false;
    if (path.startsWith("/api/")) return false;
    if (path.startsWith("/ws/")) return false;

    return true;
  }

  function isSamePageHash(url) {
    return (
      url.origin === window.location.origin &&
      url.pathname === window.location.pathname &&
      url.search === window.location.search &&
      url.hash
    );
  }

  function initNavigationPrefetch() {
    // Prefetch de páginas desativado de propósito.
    // Nos testes em produção, até o hint nativo <link rel="prefetch">
    // gerava requisições extras para /, /login, /admin e /minha-conta,
    // aumentando a carga do backend e piorando a sensação de navegação.
    // A otimização agora fica no backend: páginas mais leves + cache curto.
    return;
  }

  function initPageTransitions() {
    document.body.classList.add("page-ready");

    let lastNavHref = "";
    let lastNavAt = 0;

    document.addEventListener("click", (event) => {
      const link = event.target.closest("a[href]");
      if (!isInternalNavigableLink(link)) return;
      if (link.hasAttribute("data-login-open")) return;
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const url = new URL(link.href, window.location.href);
      if (isSamePageHash(url)) return;

      const now = Date.now();
      if (lastNavHref === url.href && now - lastNavAt < 1200) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      lastNavHref = url.href;
      lastNavAt = now;

      document.body.classList.add("page-leaving");
    }, true);

    window.addEventListener("pageshow", () => {
      document.body.classList.remove("page-leaving");
      document.body.classList.add("page-ready");
      lastNavHref = "";
      lastNavAt = 0;
    });
  }

  function formatTime(seconds) {
    seconds = Math.max(0, Number.parseInt(seconds || "0", 10) || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return h > 0
      ? `${h}h ${String(m).padStart(2, "0")}m`
      : `${m}m ${String(s).padStart(2, "0")}s`;
  }

  function parseServerDate(value) {
    if (!value) return null;
    const normalized = String(value).trim().replace(" ", "T");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function secondsFromDate(value) {
    const date = parseServerDate(value);
    if (!date) return null;
    return Math.max(0, Math.ceil((date.getTime() - Date.now()) / 1000));
  }

  function initHomeTabs() {
    const buttons = document.querySelectorAll("[data-home-target]");
    if (!buttons.length) return;

    function openHomePanel(target, updateHash) {
      const panel = document.getElementById(target);
      if (!panel) return;

      document.querySelectorAll(".lc-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.homeTarget === target);
      });
      document.querySelectorAll(".home-tab-panel").forEach((item) => {
        item.classList.toggle("active", item.id === target);
      });

      if (updateHash && window.history && window.location.hash !== `#${target}`) {
        history.replaceState(null, "", `#${target}`);
      }
    }

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => openHomePanel(btn.dataset.homeTarget, true));
    });

    const initialHash = (window.location.hash || "").replace("#", "");
    if (initialHash && document.getElementById(initialHash)) {
      openHomePanel(initialHash, false);
    }
  }

  function initCountdowns() {
    if (document.getElementById("auction-layout")) return;

    const countdowns = Array.from(document.querySelectorAll(".countdown[data-seconds]"));
    if (!countdowns.length) return;

    function currentSeconds(el) {
      const endAt = el.dataset.endAt;
      const startAt = el.dataset.startAt;
      const fromDate = secondsFromDate(endAt || startAt);
      if (fromDate !== null) return fromDate;
      return Math.max(0, Number.parseInt(el.dataset.seconds || "0", 10) || 0);
    }

    function tickCountdowns() {
      countdowns.forEach((el) => {
        const seconds = currentSeconds(el);
        el.textContent = formatTime(seconds);
        if (!el.dataset.endAt && !el.dataset.startAt) {
          el.dataset.seconds = String(Math.max(0, seconds - 1));
        }
      });
    }

    tickCountdowns();
    setInterval(tickCountdowns, 1000);
  }

  function initBrokenImageFallbacks() {
    const fallback = "/static/lanceio_hero_slide_01.png";
    document.querySelectorAll("img").forEach((img) => {
      if (img.dataset.fallbackBound === "true") return;
      img.dataset.fallbackBound = "true";
      img.addEventListener("error", () => {
        if (img.src && img.src.endsWith(fallback)) return;
        img.src = fallback;
        img.classList.add("image-fallback-applied");
      });
    });
  }

  function initAccountHashHelpers() {
    const balancePanel = document.getElementById("account-balance-panel");
    if (!balancePanel) return;

    function openBalancePanel(scroll) {
      balancePanel.classList.add("active");
      document.querySelectorAll("[data-account-target]").forEach((link) => link.classList.add("active"));
      if (scroll) balancePanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    if (window.location.hash === "#account-balance-panel") {
      window.setTimeout(() => openBalancePanel(true), 120);
    }
  }

  onReady(() => {
    initNavigationPrefetch();
    initPageTransitions();
    initHomeTabs();
    initCountdowns();
    initBrokenImageFallbacks();
    initAccountHashHelpers();
  });
})();
