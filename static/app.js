document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-home-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.homeTarget;
      document.querySelectorAll(".lc-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".home-tab-panel").forEach((p) => p.classList.remove("active"));
      const panel = document.getElementById(target);
      if (panel) panel.classList.add("active");
    });
  });

  // Countdown leve apenas fora da página do leilão.
  // O leilão tem controlador próprio em auction.html.
  if (document.getElementById("auction-layout")) return;

  const countdowns = Array.from(
    document.querySelectorAll(".countdown[data-seconds]")
  );

  if (!countdowns.length) return;

  function formatTime(seconds) {
    seconds = Math.max(0, Number.parseInt(seconds || "0", 10) || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return h > 0
      ? `${h}h ${String(m).padStart(2, "0")}m`
      : `${m}m ${String(s).padStart(2, "0")}s`;
  }

  function tickCountdowns() {
    countdowns.forEach((el) => {
      let seconds = Math.max(0, Number.parseInt(el.dataset.seconds || "0", 10) || 0);
      el.textContent = formatTime(seconds);
      el.dataset.seconds = String(Math.max(0, seconds - 1));
    });
  }

  tickCountdowns();
  setInterval(tickCountdowns, 1000);
});
