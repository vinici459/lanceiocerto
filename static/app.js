function bindCountDownElements(){
  document.querySelectorAll("[data-seconds]").forEach(el => {
    if (!el.dataset.bound){
      el.dataset.bound = "1";
      const update = () => {
        let s = parseInt(el.dataset.seconds || "0", 10);
        if (s < 0) s = 0;
        const h = String(Math.floor(s / 3600)).padStart(2, "0");
        const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
        const sec = String(s % 60).padStart(2, "0");
        if ((el.textContent || "").includes("Começa")) {
          el.textContent = `Começa em: ${h}:${m}:${sec}`;
        } else {
          el.textContent = `Tempo restante: ${h}:${m}:${sec}`;
        }
      };
      update();
      setInterval(() => {
        let s = parseInt(el.dataset.seconds || "0", 10);
        if (s > 0){
          el.dataset.seconds = String(s - 1);
          update();
        }
      }, 1000);
    }
  });
}
bindCountDownElements();

(function(){
  if (!window.__AUCTION__) return;
  const auctionId = window.__AUCTION__.id;
  const countdown = document.getElementById("countdown");
  const currentPrice = document.getElementById("current-price");
  const bidsCount = document.getElementById("bids-count");
  const lastBidder = document.getElementById("last-bidder");
  const bidButtons = document.querySelectorAll(".bid-btn");
  const chatMessages = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const statusEl = document.getElementById("auction-status");

  function formatBR(value){
    return Number(value || 0).toFixed(2).replace(".", ",");
  }

  function renderAuction(a){
    if (currentPrice) currentPrice.textContent = `R$ ${formatBR(a.current_price)}`;
    if (bidsCount) bidsCount.textContent = a.bids_count;
    if (lastBidder) lastBidder.textContent = a.last_bidder || "—";
    if (countdown) {
      countdown.dataset.seconds = String(a.remaining_seconds || 0);
      const h = String(Math.floor((a.remaining_seconds || 0) / 3600)).padStart(2, "0");
      const m = String(Math.floor(((a.remaining_seconds || 0) % 3600) / 60)).padStart(2, "0");
      const sec = String((a.remaining_seconds || 0) % 60).padStart(2, "0");
      countdown.textContent = `${h}:${m}:${sec}`;
    }
    if (statusEl) {
      statusEl.textContent = String(a.status || "").toUpperCase();
      statusEl.className = `status ${a.status}`;
    }
    const disabled = a.status !== "live";
    bidButtons.forEach(btn => btn.disabled = disabled);
  }

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/auction/${auctionId}`);
  socket.onmessage = evt => {
    const payload = JSON.parse(evt.data);
    if (payload.type === "auction_update"){
      renderAuction(payload.auction);
    } else if (payload.type === "chat_message" && chatMessages){
      const wrap = document.createElement("div");
      wrap.className = "chat-line";
      wrap.innerHTML = `<strong>${payload.message.author}</strong> <span>${payload.message.created_at}</span><p>${payload.message.text}</p>`;
      chatMessages.appendChild(wrap);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
  };

  bidButtons.forEach(btn => {
    btn.addEventListener("click", async () => {
      const fd = new FormData();
      fd.append("bid_value", btn.dataset.bid);
      try{
        const res = await fetch(`/api/auction/${auctionId}/bid`, { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Falha ao enviar lance.");
        renderAuction(data.auction);
      }catch(err){
        alert(err.message);
      }
    });
  });

  if (chatForm){
    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = chatForm.querySelector('input[name="message"]');
      if (!input.value.trim()) return;
      const fd = new FormData();
      fd.append("message", input.value);
      try{
        const res = await fetch(`/api/auction/${auctionId}/chat`, { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Falha ao enviar mensagem.");
        input.value = "";
      }catch(err){
        alert(err.message);
      }
    });
  }
})();
