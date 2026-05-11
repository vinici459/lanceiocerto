document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-home-target]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.homeTarget;
      document.querySelectorAll('.lc-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.home-tab-panel').forEach((p) => p.classList.remove('active'));
      const panel = document.getElementById(target);
      if (panel) panel.classList.add('active');
    });
  });

  function tickCountdowns(){
    document.querySelectorAll('.countdown,[data-seconds]').forEach((el) => {
      if (!el.dataset.seconds) return;
      let seconds = parseInt(el.dataset.seconds || '0', 10);
      if (Number.isNaN(seconds)) seconds = 0;
      seconds = Math.max(0, seconds);
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      el.textContent = h > 0 ? `${h}h ${String(m).padStart(2,'0')}m` : `${m}m ${String(s).padStart(2,'0')}s`;
      el.dataset.seconds = String(Math.max(0, seconds - 1));
    });
  }
  tickCountdowns();
  setInterval(tickCountdowns, 1000);
});
