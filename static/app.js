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

    let homeStateInFlight = false;
    let homeStateQueued = false;
    let homeFastSyncUntil = 0;
    let homeLastStateAt = 0;

    function currentSeconds(el) {
      if (!el || el.dataset.homeInactive === "true") return null;
      const endAt = el.dataset.endAt;
      const startAt = el.dataset.startAt;
      const fromDate = secondsFromDate(endAt || startAt);
      if (fromDate !== null) return fromDate;
      return Math.max(0, Number.parseInt(el.dataset.seconds || "0", 10) || 0);
    }

    function homeGrids() {
      const lanes = document.querySelectorAll("#home-active-auctions .auction-lane");
      return {
        live: lanes[0] ? lanes[0].querySelector(".lc-auction-grid") : null,
        upcoming: lanes[1] ? lanes[1].querySelector(".lc-auction-grid") : null,
        ended: document.querySelector("#home-ended-auctions .lc-auction-grid"),
      };
    }

    function cardById(id) {
      return document.querySelector(`[data-auction-card][data-auction-id="${String(id)}"]`);
    }

    function removeEmptyState(grid) {
      if (!grid) return;
      grid.querySelectorAll(":scope > .empty-state").forEach((item) => item.remove());
    }

    function moveCard(card, grid) {
      if (!card || !grid || card.parentElement === grid) return;
      removeEmptyState(grid);
      card.classList.add("home-card-moving");
      grid.appendChild(card);
      window.setTimeout(() => card.classList.remove("home-card-moving"), 260);
    }

    function formatMoney(value) {
      const number = Number(value || 0);
      return `R$ ${number.toFixed(2).replace(".", ",")}`;
    }

    function setBadge(card, status) {
      const badge = card.querySelector(".status-badge");
      if (!badge) return;
      badge.classList.remove("live", "scheduled", "ended");
      if (status === "live") {
        badge.classList.add("live");
        setTextIfChanged(badge, "AO VIVO");
      } else if (status === "scheduled" || status === "relisted") {
        badge.classList.add("scheduled");
        setTextIfChanged(badge, "PRÓXIMO");
      } else {
        badge.classList.add("ended");
        setTextIfChanged(badge, "ENCERRADO");
      }
    }

    function ensureCountdownElement(timerLine) {
      if (!timerLine) return null;
      let strong = timerLine.querySelector("strong");
      if (!strong) {
        strong = document.createElement("strong");
        timerLine.appendChild(strong);
      }
      strong.classList.add("countdown");
      strong.dataset.homeInactive = "false";
      return strong;
    }

    function updateTimer(card, item, status) {
      const timerLine = card.querySelector(".timer-line");
      if (!timerLine) return;
      let label = timerLine.querySelector("span");
      if (!label) {
        label = document.createElement("span");
        timerLine.prepend(label);
      }
      const strong = ensureCountdownElement(timerLine);
      if (!strong) return;

      strong.dataset.homeSyncing = "false";
      card.classList.remove("home-card-syncing");

      if (status === "live") {
        setTextIfChanged(label, "Tempo restante");
        strong.dataset.endAt = item.ends_at || "";
        delete strong.dataset.startAt;
        strong.dataset.seconds = String(Math.max(0, Number.parseInt(item.remaining_seconds || "0", 10) || 0));
        setTextIfChanged(strong, formatTime(currentSeconds(strong) ?? 0));
      } else if (status === "scheduled" || status === "relisted") {
        setTextIfChanged(label, "Começa em");
        strong.dataset.startAt = item.scheduled_start || "";
        delete strong.dataset.endAt;
        strong.dataset.seconds = String(Math.max(0, Number.parseInt(item.start_remaining || "0", 10) || 0));
        setTextIfChanged(strong, formatTime(currentSeconds(strong) ?? 0));
      } else {
        setTextIfChanged(label, "Status");
        strong.dataset.homeInactive = "true";
        delete strong.dataset.endAt;
        delete strong.dataset.startAt;
        delete strong.dataset.seconds;
        setTextIfChanged(strong, "Encerrado");
      }
    }

    function updatePrice(card, item, status) {
      const priceLine = card.querySelector(".price-line");
      if (!priceLine) return;
      const firstBox = priceLine.children[0];
      if (!firstBox) return;
      const label = firstBox.querySelector("span");
      const strong = firstBox.querySelector("strong");
      if (label) {
        setTextIfChanged(label, status === "live" ? "Atual" : (status === "ended" ? "Final" : "Inicial"));
      }
      if (strong) setTextIfChanged(strong, formatMoney(item.current_price));
    }

    function updateButton(card, item, status) {
      const btn = card.querySelector("a.btn");
      if (!btn) return;
      btn.href = `/auction/${item.id}`;
      btn.classList.toggle("ghost", status !== "live");
      setTextIfChanged(btn, status === "live" ? "Participar" : (status === "ended" ? "Ver detalhes" : "Ver produto"));
    }

    function updateCardFromItem(item) {
      if (!item || !item.id) return;
      const card = cardById(item.id);
      if (!card) return;
      const status = (item.status === "pending_payment" ? "ended" : String(item.status || "").toLowerCase()) || "scheduled";
      const grids = homeGrids();

      card.dataset.auctionStatus = status;
      card.classList.toggle("ended", status === "ended");
      setBadge(card, status);
      updatePrice(card, item, status);
      updateTimer(card, item, status);
      updateButton(card, item, status);

      if (status === "live") moveCard(card, grids.live);
      else if (status === "scheduled" || status === "relisted") moveCard(card, grids.upcoming);
      else moveCard(card, grids.ended);
    }

    function updateHomeCounts(json) {
      const live = Number(json.live_count || 0);
      const upcoming = Number(json.upcoming_count || 0);
      const ended = Number(json.ended_count || 0);
      document.querySelectorAll(".market-live strong,.lane-count.live").forEach((el) => setTextIfChanged(el, live));
      document.querySelectorAll(".market-next strong,.lane-count.scheduled").forEach((el) => setTextIfChanged(el, upcoming));
      document.querySelectorAll(".market-ended strong,.lane-count.ended").forEach((el) => setTextIfChanged(el, ended));
    }

    function applyHomeState(json) {
      updateHomeCounts(json);
      [json.live_items, json.upcoming_items, json.ended_items].forEach((items) => {
        if (Array.isArray(items)) items.forEach(updateCardFromItem);
      });
    }

    function requestHomeStateSoon(reason, critical) {
      const now = Date.now();
      if (critical) homeFastSyncUntil = Math.max(homeFastSyncUntil, now + 7000);
      const minGap = critical ? 350 : 1500;
      if (now - homeLastStateAt < minGap) return;
      window.setTimeout(() => refreshHomeState(reason || "timer", Boolean(critical)), critical ? 40 : 120);
    }

    function tickCountdowns() {
      countdowns.forEach((el) => {
        const seconds = currentSeconds(el);
        if (seconds === null) return;
        const card = el.closest("[data-auction-card]");

        if (seconds <= 0) {
          const status = String(card?.dataset.auctionStatus || "").toLowerCase();
          if (status === "scheduled" || status === "relisted") {
            setTextIfChanged(el, "Iniciando...");
          } else if (status === "live") {
            setTextIfChanged(el, "Encerrando...");
          } else {
            setTextIfChanged(el, "Conferindo...");
          }
          el.dataset.homeSyncing = "true";
          if (card) card.classList.add("home-card-syncing");
          requestHomeStateSoon("zero", true);
          return;
        }

        setTextIfChanged(el, formatTime(seconds));
        if (!el.dataset.endAt && !el.dataset.startAt) {
          el.dataset.seconds = String(Math.max(0, seconds - 1));
        }
      });

      if (Date.now() < homeFastSyncUntil) {
        requestHomeStateSoon("fast-sync", true);
      }
    }

    async function refreshHomeState(reason, critical) {
      if (!document.querySelector(".lc-home")) return;
      if (document.hidden && !critical) return;
      if (homeStateInFlight) {
        if (critical) homeStateQueued = true;
        return;
      }
      homeStateInFlight = true;
      homeLastStateAt = Date.now();
      const sent = Date.now();
      try {
        const res = await fetch(`/api/home/state?reason=${encodeURIComponent(reason || "poll")}`, { cache: "no-store" });
        const json = await res.json().catch(() => ({}));
        const received = Date.now();
        if (!res.ok || !json.ok) return;
        syncHomeServerClock(json, sent, received);
        applyHomeState(json);
      } catch (_) {
      } finally {
        homeStateInFlight = false;
        if (homeStateQueued) {
          homeStateQueued = false;
          window.setTimeout(() => refreshHomeState("queued-critical", true), 80);
        }
      }
    }

    tickCountdowns();
    setInterval(tickCountdowns, 1000);
    window.setTimeout(() => refreshHomeState("initial", true), 700);
    setInterval(() => refreshHomeState("poll", false), 12000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) window.setTimeout(() => refreshHomeState("visible", true), 250);
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
