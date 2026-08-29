"use strict";

const REPLAY_URL = "./data/demo-replay.json";
const NS = "http://www.w3.org/2000/svg";

const state = {
  replay: null,
  index: 0,
  timer: null,
  points: [],
  chart: { left: 42, right: 820, top: 24, bottom: 264 },
};

const deckState = {
  index: 0,
  hashes: ["#play", "#replay", "#evidence"],
  pointerStart: null,
};

const byId = (id) => document.getElementById(id);

function number(value) {
  return Number.parseFloat(value);
}

function money(value) {
  return number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function compactHash(value) {
  return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : "————————";
}

function quoteText(side) {
  return side ? `${side.price} × ${side.quantity}` : "WITHDRAWN";
}

function createSvg(tag, attributes = {}) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function chartCoordinates(events) {
  const prices = events.map((event) => number(event.reference_price));
  const min = Math.floor(Math.min(...prices) / 5) * 5 - 2;
  const max = Math.ceil(Math.max(...prices) / 5) * 5 + 2;
  const { left, right, top, bottom } = state.chart;
  const span = Math.max(max - min, 1);
  const points = prices.map((price, index) => ({
    x: left + (index / Math.max(events.length - 1, 1)) * (right - left),
    y: bottom - ((price - min) / span) * (bottom - top),
    price,
  }));
  return { points, min, max };
}

function buildChart(events) {
  const { points, min, max } = chartCoordinates(events);
  state.points = points;
  const { left, right, top, bottom } = state.chart;
  const grid = byId("chart-grid");
  grid.replaceChildren();

  for (let i = 0; i <= 4; i += 1) {
    const y = top + (i / 4) * (bottom - top);
    const price = max - (i / 4) * (max - min);
    grid.append(createSvg("path", { d: `M${left} ${y}H${right}` }));
    const label = createSvg("text", { x: 0, y: y + 3 });
    label.textContent = price.toFixed(0);
    grid.append(label);
  }

  for (let i = 0; i <= 6; i += 1) {
    const x = left + (i / 6) * (right - left);
    grid.append(createSvg("path", { d: `M${x} ${top}V${bottom}` }));
  }

  const line = points.map(({ x, y }) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  byId("price-line").setAttribute("points", line);
  byId("price-area").setAttribute(
    "d",
    `M${points[0].x} ${bottom}L${line.replaceAll(" ", "L")}L${points.at(-1).x} ${bottom}Z`,
  );

  const markerLayer = byId("fill-markers");
  markerLayer.replaceChildren();
  events.forEach((event, index) => {
    if (event.fills.length === 0) return;
    const { x, y } = points[index];
    const marker = createSvg("path", {
      class: "fill-marker",
      d: `M${x} ${y + 11}l-4 7h8Z`,
    });
    markerLayer.append(marker);
  });
}

function fillSummary(event) {
  if (event.fills.length === 0) {
    return "没有成交";
  }
  return event.fills.map((fill) => {
    const verb = fill.bot_side === "bid" ? "买入" : "卖出";
    return `${fill.player_id} ${verb} ${fill.quantity} @ ${fill.price}`;
  }).join(" · ");
}

function explainRound(event) {
  const order = event.customer_order;
  if (event.fills.length === 0) {
    const direction = order.side === "buy" ? "买单" : "卖单";
    return `客户是一笔 ${order.kind === "informed" ? "知情" : "普通"}${direction}，但双方报价都没有满足保留价 ${order.reservation_price}，这一轮无人承担库存变化。`;
  }

  const winners = [...new Set(event.fills.map((fill) => fill.player_id))].join("、");
  const bestSide = order.side === "buy" ? "更低的卖价" : "更高的买价";
  if (order.kind === "informed") {
    const movement = number(event.next_reference_price) >= number(event.reference_price) ? "上涨" : "下跌";
    return `${winners} 用${bestSide}拿到知情订单。客户方向与下一轮${movement}一致，因此这笔成交包含逆向选择风险。`;
  }
  return `${winners} 用${bestSide}拿到普通订单。积极报价赢得了流量，但累计库存与费用也随之改变。`;
}

function renderRound(index) {
  const { events, summary } = state.replay;
  const event = events[index];
  state.index = index;
  const point = state.points[index];
  const { top, bottom } = state.chart;
  const order = event.customer_order;
  const accountA = event.accounts_after.A;
  const accountB = event.accounts_after.B;
  const initialWealth = number(summary.initial_wealth);
  const liveScore = (number(accountA.equity) - number(accountB.equity)) / initialWealth;
  const signalCorrect = event.market_signal === (
    number(event.next_reference_price) >= number(event.reference_price) ? "UP" : "DOWN"
  );

  byId("block-label").textContent = String(event.block_seq).padStart(3, "0");
  byId("event-hash").textContent = compactHash(event.event_hash);
  byId("reference-price").textContent = event.reference_price;
  byId("next-price").textContent = event.next_reference_price;
  byId("next-price").className = number(event.next_reference_price) >= number(event.reference_price) ? "positive" : "negative";
  byId("signal").textContent = `${event.market_signal} · ${signalCorrect ? "✓" : "✕"}`;
  byId("signal").className = signalCorrect ? "positive" : "negative";
  byId("customer-order").textContent = `${order.side.toUpperCase()} ${order.quantity} @ ${order.reservation_price}`;
  byId("order-kind").textContent = order.kind.toUpperCase();
  byId("order-kind").className = `pill ${order.kind}`;

  byId("a-bid").textContent = quoteText(event.actions.A.effective.bid);
  byId("a-ask").textContent = quoteText(event.actions.A.effective.ask);
  byId("b-bid").textContent = quoteText(event.actions.B.effective.bid);
  byId("b-ask").textContent = quoteText(event.actions.B.effective.ask);
  byId("fill-result").textContent = fillSummary(event);
  byId("round-explanation").textContent = explainRound(event);

  byId("a-inventory").textContent = accountA.inventory;
  byId("a-equity").textContent = `EQUITY ${money(accountA.equity)}`;
  byId("b-inventory").textContent = accountB.inventory;
  byId("b-equity").textContent = `EQUITY ${money(accountB.equity)}`;
  byId("live-score").textContent = liveScore === 0
    ? "TIED · 0.000%"
    : `${liveScore > 0 ? "A" : "B"} +${Math.abs(liveScore * 100).toFixed(3)}%`;
  byId("live-score").style.color = liveScore >= 0 ? "var(--cyan)" : "var(--coral)";
  const normalizedScore = Math.max(-.08, Math.min(.08, liveScore));
  byId("score-indicator").style.left = `${50 + (normalizedScore / .08) * 48}%`;

  byId("chart-cursor").setAttribute("x1", point.x);
  byId("chart-cursor").setAttribute("x2", point.x);
  byId("chart-cursor").setAttribute("y1", top);
  byId("chart-cursor").setAttribute("y2", bottom);
  byId("chart-point").setAttribute("cx", point.x);
  byId("chart-point").setAttribute("cy", point.y);

  byId("round-slider").value = index;
  byId("round-output").value = `${String(index + 1).padStart(2, "0")} / ${events.length}`;
}

function stopPlayback() {
  if (state.timer !== null) {
    window.clearInterval(state.timer);
    state.timer = null;
  }
  byId("play-replay").innerHTML = "<span>▶</span> PLAY";
  byId("play-replay").setAttribute("aria-label", "播放回放");
}

function togglePlayback() {
  if (state.timer !== null) {
    stopPlayback();
    return;
  }
  byId("play-replay").innerHTML = "<span>Ⅱ</span> PAUSE";
  byId("play-replay").setAttribute("aria-label", "暂停回放");
  state.timer = window.setInterval(() => {
    const next = state.index + 1;
    if (next >= state.replay.events.length) {
      stopPlayback();
      return;
    }
    renderRound(next);
  }, 650);
}

function bindControls() {
  byId("round-slider").addEventListener("input", (event) => {
    stopPlayback();
    renderRound(Number.parseInt(event.target.value, 10));
  });
  byId("prev-round").addEventListener("click", () => {
    stopPlayback();
    renderRound(Math.max(0, state.index - 1));
  });
  byId("next-round").addEventListener("click", () => {
    stopPlayback();
    renderRound(Math.min(state.replay.events.length - 1, state.index + 1));
  });
  byId("play-replay").addEventListener("click", togglePlayback);
}

function showReplayError(error) {
  const consoleNode = document.querySelector(".replay-console");
  consoleNode.dataset.state = "error";
  byId("match-id").textContent = "REPLAY LOAD FAILED";
  byId("verified-label").textContent = "ERROR";
  byId("round-explanation").textContent = `无法读取演示回放：${error.message}`;
}

function slideFromHash() {
  const index = deckState.hashes.indexOf(window.location.hash);
  return index === -1 ? 0 : index;
}

function goToSlide(rawIndex, { writeHistory = false } = {}) {
  const slides = [...document.querySelectorAll(".slide")];
  const index = Math.max(0, Math.min(slides.length - 1, rawIndex));
  deckState.index = index;
  document.body.dataset.activeSlide = String(index);
  document.querySelector(".deck").style.transform = `translate3d(-${index * 100}vw, 0, 0)`;

  slides.forEach((slide, slideIndex) => {
    const active = slideIndex === index;
    slide.toggleAttribute("inert", !active);
    slide.setAttribute("aria-hidden", String(!active));
  });
  document.querySelectorAll("nav [data-goto], .deck-controls [data-goto]").forEach((button) => {
    if (Number(button.dataset.goto) === index) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });

  byId("deck-prev").disabled = index === 0;
  byId("deck-next").disabled = index === slides.length - 1;
  byId("slide-count").textContent = `${String(index + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;
  if (index !== 1) stopPlayback();

  const nextHash = deckState.hashes[index];
  if (writeHistory && window.location.hash !== nextHash) {
    window.history.pushState({ slide: index }, "", nextHash);
  } else if (!window.location.hash) {
    window.history.replaceState({ slide: index }, "", nextHash);
  }
}

function bindDeckNavigation() {
  document.querySelectorAll("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => goToSlide(Number(button.dataset.goto), { writeHistory: true }));
  });
  byId("deck-prev").addEventListener("click", () => goToSlide(deckState.index - 1, { writeHistory: true }));
  byId("deck-next").addEventListener("click", () => goToSlide(deckState.index + 1, { writeHistory: true }));

  const syncFromHistory = () => goToSlide(slideFromHash());
  window.addEventListener("hashchange", syncFromHistory);
  window.addEventListener("popstate", syncFromHistory);
  window.addEventListener("keydown", (event) => {
    if (["INPUT", "BUTTON"].includes(document.activeElement?.tagName)) return;
    if (event.key === "ArrowLeft") goToSlide(deckState.index - 1, { writeHistory: true });
    if (event.key === "ArrowRight") goToSlide(deckState.index + 1, { writeHistory: true });
  });

  const deck = document.querySelector(".deck");
  deck.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, input")) return;
    deckState.pointerStart = event.clientX;
  });
  deck.addEventListener("pointerup", (event) => {
    if (deckState.pointerStart === null) return;
    const distance = event.clientX - deckState.pointerStart;
    deckState.pointerStart = null;
    if (Math.abs(distance) < 55) return;
    goToSlide(deckState.index + (distance < 0 ? 1 : -1), { writeHistory: true });
  });

  goToSlide(slideFromHash());
}

async function loadReplay() {
  try {
    const response = await fetch(REPLAY_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.replay = await response.json();
    const { manifest, summary, events } = state.replay;
    if (manifest.ruleset !== "blockmarket-v1-prototype.2" || events.length === 0) {
      throw new Error("unexpected replay format");
    }

    document.querySelector(".replay-console").dataset.state = "ready";
    byId("match-id").textContent = manifest.match_id.toUpperCase();
    byId("verified-label").textContent = "VERIFIER PASSED";
    byId("round-slider").max = String(events.length - 1);
    byId("round-output").value = `01 / ${events.length}`;
    byId("event-hash").textContent = compactHash(summary.final_event_hash);
    buildChart(events);
    bindControls();
    renderRound(0);
  } catch (error) {
    showReplayError(error);
  }
}

bindDeckNavigation();
loadReplay();
