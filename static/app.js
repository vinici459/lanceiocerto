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

    const progress = document.createElement("div");
    progress.className = "page-progress-bar";
    progress.setAttribute("aria-hidden", "true");
    document.body.appendChild(progress);

    let lastNavHref = "";
    let lastNavAt = 0;
    let almostTimer = 0;

    function clearProgressTimers() {
      if (almostTimer) window.clearTimeout(almostTimer);
      almostTimer = 0;
    }

    function startNavigationFeedback(url) {
      document.body.classList.add("page-leaving", "page-loading");
      document.querySelectorAll(".nav a").forEach((item) => {
        try {
          const itemUrl = new URL(item.href, window.location.href);
          item.classList.toggle("active", itemUrl.pathname === url.pathname);
        } catch (_) {}
      });
      progress.classList.remove("done", "almost");
      progress.classList.add("active");
      clearProgressTimers();
      almostTimer = window.setTimeout(() => progress.classList.add("almost"), 450);
    }

    function finishNavigationFeedback() {
      clearProgressTimers();
      document.body.classList.remove("page-leaving", "page-loading");
      document.body.classList.add("page-ready");
      progress.classList.add("done");
      window.setTimeout(() => progress.classList.remove("active", "almost", "done"), 220);
      lastNavHref = "";
      lastNavAt = 0;
    }

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

      startNavigationFeedback(url);
    }, true);

    window.addEventListener("pageshow", finishNavigationFeedback);
    window.addEventListener("pagehide", clearProgressTimers);
  }

  function initImageFallbacks() {
    const fallback = "/static/lanceio_hero_slide_01.png";
    document.querySelectorAll("img").forEach((img) => {
      if (img.dataset.safeFallbackBound === "true") return;
      img.dataset.safeFallbackBound = "true";
      img.addEventListener("error", () => {
        if (img.src.includes(fallback)) return;
        img.src = fallback;
      });
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

  function setTextIfChanged(el, value) {
    if (!el) return;
    const text = String(value ?? "");
    if (el.textContent !== text) el.textContent = text;
  }

  let lcServerClockOffsetMs = 0;
  let lcServerClockSynced = false;

  function syncHomeServerClock(payload, sentMs, receivedMs) {
    if (!payload) return;
    let serverMs = Number(payload.server_time_ms || 0);
    if (!serverMs && payload.server_time) {
      const parsed = Date.parse(parseServerDate(payload.server_time) || "");
      if (!Number.isNaN(parsed)) serverMs = parsed;
    }
    if (!serverMs) return;
    const referenceMs = sentMs && receivedMs && receivedMs >= sentMs
      ? Math.round((sentMs + receivedMs) / 2)
      : Date.now();
    const nextOffsetMs = serverMs - referenceMs;
    if (!lcServerClockSynced || Math.abs(nextOffsetMs - lcServerClockOffsetMs) > 1500) {
      lcServerClockOffsetMs = nextOffsetMs;
      lcServerClockSynced = true;
    } else {
      lcServerClockOffsetMs = Math.round((lcServerClockOffsetMs * 0.85) + (nextOffsetMs * 0.15));
    }
  }

  function homeServerNowMs() {
    return Date.now() + lcServerClockOffsetMs;
  }

  function parseServerDate(value) {
    if (!value) return null;
    let normalized = String(value).trim().replace(" ", "T");
    // Datas do backend são UTC. Sem timezone, alguns navegadores interpretam
    // como horário local e a vitrine pode mostrar horas a mais/menos.
    if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized)) normalized += "Z";
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function secondsFromDate(value) {
    const date = parseServerDate(value);
    if (!date) return null;
    return Math.max(0, Math.ceil((date.getTime() - homeServerNowMs()) / 1000));
  }

  // Relógio inicial enviado pelo backend para a Home. Em seguida o polling
  // abaixo corrige com compensação de latência.
  if (Number(window.__LC_SERVER_TIME_MS || 0) > 0) {
    lcServerClockOffsetMs = Number(window.__LC_SERVER_TIME_MS || 0) - Date.now();
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

    let homeRefreshCheckScheduled = false;
    let homeHardReloadScheduled = false;

    function currentSeconds(el) {
      const endAt = el.dataset.endAt;
      const startAt = el.dataset.startAt;
      const fromDate = secondsFromDate(endAt || startAt);
      if (fromDate !== null) return fromDate;
      return Math.max(0, Number.parseInt(el.dataset.seconds || "0", 10) || 0);
    }

    function markCardChecking(el) {
      const card = el?.closest?.("[data-auction-card]");
      if (!card) return;
      card.classList.add("home-card-checking");
      const badge = card.querySelector(".status-badge");
      if (badge && !badge.dataset.originalText) badge.dataset.originalText = badge.textContent || "";
      if (badge) badge.textContent = "CONFERINDO";
    }

    function reloadHomeSoftly(reason) {
      if (homeHardReloadScheduled) return;
      homeHardReloadScheduled = true;
      document.body.classList.add("home-soft-refreshing");
      window.setTimeout(() => window.location.reload(), reason === "zero" ? 1200 : 950);
    }

    function scheduleHomeRefresh(reason, el) {
      if (el) {
        markCardChecking(el);
        setTextIfChanged(el, "Conferindo...");
      }
      if (homeRefreshCheckScheduled) return;
      homeRefreshCheckScheduled = true;
      window.setTimeout(async () => {
        homeRefreshCheckScheduled = false;
        await refreshHomeState(true, reason);
      }, reason === "zero" ? 650 : 900);
    }

    function tickCountdowns() {
      countdowns.forEach((el) => {
        const seconds = currentSeconds(el);
        if (seconds <= 0) {
          setTextIfChanged(el, "Conferindo...");
          scheduleHomeRefresh("zero", el);
          return;
        }
        setTextIfChanged(el, formatTime(seconds));
        if (!el.dataset.endAt && !el.dataset.startAt) {
          el.dataset.seconds = String(Math.max(0, seconds - 1));
        }
      });
    }

    let homeStateInFlight = false;

    async function refreshHomeState(force = false, reason = "timer") {
      if (!document.querySelector(".lc-home")) return;
      if ((!force && document.hidden) || homeStateInFlight) return;
      homeStateInFlight = true;
      const sent = Date.now();
      try {
        const res = await fetch("/api/home/state", { cache: "no-store" });
        const json = await res.json().catch(() => ({}));
        const received = Date.now();
        if (!res.ok || !json.ok) return;
        syncHomeServerClock(json, sent, received);

        const currentLive = document.querySelectorAll("#home-active-auctions .status-badge.live").length;
        const currentUpcoming = document.querySelectorAll("#home-active-auctions .status-badge.scheduled").length;
        const nextLive = Number.isFinite(Number(json.live_count))
          ? Number(json.live_count)
          : (Array.isArray(json.live_items) ? json.live_items.length : currentLive);
        const nextUpcoming = Number.isFinite(Number(json.upcoming_count))
          ? Number(json.upcoming_count)
          : (Array.isArray(json.upcoming_items) ? json.upcoming_items.length : currentUpcoming);

        const visibleLiveIds = Array.from(document.querySelectorAll("#home-active-auctions .status-badge.live"))
          .map((badge) => Number(badge.closest("[data-auction-card]")?.dataset.auctionId || 0))
          .filter(Boolean);
        const visibleUpcomingIds = Array.from(document.querySelectorAll("#home-active-auctions .status-badge.scheduled"))
          .map((badge) => Number(badge.closest("[data-auction-card]")?.dataset.auctionId || 0))
          .filter(Boolean);
        const nextLiveIds = Array.isArray(json.live_ids) ? json.live_ids.map(Number).filter(Boolean) : visibleLiveIds;
        const nextUpcomingIds = Array.isArray(json.upcoming_ids) ? json.upcoming_ids.map(Number).filter(Boolean) : visibleUpcomingIds;
        const idsChanged =
          visibleLiveIds.join(",") !== nextLiveIds.join(",") ||
          visibleUpcomingIds.join(",") !== nextUpcomingIds.join(",");

        if (currentLive !== nextLive || currentUpcoming !== nextUpcoming || idsChanged) {
          reloadHomeSoftly(reason);
        }
      } catch (_) {
      } finally {
        homeStateInFlight = false;
      }
    }

    tickCountdowns();
    setInterval(tickCountdowns, 1000);
    window.setTimeout(refreshHomeState, 2500);
    setInterval(refreshHomeState, 15000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) window.setTimeout(refreshHomeState, 600);
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
    initImageFallbacks();
    initHomeTabs();
    initCountdowns();
    initAccountHashHelpers();
  });
})();
