/* ===========================================================
   Hallelujah Coffee — menu, giỏ hàng, chốt đơn, trợ lý AI (SSE)
   =========================================================== */

const $ = (sel, root = document) => root.querySelector(sel);

const vnd = (n) => Number(n).toLocaleString("vi-VN") + "đ";

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const asset = (itemId) => `static/assets/${itemId}.png`;

const WINDOW = 5;

const state = {
  sid:
    localStorage.sid ||
    (localStorage.sid = crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()),
  menu: null,
  bestsellers: new Set(),
  category: "all",
  query: "",
  list: [],
  focus: 0,
  start: 0,
  size: "M",
  toppings: new Set(),
  cart: { lines: [], total: 0, total_text: "0đ", count: 0 },
  busy: false,
  lastFailed: null,
};

/* ---------- API ---------- */

async function api(path, body) {
  const options = body
    ? {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sid, ...body }),
      }
    : undefined;

  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || "Có lỗi xảy ra");
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------- pricing ---------- */

function toppingsFor(item) {
  return state.menu.toppings.filter((t) => t.categories.includes(item.category));
}

function linePrice(item, size, toppingIds) {
  const sizeSurcharge = item.sized && size === "L" ? state.menu.sizes.L : 0;
  const toppingTotal = [...toppingIds].reduce((sum, id) => {
    const t = state.menu.toppings.find((x) => x.id === id);
    return sum + (t ? t.price : 0);
  }, 0);
  return item.price + sizeSurcharge + toppingTotal;
}

/* ---------- menu list ---------- */

function buildList() {
  const q = state.query.trim().toLowerCase();
  state.list = state.menu.items.filter((item) => {
    const inCategory = state.category === "all" || item.category === state.category;
    const inQuery = !q || item.name.toLowerCase().includes(q) || item.description.toLowerCase().includes(q);
    return inCategory && inQuery;
  });
  if (state.focus >= state.list.length) state.focus = 0;
}

function currentItem() {
  return state.list[state.focus];
}

/* ---------- render: navigation + categories ---------- */

function renderNav() {
  const nav = $("#site-nav");
  nav.innerHTML = Object.entries(state.menu.categories)
    .map(
      ([id, name]) =>
        `<a href="#menu" data-category="${id}" ${state.category === id ? 'aria-current="true"' : ""}>${esc(name)}</a>`
    )
    .join("");

  const chips = $("#cat-chips");
  const all = `<button type="button" class="chip" data-category="all" aria-pressed="${state.category === "all"}">Tất cả</button>`;
  chips.innerHTML =
    all +
    Object.entries(state.menu.categories)
      .map(
        ([id, name]) =>
          `<button type="button" class="chip" data-category="${id}" aria-pressed="${
            state.category === id
          }">${esc(name)}</button>`
      )
      .join("");

  $("#menu-count").textContent = `${state.list.length} / ${state.menu.items.length} món`;
}

/* ---------- render: product cards ---------- */

function cardHTML(item, index) {
  const best = state.bestsellers.has(item.id)
    ? `<p class="best"><i class="ph ph-star" aria-hidden="true"></i>Bán chạy</p>`
    : "";
  return `
    <article class="card swap" tabindex="0" data-index="${index}" aria-label="${esc(item.name)}">
      <div class="card__media">
        <img src="${asset(item.id)}" alt="${esc(item.name)}" width="178" height="178" loading="lazy" decoding="async">
      </div>
      <div class="card__panel">
        <h3 class="card__name">${esc(item.name)}</h3>
        <dl class="card__meta">
          <div><dt>Caffeine</dt><dd>${item.caffeine ? "Có" : "Không"}</dd></div>
          <div><dt>Dị ứng</dt><dd>${item.allergens.length ? esc(item.allergens.join(", ")) : "Chưa ghi nhận"}</dd></div>
        </dl>
        <div class="card__foot">
          <div>
            ${best}
            <p class="card__price">${vnd(item.price)}</p>
          </div>
          <button type="button" class="add-btn" data-add="${item.id}" aria-label="Thêm ${esc(item.name)}">
            <i class="ph ph-plus" aria-hidden="true"></i>
          </button>
        </div>
      </div>
    </article>`;
}

function featuredHTML(item, index) {
  const best = state.bestsellers.has(item.id)
    ? `<span class="featured__best"><i class="ph ph-star" aria-hidden="true"></i>Bán chạy</span>`
    : "";

  const sizes = item.sized
    ? `<div class="row">
         <span class="row__label">Size</span>
         <div class="size-group">
           <button type="button" class="size" data-size="M" aria-pressed="${state.size === "M"}">M</button>
           <button type="button" class="size" data-size="L" aria-pressed="${state.size === "L"}">L +${vnd(
             state.menu.sizes.L
           )}</button>
         </div>
       </div>`
    : "";

  const options = toppingsFor(item);
  const pickedLabels = options.filter((t) => state.toppings.has(t.id)).map((t) => t.name);
  const toppingLabel = pickedLabels.length ? pickedLabels.join(", ") : "Không thêm";
  const toppingButton = options.length
    ? `<div class="row">
         <span class="row__label">Topping</span>
         <button type="button" class="topping-select" data-action="toppings" aria-expanded="false" aria-controls="topping-pop">
           <span>${esc(toppingLabel)}</span><i class="ph ph-caret-down" aria-hidden="true"></i>
         </button>
       </div>`
    : "";

  const popover = options.length
    ? `<div class="topping-pop" id="topping-pop" hidden>
         <p class="topping-pop__title">Topping cho ${esc(item.name)}</p>
         ${options
           .map(
             (t) => `
           <button type="button" class="topping-opt" data-topping="${t.id}" aria-pressed="${state.toppings.has(t.id)}">
             <span class="topping-opt__tick"><i class="ph ph-check" aria-hidden="true"></i></span>
             <span style="flex:1">${esc(t.name)}</span>
             <em>+${vnd(t.price)}</em>
           </button>`
           )
           .join("")}
       </div>`
    : "";

  return `
    <article class="featured" data-index="${index}" aria-label="${esc(item.name)}, đang chọn">
      <div class="featured__media">
        <img class="swap" src="${asset(item.id)}" alt="${esc(item.name)}" width="252" height="252" loading="lazy" decoding="async">
      </div>
      <div class="featured__panel swap">
        <div class="featured__head">
          <h3 class="featured__name">${esc(item.name)}</h3>
          ${best}
        </div>
        <p class="featured__desc">${esc(item.description)}</p>
        <div class="featured__rows">
          ${sizes}
          ${toppingButton}
        </div>
        <div class="featured__total">
          <span class="featured__price">${vnd(linePrice(item, state.size, state.toppings))}</span>
          <span class="featured__size-label">${item.sized ? "Size " + state.size : "1 phần"}</span>
        </div>
        <button type="button" class="featured__add" data-action="add-featured">Thêm vào giỏ</button>
        ${popover}
      </div>
    </article>`;
}

function renderRail() {
  const rail = $("#rail");
  const list = state.list;

  if (!list.length) {
    rail.innerHTML = `<p class="rail-empty">Không có món phù hợp bộ lọc này.</p>`;
    return;
  }

  // hiển thị toàn bộ món trong bộ lọc; dải thẻ cuộn ngang khi không đủ chỗ
  const cells = list.map((item, index) => ({ index, item }));

  rail.innerHTML = cells
    .map(({ index, item }) => (index === state.focus ? featuredHTML(item, index) : cardHTML(item, index)))
    .join("");

  centerFeatured();
}

// đưa thẻ đang chọn vào giữa dải (chỉ cuộn trong dải, không cuộn cả trang)
function centerFeatured() {
  const rail = $("#rail");
  const f = rail.querySelector(".featured");
  if (!f || rail.scrollWidth <= rail.clientWidth) return;
  const r = rail.getBoundingClientRect();
  const b = f.getBoundingClientRect();
  const delta = b.left + b.width / 2 - (r.left + r.width / 2);
  if (Math.abs(delta) > 2) rail.scrollLeft += delta;
}

/* ---------- render: cart ---------- */

function lineOptions(line) {
  const parts = [];
  if (line.size) parts.push("Size " + line.size);
  if (line.toppings.length) parts.push(line.toppings.join(", "));
  return parts.join(", ");
}

function renderCartSummary() {
  const cart = state.cart;
  const list = $("#order-lines");

  $("#order-count").textContent = `${cart.count} món`;
  $("#cart-count").textContent = cart.count;
  $("#order-total").textContent = cart.total_text;
  $("#order-checkout").disabled = !cart.count;

  if (!cart.lines.length) {
    list.innerHTML = `<li class="order__empty">Giỏ hàng đang trống.</li>`;
    return;
  }

  list.innerHTML = cart.lines
    .map(
      (line) => `
    <li>
      <div class="order-line__top">
        <span>${line.qty} × ${esc(line.name)}</span>
        <strong>${vnd(line.subtotal)}</strong>
      </div>
      <div class="order-line__bottom">
        <span class="order-line__opts">${esc(lineOptions(line)) || "Mặc định"}</span>
        <span class="stepper">
          <button type="button" data-index="${line.index}" data-qty="${line.qty - 1}" aria-label="Giảm ${esc(
        line.name
      )}">−</button>
          <span>${line.qty}</span>
          <button type="button" data-index="${line.index}" data-qty="${line.qty + 1}" aria-label="Tăng ${esc(
        line.name
      )}">+</button>
        </span>
      </div>
    </li>`
    )
    .join("");
}

function renderCartDrawer() {
  const cart = state.cart;
  $("#drawer-count").textContent = `${cart.count} món`;
  $("#drawer-total").textContent = cart.total_text;
  $("#drawer-checkout").disabled = !cart.count;

  const body = $("#drawer-lines");
  if (!cart.lines.length) {
    body.innerHTML = `<p class="drawer__empty">Giỏ hàng đang trống.</p>`;
    return;
  }

  body.innerHTML = cart.lines
    .map(
      (line) => `
    <div class="drawer-line">
      <div class="drawer-line__thumb">
        <img src="${asset(line.item_id)}" alt="" width="44" height="44" loading="lazy" decoding="async">
      </div>
      <div class="drawer-line__body">
        <div class="drawer-line__top">
          <p class="drawer-line__name">${esc(line.name)}</p>
          <p class="drawer-line__sub">${vnd(line.subtotal)}</p>
        </div>
        <p class="drawer-line__opts">${esc(lineOptions(line)) || "Mặc định"}</p>
        <div class="drawer-line__actions">
          <span class="stepper">
            <button type="button" data-index="${line.index}" data-qty="${line.qty - 1}" aria-label="Giảm ${esc(
        line.name
      )}">−</button>
            <span>${line.qty}</span>
            <button type="button" data-index="${line.index}" data-qty="${line.qty + 1}" aria-label="Tăng ${esc(
        line.name
      )}">+</button>
          </span>
          <button type="button" class="drawer-line__remove" data-index="${line.index}" data-qty="0">Bỏ</button>
        </div>
      </div>
    </div>`
    )
    .join("");
}

function setCart(cart) {
  state.cart = cart;
  renderCartSummary();
  renderCartDrawer();
}

/* ---------- render: chat context ---------- */

function renderChatContext() {
  const item = currentItem();
  const box = $("#chat-context");
  if (!item) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $("#chat-context-text").textContent = `${item.name}, ${item.sized ? "Size " + state.size : "1 phần"}, ${vnd(
    linePrice(item, state.size, state.toppings)
  )}`;
}

/* ---------- interactions ---------- */

function recenter() {
  state.start = state.focus - Math.floor(WINDOW / 2);
}

function setFocus(index, { resetSize = true, recenter: doRecenter = false } = {}) {
  if (!state.list[index] || index === state.focus) return;
  state.focus = index;
  if (resetSize) {
    state.size = "M";
    state.toppings = new Set();
  }
  if (doRecenter) recenter();
  renderRail();
  renderChatContext();
}

function openDrawer() {
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer-close").focus();
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

async function addItem(item, size = "M", toppings = [], qty = 1) {
  try {
    const cart = await api("/api/cart/add", { item_id: item.id, size, toppings, qty });
    setCart(cart);
    toast(`Đã thêm ${item.name}`);
  } catch (err) {
    toast(err.message);
  }
}

async function changeQty(index, qty) {
  try {
    setCart(await api("/api/cart/qty", { index, qty }));
  } catch (err) {
    toast(err.message);
  }
}

/* ---------- checkout ---------- */

function showOrderDialog(order) {
  $("#dialog-id").textContent = order.order_id;
  $("#dialog-total").textContent = order.total_text;
  $("#dialog-lines").innerHTML = order.lines
    .map(
      (line) =>
        `<li><span>${line.qty} × ${esc(line.name)}${lineOptions(line) ? ", " + esc(lineOptions(line)) : ""}</span>
             <span>${vnd(line.subtotal)}</span></li>`
    )
    .join("");
  $("#dialog").hidden = false;
  $("#dialog-close").focus();
}

async function checkout() {
  const buttons = [$("#order-checkout"), $("#drawer-checkout")];
  buttons.forEach((b) => (b.disabled = true));
  try {
    const order = await api("/api/checkout", {});
    setCart(await api(`/api/cart/${state.sid}`));
    closeDrawer();
    showOrderDialog(order);
    addMessage(`Đã chốt đơn ${order.order_id}. Tổng ${order.total_text}.`, "bot");
  } catch (err) {
    toast(err.message);
  } finally {
    buttons.forEach((b) => (b.disabled = !state.cart.count));
  }
}

/* ---------- chat ---------- */

function scrollLog() {
  const log = $("#log");
  log.scrollTop = log.scrollHeight;
}

function addMessage(text, kind) {
  const el = document.createElement("div");
  el.className = `msg msg--${kind}`;
  el.textContent = text;
  $("#log").append(el);
  scrollLog();
  return el;
}

function setChatState(text) {
  $("#chat-state").textContent = text;
}

function addErrorBubble(message, retryText) {
  const el = document.createElement("div");
  el.className = "msg msg--err";
  el.innerHTML = `<i class="ph ph-warning-circle" aria-hidden="true"></i><span>${esc(message)}</span>`;

  if (retryText) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "msg__retry";
    retry.textContent = "Gửi lại";
    retry.addEventListener("click", () => {
      el.remove();
      send(retryText);
    });
    el.append(retry);
  }

  $("#log").append(el);
  scrollLog();
}

async function send(text) {
  const message = text.trim();
  if (!message || state.busy) return;

  state.busy = true;
  state.lastFailed = message;
  $("#send").disabled = true;
  $("#input").value = "";

  addMessage(message, "me");
  const bubble = addMessage("Đang chờ phản hồi", "bot");
  bubble.classList.add("msg--pending");
  setChatState("Đang chờ phản hồi");
  let aiFailed = false;

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sid, message }),
    });

    if (res.status === 429) {
      bubble.remove();
      addErrorBubble("Quán đang đông. Vui lòng thử lại sau.", message);
      setChatState("Sẵn sàng");
      return;
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Lỗi máy chủ");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streaming = false;

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let split;
      while ((split = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, split).trim();
        buffer = buffer.slice(split + 2);
        if (!chunk.startsWith("data:")) continue;

        const event = JSON.parse(chunk.slice(5));

        if (event.type === "token") {
          if (!streaming) {
            bubble.textContent = "";
            bubble.classList.remove("msg--pending");
            setChatState("Đang trả lời");
            streaming = true;
          }
          bubble.textContent += event.text;
          scrollLog();
        } else if (event.type === "done") {
          if (event.cart) setCart(event.cart);
          setChatState("Sẵn sàng");
        } else if (event.type === "error") {
          bubble.remove();
          addErrorBubble(event.message, message);
          aiFailed = true;
        }
      }
    }
  } catch (err) {
    bubble.remove();
    addErrorBubble(err.message || "Mất kết nối, bạn thử lại nhé.", message);
    aiFailed = true;
  } finally {
    state.busy = false;
    $("#send").disabled = false;
    $("#input").focus();
    if (aiFailed) refreshHealth();
  }
}

/* ---------- wiring ---------- */

function wireUI() {
  // card rail
  const rail = $("#rail");

  // Chọn thẻ bằng click hoặc bàn phím. KHÔNG đổi thẻ khi rê chuột: thẻ được chọn rộng hơn
  // (300px so với 200px) nên đổi khi hover làm cả dải dịch chuyển dưới con trỏ -> bị "nhảy".
  rail.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target.closest(".add-btn") || event.target.closest(".featured")) return;
    const card = event.target.closest(".card[data-index]");
    if (card) {
      event.preventDefault();
      setFocus(Number(card.dataset.index));
    }
  });

  rail.addEventListener("click", (event) => {
    const addCard = event.target.closest("[data-add]");
    if (addCard) {
      const item = state.menu.items.find((i) => i.id === addCard.dataset.add);
      if (item) addItem(item);
      return;
    }

    const sizeBtn = event.target.closest("[data-size]");
    if (sizeBtn) {
      state.size = sizeBtn.dataset.size;
      state.toppings = new Set();
      renderRail();
      renderChatContext();
      return;
    }

    const toggle = event.target.closest('[data-action="toppings"]');
    if (toggle) {
      const pop = $("#topping-pop");
      if (pop) {
        const open = pop.hidden;
        pop.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
      }
      return;
    }

    const topping = event.target.closest("[data-topping]");
    if (topping) {
      const id = topping.dataset.topping;
      if (state.toppings.has(id)) state.toppings.delete(id);
      else state.toppings.add(id);
      renderRail();
      renderChatContext();
      const pop = $("#topping-pop");
      if (pop) pop.hidden = false;
      const button = $('[data-action="toppings"]');
      if (button) button.setAttribute("aria-expanded", "true");
      return;
    }

    const featuredAdd = event.target.closest('[data-action="add-featured"]');
    if (featuredAdd) {
      const item = currentItem();
      if (item) addItem(item, item.sized ? state.size : "M", [...state.toppings]);
      return;
    }

    const card = event.target.closest(".card[data-index]");
    if (card) setFocus(Number(card.dataset.index));
  });

  document.addEventListener("click", (event) => {
    const pop = $("#topping-pop");
    if (!pop || pop.hidden) return;
    if (event.target.closest("#topping-pop") || event.target.closest('[data-action="toppings"]')) return;
    pop.hidden = true;
    const button = $('[data-action="toppings"]');
    if (button) button.setAttribute("aria-expanded", "false");
  });

  // category filters (nav + chips)
  const applyCategory = (category) => {
    state.category = category;
    state.focus = 0;
    state.start = 0;
    state.size = "M";
    state.toppings = new Set();
    buildList();
    renderNav();
    renderRail();
    renderChatContext();
  };

  $("#cat-chips").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-category]");
    if (chip) applyCategory(chip.dataset.category);
  });

  $("#site-nav").addEventListener("click", (event) => {
    const link = event.target.closest("[data-category]");
    if (link) applyCategory(link.dataset.category);
  });

  // search
  let searchTimer;
  $("#search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    const value = event.target.value;
    searchTimer = setTimeout(() => {
      state.query = value;
      state.focus = 0;
      state.start = 0;
      buildList();
      renderNav();
      renderRail();
      renderChatContext();
    }, 160);
  });

  // arrows
  $("#prev").addEventListener("click", () => {
    const n = state.list.length;
    if (!n) return;
    state.focus = (state.focus - 1 + n) % n;
    state.size = "M";
    state.toppings = new Set();
    recenter();
    renderRail();
    renderChatContext();
  });

  $("#next").addEventListener("click", () => {
    const n = state.list.length;
    if (!n) return;
    state.focus = (state.focus + 1) % n;
    state.size = "M";
    state.toppings = new Set();
    recenter();
    renderRail();
    renderChatContext();
  });

  // cart
  $("#open-cart").addEventListener("click", openDrawer);
  $("#burger").addEventListener("click", openDrawer);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", () => {
    closeDrawer();
    $("#dialog").hidden = true;
  });

  const qtyHandler = (event) => {
    const btn = event.target.closest("[data-qty]");
    if (!btn) return;
    changeQty(Number(btn.dataset.index), Number(btn.dataset.qty));
  };
  $("#order-lines").addEventListener("click", qtyHandler);
  $("#drawer-lines").addEventListener("click", qtyHandler);

  $("#order-checkout").addEventListener("click", checkout);
  $("#drawer-checkout").addEventListener("click", checkout);

  $("#dialog-close").addEventListener("click", () => {
    $("#dialog").hidden = true;
    $("#scrim").hidden = true;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      $("#dialog").hidden = true;
      const pop = $("#topping-pop");
      if (pop) pop.hidden = true;
    }
  });

  // chat
  $("#form").addEventListener("submit", (event) => {
    event.preventDefault();
    send($("#input").value);
  });

  const suggestions = ["Món nào ít ngọt, dưới 40.000đ?", "Món nào không sữa?", "Tính tiền giúp mình"];
  const chipsBox = $("#chips");
  chipsBox.innerHTML = suggestions
    .map((q) => `<button type="button" class="chip">${esc(q)}</button>`)
    .join("");
  chipsBox.addEventListener("click", (event) => {
    const chip = event.target.closest("button");
    if (chip) send(chip.textContent);
  });
}

/* ---------- boot ---------- */

/* ---------- trạng thái AI ---------- */

async function refreshHealth() {
  const dot = $("#ai-status .dot");
  let text;
  let ok = false;
  try {
    const health = await api("/api/health");
    ok = Boolean(health.llm);
    text = ok ? (health.mock ? "AI giả lập" : "AI sẵn sàng") : "AI chưa kết nối";
  } catch {
    text = "Mất kết nối";
  }
  $("#ai-status-text").textContent = text;
  dot.classList.toggle("dot--off", !ok);
  if (!state.busy) setChatState(ok ? "Sẵn sàng" : text === "Mất kết nối" ? "Mất kết nối" : "Chưa kết nối");
}

async function boot() {
  wireUI();

  try {
    state.menu = await api("/api/menu");
  } catch (err) {
    $("#rail").innerHTML = `<p class="rail-empty">Không tải được menu: ${esc(err.message)}</p>`;
    return;
  }

  state.menu.items.forEach((item) => {
    if (item.bestsellers) state.bestsellers.add(item.id);
  });

  if (state.menu.shop_name) {
    $("#shop-name").textContent = state.menu.shop_name;
    $("#dialog-shop").textContent = state.menu.shop_name;
    document.title = `${state.menu.shop_name} — Trợ lý AI`;
  }

  buildList();
  recenter();
  renderNav();
  renderRail();
  renderChatContext();

  addMessage(
    "Chào bạn! Mình là trợ lý của quán. Bạn có thể hỏi về menu, nhờ tư vấn món, hoặc gõ thẳng: “cho mình 2 cà phê muối size L”.",
    "bot"
  );

  try {
    setCart(await api(`/api/cart/${state.sid}`));
  } catch (err) {
    toast(err.message);
  }

  await refreshHealth();
  setInterval(refreshHealth, 10000);
}

boot();
