(function () {
  "use strict";

  const pageCache = new Map();
  const CACHE_TTL_MS = 30000;
  let activeController = null;
  let countdownTimer = null;

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  }

  function normalizeUrl(href) {
    try {
      const url = new URL(href, window.location.href);
      url.hash = "";
      return url.href;
    } catch (_) {
      return "";
    }
  }

  function isInternalNavigableLink(link) {
    if (!link || !link.href) return false;
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    if (link.dataset.noPrefetch === "true" || link.dataset.noTransition === "true" || link.dataset.noInstant === "true") return false;

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

  function isInstantEligible(linkOrHref) {
    let url;
    try {
      url = new URL(typeof linkOrHref === "string" ? linkOrHref : linkOrHref.href, window.location.href);
    } catch (_) {
      return false;
    }

    if (url.origin !== window.location.origin) return false;
    const path = url.pathname.toLowerCase();

    // A página do leilão possui controlador próprio de tempo real/websocket.
    // Ela continua com navegação normal para não arriscar quebrar lances.
    if (path.startsWith("/auction/")) return false;

    // Páginas com ações críticas ou saída devem continuar no fluxo padrão.
    if (path === "/logout") return false;

    return (
      path === "/" ||
      path === "/login" ||
      path === "/register" ||
      path.startsWith("/minha-conta") ||
      path === "/admin"
    );
  }

  function isSamePageHash(url) {
    return (
      url.origin === window.location.origin &&
      url.pathname === window.location.pathname &&
      url.search === window.location.search &&
      url.hash
    );
  }

  function cacheGet(url) {
    const key = normalizeUrl(url);
    const item = pageCache.get(key);
    if (!item) return null;
    if (Date.now() - item.time > CACHE_TTL_MS) {
      pageCache.delete(key);
      return null;
    }
    return item.html;
  }

  function cacheSet(url, html) {
    const key = normalizeUrl(url);
    if (!key || !html) return;
    pageCache.set(key, { html, time: Date.now() });
    if (pageCache.size > 12) {
      const first = pageCache.keys().next().value;
      if (first) pageCache.delete(first);
    }
  }

  async function fetchPage(url, options = {}) {
    const key = normalizeUrl(url);
    if (!key) throw new Error("URL inválida");

    if (options.preferCache !== false) {
      const cached = cacheGet(key);
      if (cached) return cached;
    }

    const controller = new AbortController();
    if (!options.prefetch) {
      if (activeController) activeController.abort();
      activeController = controller;
    }

    const response = await fetch(key, {
      method: "GET",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        "X-Requested-With": "fetch",
        "X-LC-Navigation": options.prefetch ? "prefetch" : "instant",
      },
      priority: options.prefetch ? "low" : "high",
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const html = await response.text();
    cacheSet(key, html);
    return html;
  }

  function ensureProgressBar() {
    let bar = document.querySelector(".page-progress-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "page-progress-bar";
      document.body.appendChild(bar);
    }
    return bar;
  }

  function setLoadingState(isLoading) {
    const bar = ensureProgressBar();
    document.body.classList.toggle("page-loading", Boolean(isLoading));
    if (isLoading) {
      bar.classList.add("active");
      window.setTimeout(() => bar.classList.add("almost"), 80);
    } else {
      bar.classList.remove("almost");
      bar.classList.add("done");
      window.setTimeout(() => {
        bar.classList.remove("active", "done");
        document.body.classList.remove("page-loading", "page-leaving");
      }, 180);
    }
  }

  function loadMissingHeadAssets(doc) {
    const existing = new Set(
      Array.from(document.querySelectorAll('link[rel="stylesheet"][href]')).map((link) => new URL(link.href, window.location.href).href)
    );

    doc.querySelectorAll('link[rel="stylesheet"][href]').forEach((link) => {
      const href = new URL(link.getAttribute("href"), window.location.href).href;
      if (existing.has(href)) return;
      const clone = document.createElement("link");
      clone.rel = "stylesheet";
      clone.href = href;
      document.head.appendChild(clone);
      existing.add(href);
    });
  }

  function runScripts(container) {
    const scripts = Array.from(container.querySelectorAll("script"));
    scripts.forEach((oldScript) => {
      const src = oldScript.getAttribute("src") || "";
      if (src && src.includes("/static/app.js")) {
        oldScript.remove();
        return;
      }

      const script = document.createElement("script");
      Array.from(oldScript.attributes).forEach((attr) => script.setAttribute(attr.name, attr.value));
      script.textContent = oldScript.textContent || "";
      oldScript.replaceWith(script);
    });
  }

  function updateActiveNav(url) {
    const targetPath = url.pathname.replace(/\/$/, "") || "/";
    document.querySelectorAll(".nav a[href]").forEach((link) => {
      try {
        const linkUrl = new URL(link.href, window.location.href);
        const linkPath = linkUrl.pathname.replace(/\/$/, "") || "/";
        link.classList.toggle("active", linkPath === targetPath);
      } catch (_) {}
    });
  }

  function applyPage(html, href, options = {}) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const nextMain = doc.querySelector("#page-content") || doc.querySelector("main");
    const currentMain = document.querySelector("#page-content") || document.querySelector("main");

    if (!nextMain || !currentMain) {
      window.location.href = href;
      return;
    }

    loadMissingHeadAssets(doc);

    if (doc.title) document.title = doc.title;

    currentMain.classList.add("page-content-out");
    window.setTimeout(() => {
      currentMain.innerHTML = nextMain.innerHTML;
      currentMain.className = nextMain.className || "page-content-shell";
      currentMain.id = nextMain.id || "page-content";
      currentMain.classList.add("page-content-in");

      runScripts(currentMain);

      const url = new URL(href, window.location.href);
      if (options.push !== false) {
        history.pushState({ lcInstant: true, url: url.href }, "", url.href);
      }

      updateActiveNav(url);
      initPageFeatures();

      if (url.hash) {
        const target = document.querySelector(url.hash);
        if (target) window.setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
      } else {
        window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
      }

      window.setTimeout(() => currentMain.classList.remove("page-content-in"), 240);
      setLoadingState(false);
    }, 70);
  }

  function initNavigationPrefetch() {
    function prefetch(link) {
      if (!isInternalNavigableLink(link)) return;
      if (!isInstantEligible(link)) return;

      const url = new URL(link.href, window.location.href);
      if (isSamePageHash(url)) return;

      const key = normalizeUrl(url.href);
      if (cacheGet(key)) return;

      try {
        const hint = document.createElement("link");
        hint.rel = "prefetch";
        hint.href = key;
        hint.as = "document";
        document.head.appendChild(hint);
      } catch (_) {}

      if (window.fetch) {
        window.setTimeout(() => fetchPage(key, { prefetch: true, preferCache: false }).catch(() => {}), 60);
      }
    }

    document.querySelectorAll("a[href]").forEach((link) => {
      if (link.dataset.lcPrefetchBound === "1") return;
      link.dataset.lcPrefetchBound = "1";
      link.addEventListener("pointerenter", () => prefetch(link), { passive: true });
      link.addEventListener("focus", () => prefetch(link), { passive: true });
      link.addEventListener("touchstart", () => prefetch(link), { passive: true });
    });
  }

  function initInstantNavigation() {
    if (document.documentElement.dataset.lcInstantNavigation === "1") return;
    document.documentElement.dataset.lcInstantNavigation = "1";

    document.body.classList.add("page-ready");

    document.addEventListener("click", async (event) => {
      const link = event.target.closest("a[href]");
      if (!isInternalNavigableLink(link)) return;
      if (!isInstantEligible(link)) return;
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const url = new URL(link.href, window.location.href);
      if (isSamePageHash(url)) return;

      event.preventDefault();
      document.body.classList.add("page-leaving");
      setLoadingState(true);

      try {
        const cached = cacheGet(url.href);
        if (cached) {
          applyPage(cached, url.href);
          // Atualiza o cache em segundo plano para a próxima visita.
          fetchPage(url.href, { prefetch: true, preferCache: false }).catch(() => {});
        } else {
          const html = await fetchPage(url.href, { prefetch: false, preferCache: true });
          applyPage(html, url.href);
        }
      } catch (_) {
        window.location.href = url.href;
      }
    }, true);

    window.addEventListener("popstate", async () => {
      const href = window.location.href;
      if (!isInstantEligible(href)) {
        window.location.reload();
        return;
      }
      setLoadingState(true);
      try {
        const html = await fetchPage(href, { prefetch: false, preferCache: true });
        applyPage(html, href, { push: false });
      } catch (_) {
        window.location.reload();
      }
    });

    window.addEventListener("pageshow", () => {
      document.body.classList.remove("page-leaving");
      document.body.classList.add("page-ready");
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
        history.replaceState(history.state, "", `#${target}`);
      }
    }

    buttons.forEach((btn) => {
      if (btn.dataset.lcHomeBound === "1") return;
      btn.dataset.lcHomeBound = "1";
      btn.addEventListener("click", () => openHomePanel(btn.dataset.homeTarget, true));
    });

    const initialHash = (window.location.hash || "").replace("#", "");
    if (initialHash && document.getElementById(initialHash)) {
      openHomePanel(initialHash, false);
    }
  }

  function initCountdowns() {
    if (countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }

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
    countdownTimer = setInterval(tickCountdowns, 1000);
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

  function initPageFeatures() {
    initNavigationPrefetch();
    initHomeTabs();
    initCountdowns();
    initAccountHashHelpers();
  }

  window.LCInitPageFeatures = initPageFeatures;

  onReady(() => {
    initNavigationPrefetch();
    initInstantNavigation();
    initPageFeatures();
  });
})();
