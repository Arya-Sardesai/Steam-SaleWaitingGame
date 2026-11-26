// Sale Waiting Game: front end. No framework, no build step.
// Reads the static JSON that steamwait/predict.py writes into site/data/.

const $app = document.getElementById("app");
const $tip = document.getElementById("tooltip");
const DAY_MS = 86400000;

// ---------------------------------------------------------------- data
const cache = { index: null, browse: null, card: null, status: null, shards: new Map() };

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
const index = async () => (cache.index ??= await getJSON("data/index.json"));
const browse = async () => (cache.browse ??= await getJSON("data/browse.json"));
const modelCard = async () => (cache.card ??= await getJSON("data/model_card.json"));
const status = async () => (cache.status ??= await getJSON("data/status.json"));
async function game(id) {
  const idx = await index();
  const shard = String(id % idx.shards).padStart(2, "0");
  if (!cache.shards.has(shard)) cache.shards.set(shard, getJSON(`data/games/${shard}.json`));
  return (await cache.shards.get(shard))[String(id)] ?? null;
}

// ---------------------------------------------------------------- watchlist (this browser only)
const WATCH_KEY = "swg.watchlist";
function watchlist() {
  try { return JSON.parse(localStorage.getItem(WATCH_KEY)) ?? []; } catch { return []; }
}
function setWatchlist(ids) {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(ids)); } catch { /* storage blocked */ }
  paintWatchCount();
}
function toggleWatch(id) {
  const ids = watchlist();
  setWatchlist(ids.includes(id) ? ids.filter((x) => x !== id) : [id, ...ids]);
  return watchlist().includes(id);
}
function paintWatchCount() {
  const n = watchlist().length;
  document.getElementById("watch-count").textContent = n ? ` ${n}` : "";
}

// ---------------------------------------------------------------- formatting
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x == null ? "—" : x === 0 ? "Free" : `$${x.toFixed(2)}`);
const pct = (p) => (p == null ? "—" : `${Math.round(p * 100)}%`);
const num = (n) => (n == null ? "—" : n.toLocaleString("en-CA"));
const parseDay = (s) => new Date(s + "T00:00:00Z");
const fmtDate = (d, opts = { month: "short", day: "numeric", year: "numeric" }) => d.toLocaleDateString("en-CA", { ...opts, timeZone: "UTC" });
const daysBetween = (a, b) => Math.round((b - a) / DAY_MS);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
function humanDays(d) {
  if (d == null) return "—";
  if (d < 60) return plural(d, "day");
  if (d < 730) return plural(Math.round(d / 30.4), "month");
  return `${(d / 365).toFixed(1)} yrs`;
}

// ---------------------------------------------------------------- game art (Steam CDN, with a typographic fallback)
const ART = [
  (id) => `https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/${id}/header.jpg`,
  (id) => `https://cdn.cloudflare.steamstatic.com/steam/apps/${id}/header.jpg`,
];
function art(id, name, badge = "") {
  return `<div class="thumb"><div class="thumb__fallback">${esc(name)}</div><img src="${ART[0](id)}" data-id="${id}" data-try="0" alt="" loading="lazy" decoding="async">${badge}</div>`;
}
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.dataset.id) return;
  const next = Number(img.dataset.try) + 1;
  if (next < ART.length) { img.dataset.try = next; img.src = ART[next](img.dataset.id); }
  else img.remove();
}, true);

const badgeP = (p) => `<span class="badge">${pct(p)}</span>`;
const badgeCut = (c) => `<span class="badge badge--sale">−${c}%</span>`;

// ---------------------------------------------------------------- tooltip
function showTip(html, x, y) {
  $tip.innerHTML = html;
  $tip.style.opacity = 1;
  $tip.style.left = `${Math.min(window.innerWidth - $tip.offsetWidth - 8, x + 14)}px`;
  $tip.style.top = `${y + 16}px`;
}
const hideTip = () => { $tip.style.opacity = 0; };

// ---------------------------------------------------------------- router
const routes = [
  [/^#?\/?$/, discoverPage, "discover"],
  [/^#\/game\/(\d+)/, (m) => gamePage(Number(m[1])), null],
  [/^#\/browse(?:\/(\w+))?/, (m) => browsePage(m[1]), "browse"],
  [/^#\/watchlist/, watchlistPage, "watchlist"],
  [/^#\/data/, dataPage, "data"],
];
async function route() {
  hideTip();
  window.onresize = null;
  const hash = location.hash || "#/";
  for (const [re, fn, nav] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    document.querySelectorAll("[data-nav]").forEach((a) =>
      a.dataset.nav === nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
    try { await fn(m); } catch (err) {
      console.error(err);
      $app.innerHTML = `<div class="wrap"><p class="empty">Couldn't load this page. <span class="mono small">${esc(err.message)}</span></p></div>`;
    }
    window.scrollTo(0, 0);
    return;
  }
  $app.innerHTML = `<div class="wrap"><p class="empty">Page not found. <a href="#/">Back to Discover</a>.</p></div>`;
}

// ---------------------------------------------------------------- search
function rankMatches(games, q, limit = 8) {
  q = q.trim().toLowerCase();
  if (q.length < 2) return [];
  const out = [];
  for (const g of games) {
    const name = g[1].toLowerCase();
    const at = name.indexOf(q);
    if (at < 0) continue;
    out.push([at === 0 ? 0 : /[\s:\-–]/.test(name[at - 1]) ? 1 : 2, name.length, g]);
  }
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return out.slice(0, limit).map((x) => x[2]);
}
function highlight(name, q) {
  const at = name.toLowerCase().indexOf(q.trim().toLowerCase());
  if (at < 0) return esc(name);
  const n = q.trim().length;
  return esc(name.slice(0, at)) + "<mark>" + esc(name.slice(at, at + n)) + "</mark>" + esc(name.slice(at + n));
}
const TAG = { f: "forecast", s: "on sale now", n: "never discounted" };

function mountSearch(root, games) {
  const input = root.querySelector("input");
  const list = root.querySelector(".results");
  let items = [], sel = -1;
  function paint() {
    const q = input.value;
    items = rankMatches(games, q);
    if (q.trim().length < 2) { list.hidden = true; return; }
    list.hidden = false;
    list.innerHTML = items.length
      ? items.map((g, i) => `<li><a href="#/game/${g[0]}" aria-selected="${i === sel}">${art(g[0], g[1])}
          <span class="results__name">${highlight(g[1], q)}</span><span class="results__tag ${g[2] === "s" ? "up" : ""}">${TAG[g[2]] ?? ""}</span></a></li>`).join("")
      : `<li class="results__empty">No match for “${esc(q)}”. Adult-only games and games without a Canadian store price aren't tracked.</li>`;
  }
  input.addEventListener("input", () => { sel = -1; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(-1, sel - 1); paint(); e.preventDefault(); }
    else if (e.key === "Enter") { const g = items[Math.max(0, sel)]; if (g) location.hash = `#/game/${g[0]}`; }
    else if (e.key === "Escape") { input.value = ""; paint(); }
  });
  document.addEventListener("click", (e) => { if (!root.contains(e.target)) list.hidden = true; });
  input.addEventListener("focus", paint);
}
const searchHTML = (placeholder = "Search a game, e.g. Hades, Stardew Valley, Elden Ring") => `
  <div class="search" role="search">
    <label class="search__box">
      <input type="search" placeholder="${placeholder}" autocomplete="off" spellcheck="false" aria-label="Search games">
      <span class="search__key">/</span>
    </label>
    <ul class="results" hidden></ul>
  </div>`;
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
    const input = document.querySelector(".search input");
    if (input) { e.preventDefault(); input.focus(); }
  }
});

// ---------------------------------------------------------------- shared
function upcomingEvents(idx, n = 4) {
  const today = parseDay(idx.as_of);
  const seen = new Set();
  return idx.events
    .filter((e) => parseDay(e.date) >= today)
    .filter((e) => (seen.has(e.name) ? false : seen.add(e.name)))
    .slice(0, n)
    .map((e) => ({ ...e, days: daysBetween(today, parseDay(e.date)) }));
}

const card = (g, badge, row, sub) => `<a class="card" href="#/game/${g.id}">${art(g.id, g.name, badge)}
  <div class="card__name">${esc(g.name)}</div><div class="card__row">${row}</div>${sub ? `<div class="card__sub">${sub}</div>` : ""}</a>`;

const row = (g, sub, mid, big) => `<li><a class="row" href="#/game/${g.id}">${art(g.id, g.name)}
  <span><span class="row__name">${esc(g.name)}</span><br><span class="row__sub">${sub}</span></span>
  <span class="row__num">${mid}</span><span class="row__big">${big}</span></a></li>`;

const LISTS = {
  likely_soon: {
    title: "Likely on sale soon",
    blurb: "Full price today, with a high chance of a discount in the next 30 days. Hold off on these.",
    card: (g) => card(g, badgeP(g.p30), `<span>${money(g.price)}</span><span class="muted">${g.cut_range ? `→ ${g.cut_range[0]}–${g.cut_range[1]}% off` : ""}</span>`),
    row: (g) => row(g, g.cut_range ? `Expect ${g.cut_range[0]}–${g.cut_range[1]}% off${g.next_event_name ? ` · ${esc(g.next_event_name)}` : ""}` : "", money(g.price), `<span class="hot">${pct(g.p30)}</span>`),
  },
  biggest_savings: {
    title: "Biggest savings for waiting",
    blurb: "Pricey at full price, likely to drop, and usually cut deep. Waiting is worth the most here.",
    card: (g) => card(g, badgeP(g.p30), `<span>${money(g.price)}</span><span class="up">${g.price_range ? `save $${Math.floor(g.price - g.price_range[1])}+` : ""}</span>`),
    row: (g) => row(g, g.price_range ? `Projected ${money(g.price_range[0])}–${money(g.price_range[1])} · ${pct(g.p30)} within 30 days` : "", money(g.price), g.price_range ? `<span class="up">−$${Math.floor(g.price - g.price_range[1])}</span>` : "—"),
  },
  at_all_time_low: {
    title: "At their all-time low",
    blurb: "On sale right now at the lowest price on record. These rarely get cheaper.",
    card: (g) => card(g, badgeCut(g.cut_now), `<span class="up">${money(g.price)}</span><s class="muted">${money(g.regular)}</s>`),
    row: (g) => row(g, `Lowest price on record · −${g.cut_now}% today`, `<s class="muted">${money(g.regular)}</s>`, `<span class="up">${money(g.price)}</span>`),
  },
  dont_buy_yet: {
    title: "Never pay full price for",
    blurb: "Full price today, but they get cut deep and often. Paying full price for these is the worst deal on Steam.",
    card: (g) => card(g, `<span class="badge badge--plain">−${g.typical_cut}% usual</span>`, `<span>${money(g.price)}</span><span class="up">low ${money(g.lowest_ever)}</span>`),
    row: (g) => row(g, `Usually −${g.typical_cut}% · ${pct(g.p30)} chance within 30 days`, `${money(g.price)}`, `<span class="up">${money(g.lowest_ever)}</span>`),
  },
};

// ---------------------------------------------------------------- Discover
async function discoverPage() {
  const [idx, b, st] = await Promise.all([index(), browse(), status().catch(() => ({}))]);
  const up = upcomingEvents(idx, 4);
  const next = up[0];
  const onSale = idx.games.filter((g) => g[2] === "s").length;

  $app.innerHTML = `
    <section class="band">
      <div class="wrap">
        <div class="hero">
          <div>
            <h1 class="h1">Buy it now,<br>or <em>wait for the sale?</em></h1>
            <p class="hero__lede">Search any Steam game. See the chance it goes on sale in the next month, how deep the cut usually goes, and what you'd probably pay.</p>
            ${searchHTML()}
          </div>
          ${next ? `<div class="countdown">
            <div class="label">Next big Steam sale</div>
            <div class="countdown__n">${next.days}<span style="font-size:22px"> days</span></div>
            <div class="countdown__name">${esc(next.name)}</div>
            <div class="mono small muted" style="margin-top:4px">starts ${fmtDate(parseDay(next.date), { month: "short", day: "numeric" })}</div>
          </div>` : "<div></div>"}
        </div>
        <div class="strip-head"><span class="label">Most likely to drop in the next 30 days</span><a href="#/browse/likely_soon">See all →</a></div>
        <div class="strip">${b.likely_soon.slice(0, 5).map((g) => `<a class="tile" href="#/game/${g.id}">${art(g.id, g.name, badgeP(g.p30))}
          <div class="tile__name">${esc(g.name)}</div><div class="tile__meta">${money(g.price)}${g.price_range ? ` → ~${money(g.price_range[1])}` : ""}</div></a>`).join("")}</div>
      </div>
    </section>

    <div class="wrap">
      <div class="ticker">
        <div><div class="ticker__n">${num(st.games_tracked ?? idx.games.length)}</div><div class="label">games tracked</div></div>
        <div><div class="ticker__n">${num(st.price_changes)}</div><div class="label">price changes since ${(st.history_since ?? "").slice(0, 4) || "—"}</div></div>
        <div><div class="ticker__n">${num(st.sale_events)}</div><div class="label">sales detected</div></div>
        <div><div class="ticker__n">${num(onSale)}</div><div class="label">on sale right now</div></div>
      </div>

      <section class="section">
        <div class="sec-head"><h2 class="h2">At their all-time low</h2><a href="#/browse/at_all_time_low">See all →</a></div>
        <p class="sec-sub">${LISTS.at_all_time_low.blurb}</p>
        <div class="grid">${b.at_all_time_low.slice(0, 8).map(LISTS.at_all_time_low.card).join("")}</div>
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">Biggest savings for waiting</h2><a href="#/browse/biggest_savings">See all →</a></div>
        <p class="sec-sub">${LISTS.biggest_savings.blurb}</p>
        <div class="grid">${b.biggest_savings.slice(0, 4).map(LISTS.biggest_savings.card).join("")}</div>
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">Coming up on Steam</h2></div>
        <div class="calendar">${up.map((e) => `<div><div class="label">${fmtDate(parseDay(e.date), { month: "short", day: "numeric" })}</div>
          <div class="calendar__name">${esc(e.name)}</div><div class="mono small muted">${e.days === 0 ? "today" : `in ${plural(e.days, "day")}`}</div></div>`).join("") || `<div class="muted">No events scheduled.</div>`}</div>
      </section>
    </div>`;
  mountSearch($app.querySelector(".search"), idx.games);
}

// ---------------------------------------------------------------- Game page
const VERDICTS = {
  wait: { word: "Wait", line: (g) => `${pct(g.p30)} chance of a sale within 30 days` },
  buy: { word: "Buy", line: (g) => `Only ${pct(g.p30)} chance of a sale within 30 days` },
  toss_up: { word: "Toss-up", line: (g) => `${pct(g.p30)} chance of a sale within 30 days` },
  on_sale: { word: "On sale", line: (g) => `−${g.cut_now}% right now` },
  none: { word: "No sales", line: () => "Never discounted on record" },
};

function verdictWhy(g) {
  if (g.status === "on_sale") {
    if (g.lowest_ever != null && g.price <= g.lowest_ever + 0.005) return "It's on sale at the lowest price we've ever recorded. If you want it, this is the time.";
    if (g.lowest_ever != null) return `It's on sale, but it has been cheaper: ${money(g.lowest_ever)} at its lowest (−${g.deepest_ever}%). Today's cut is −${g.cut_now}%.`;
    return `It's −${g.cut_now}% off today.`;
  }
  if (g.status === "no_history") return "This game has never been discounted, so there's nothing to learn from yet. Some games simply never go on sale.";
  const range = `usually ${g.cut_range[0]}–${g.cut_range[1]}% off`;
  if (g.verdict === "wait") return `A sale is likely soon. If you can hold off, you'd probably pay ${money(g.price_range[0])}–${money(g.price_range[1])} instead of ${money(g.price)}.`;
  if (g.verdict === "buy") return `A sale isn't likely in the next month. If you want it now, waiting probably isn't worth it. When it does go on sale, it's ${range}.`;
  return `It could go either way this month. If you're not in a hurry, waiting could save you up to ${money(g.price - g.price_range[0])}.`;
}

async function gamePage(id) {
  const [idx, g] = await Promise.all([index(), game(id)]);
  if (!location.hash.startsWith(`#/game/${id}`)) return; // already navigated away
  if (!g) {
    $app.innerHTML = `<div class="wrap"><p class="empty">We don't have that game. It may be adult-only, free, or not sold on Steam in Canada. <a href="#/">Search again</a>.</p></div>`;
    return;
  }
  document.title = `${g.name}: buy now or wait? · Sale Waiting Game`;
  const v = VERDICTS[g.verdict ?? "none"] ?? VERDICTS.none;
  const watching = watchlist().includes(g.id);
  const meta = [g.publisher, (g.genres ?? []).slice(0, 3).join(", "), g.released ? `released ${fmtDate(parseDay(g.released), { year: "numeric", month: "short" })}` : null].filter(Boolean);
  const isForecast = g.status === "forecast";

  $app.innerHTML = `
    <section class="band">
      <div class="wrap"><div class="ghead">
        <div>
          <div class="crumbs"><a href="#/">Discover</a> / ${esc(g.name)}</div>
          <h1 class="title">${esc(g.name)}</h1>
          <div class="ghead__meta">${meta.map(esc).join(" · ")}</div>
          <div class="ghead__price"><b>${money(g.price)}</b>${g.status === "on_sale" ? `<s>${money(g.regular)}</s><span class="up">−${g.cut_now}%</span>` : `<span class="muted">full price</span>`}</div>
          <div class="actions">
            <button class="btn ${watching ? "btn--on" : ""}" id="watch">${watching ? "Watching" : "+ Watch"}</button>
            <a class="btn" href="https://store.steampowered.com/app/${g.id}" target="_blank" rel="noopener">Steam page ↗</a>
          </div>
        </div>
        ${art(g.id, g.name)}
      </div></div>
    </section>

    <div class="wrap">
      <section class="verdict">
        <div class="score score--${g.verdict ?? "none"}">
          <div class="label" style="color:rgba(255,255,255,.75)">Our call</div>
          <div><div class="score__word">${v.word}</div><div class="score__line">${v.line(g)}</div></div>
        </div>
        <div class="verdict__body">
          <p class="verdict__why">${verdictWhy(g)}</p>
          ${isForecast ? `<div class="odds">
            ${[["7 days", g.p7], ["14 days", g.p14], ["30 days", g.p30]].map(([lbl, p]) => `
              <div class="${p >= 0.5 ? "odds--hi" : ""}"><div class="label">Sale within ${lbl}</div><div class="odds__n">${pct(p)}</div><div class="odds__bar"><span style="width:${Math.round(p * 100)}%"></span></div></div>`).join("")}
          </div><p class="note">Chance a discount starts within each window. They stack: 30 days includes the first 14.</p>` : ""}
          ${!isForecast && g.lowest_ever != null ? `<div class="odds">
            <div><div class="label">All-time low</div><div class="odds__n">${money(g.lowest_ever)}</div></div>
            <div><div class="label">Deepest cut</div><div class="odds__n">−${g.deepest_ever}%</div></div>
            <div><div class="label">Times on sale</div><div class="odds__n">${g.sales_recorded}</div></div></div>` : ""}
        </div>
      </section>

      ${isForecast ? `<section class="figs">
        <div><div class="label">Most likely cut</div><div class="fig">−${g.cut_likely}%</div><div class="small muted">if it goes on sale</div></div>
        <div><div class="label">Discount range</div><div class="fig">${g.cut_range[0]}–${g.cut_range[1]}%</div><div class="small muted">80% of similar sales land here</div></div>
        <div><div class="label">You'd probably pay</div><div class="fig">${money(g.price_range[0])}–${money(g.price_range[1])}</div><div class="small muted">vs ${money(g.price)} today</div></div>
      </section>` : ""}

      ${g.reasons?.length ? `<section class="section">
        <div class="sec-head"><h2 class="h2">Why we think so</h2><span class="label">Biggest factors in this forecast</span></div>
        <ul class="reasons">${g.reasons.map((r) => `<li><span class="reasons__dir reasons__dir--${r.dir === "+" ? "plus" : "minus"}">${r.dir === "+" ? "↑" : "↓"}</span><span>${esc(r.text)}</span></li>`).join("")}</ul>
        <p class="note"><span class="hot">↑</span> makes a sale more likely, <span style="color:var(--grey)">↓</span> less likely. Pulled from the model's breakdown of this exact prediction (SHAP).</p>
      </section>` : ""}

      <section class="section">
        <div class="sec-head">
          <h2 class="h2">Price history</h2>
          <div class="chart-tools">
            <div class="seg" data-k="mode"><button data-v="price" aria-pressed="true">Price</button><button data-v="cut">Discount</button></div>
            <div class="seg" data-k="range"><button data-v="365">1Y</button><button data-v="1095" aria-pressed="true">3Y</button><button data-v="all">All</button></div>
          </div>
        </div>
        <div class="chart" id="chart"></div>
        <div class="legend">
          <span><i style="background:rgba(217,119,53,.22)"></i>on sale</span>
          <span><i style="height:0;border-top:2px dashed var(--green)"></i>all-time low</span>
          <span><i style="width:10px;height:10px;background:var(--ink)"></i>Steam seasonal sale</span>
        </div>
      </section>

      ${g.sales_recorded ? `<section class="section">
        <div class="sec-head"><h2 class="h2">How this game behaves</h2></div>
        <div class="behave">
          <div><div class="label">Times on sale</div><div class="fig">${g.sales_recorded}</div></div>
          <div><div class="label">Usual gap</div><div class="fig">${humanDays(g.typical_gap_days)}</div></div>
          <div><div class="label">Usual cut</div><div class="fig">−${g.typical_cut}%</div></div>
          <div><div class="label">Deepest cut</div><div class="fig">−${g.deepest_ever}%</div></div>
          <div><div class="label">Last sale ended</div><div class="fig">${g.days_since_sale != null ? humanDays(g.days_since_sale) : g.status === "on_sale" ? "now" : "—"}</div><div class="small muted">${g.days_since_sale != null ? "ago" : ""}</div></div>
        </div>
        <div class="split">
          <div><div class="label">How deep past sales went</div><div class="dist" id="dist"></div><div class="dist-l" id="dist-l"></div></div>
          <div>${atlCompare(g)}</div>
        </div>
      </section>` : ""}
    </div>`;

  $app.querySelector("#watch").addEventListener("click", (e) => {
    const on = toggleWatch(g.id);
    e.currentTarget.classList.toggle("btn--on", on);
    e.currentTarget.textContent = on ? "Watching" : "+ Watch";
  });
  const state = { mode: "price", range: "1095" };
  const chartEl = $app.querySelector("#chart");
  const draw = () => priceChart(chartEl, g, idx, state);
  $app.querySelectorAll(".seg").forEach((seg) => seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    state[seg.dataset.k] = b.dataset.v;
    draw();
  }));
  draw();
  let t; window.onresize = () => { clearTimeout(t); t = setTimeout(() => { if (chartEl.isConnected) draw(); }, 120); };
  if ($app.querySelector("#dist")) distribution(g);
}

function atlCompare(g) {
  if (g.lowest_ever == null) return "";
  const gap = g.price - g.lowest_ever;
  const atLow = gap < 0.005;
  return `<div class="versus">
    <div class="label">Today vs the all-time low</div>
    <div class="versus__big">${atLow ? "This is the lowest price ever" : `${money(gap)} above its best price`}</div>
    <p>Lowest on record: <span class="mono">${money(g.lowest_ever)}</span> (−${g.deepest_ever}%). Today: <span class="mono">${money(g.price)}</span>.
    ${atLow ? "" : g.status === "forecast" && g.price_range ? `A typical sale would bring it to <span class="mono">${money(g.price_range[0])}–${money(g.price_range[1])}</span>.` : ""}</p>
  </div>`;
}

function saleCuts(history) {
  const out = [];
  let prev = 0;
  for (const [, , cut] of history) { if (cut > 0 && prev === 0) out.push(cut); prev = cut; }
  return out;
}
function distribution(g) {
  const cuts = saleCuts(g.history);
  const bins = [[1, 24, "<25%"], [25, 39, "25–39"], [40, 54, "40–54"], [55, 69, "55–69"], [70, 84, "70–84"], [85, 100, "85%+"]];
  const counts = bins.map(([lo, hi]) => cuts.filter((c) => c >= lo && c <= hi).length);
  const max = Math.max(1, ...counts);
  const top = counts.indexOf(max);
  $app.querySelector("#dist").innerHTML = counts.map((c, i) => `<div class="${i === top ? "is-top" : ""}"><span class="cnt">${c || ""}</span><span class="bar" style="height:${(c / max) * 80}%"></span></div>`).join("");
  $app.querySelector("#dist-l").innerHTML = bins.map((b) => `<span>${b[2]}</span>`).join("");
}

// ---------------------------------------------------------------- price chart (hand-drawn SVG)
function priceChart(el, g, idx, { mode, range }) {
  const epoch = parseDay(idx.history_epoch).getTime();
  const end = parseDay(idx.as_of).getTime() + DAY_MS;
  const steps = g.history.map(([d, c, cut]) => ({ t: epoch + d * DAY_MS, price: c / 100, cut }));
  if (!steps.length) { el.innerHTML = `<p class="empty">No price history.</p>`; return; }
  const start = range === "all" ? steps[0].t : Math.max(steps[0].t, end - Number(range) * DAY_MS);
  const i0 = steps.findLastIndex((s) => s.t <= start);
  const view = (i0 >= 0 ? [{ ...steps[i0], t: start }] : []).concat(steps.filter((s) => s.t > start));

  const W = el.clientWidth || 900, H = Math.max(240, Math.min(340, W * 0.34));
  const m = { t: 24, r: 8, b: 26, l: 50 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const val = (s) => (mode === "price" ? s.price : s.cut);
  const yMax = mode === "price" ? Math.max(...view.map((s) => s.price), g.regular ?? 0) * 1.1 || 1 : 100;
  const x = (t) => m.l + ((t - start) / (end - start)) * iw;
  const y = (v) => m.t + ih - (v / yMax) * ih;
  const nextT = (i) => (i + 1 < view.length ? view[i + 1].t : end);

  let d = "", bands = "";
  view.forEach((s, i) => {
    d += `${i ? "L" : "M"}${x(s.t).toFixed(1)},${y(val(s)).toFixed(1)}H${x(nextT(i)).toFixed(1)}`;
    if (s.cut > 0) bands += `<rect x="${x(s.t).toFixed(1)}" y="${m.t}" width="${Math.max(1.5, x(nextT(i)) - x(s.t)).toFixed(1)}" height="${ih}" fill="rgba(217,119,53,.22)"/>`;
  });

  const evs = idx.events.filter((e) => /sale/i.test(e.name)).map((e) => ({ ...e, t: parseDay(e.date).getTime() })).filter((e) => e.t >= start && e.t <= end);
  const evMarks = evs.map((e) => `<g class="ev" data-name="${esc(e.name)}" data-date="${e.date}">
      <line x1="${x(e.t)}" x2="${x(e.t)}" y1="${m.t - 6}" y2="${m.t + ih}" stroke="var(--ink)" stroke-width="0.6" stroke-dasharray="1 3"/>
      <rect x="${x(e.t) - 4.5}" y="${m.t - 15}" width="9" height="9" fill="var(--ink)"/>
      <rect x="${x(e.t) - 7}" y="${m.t - 18}" width="14" height="${ih + 18}" fill="transparent"/></g>`).join("");

  const stepY = mode === "price" ? niceStep(yMax / 4) : 25;
  let yAxis = "";
  for (let v = 0; v <= yMax + 1e-9; v += stepY)
    yAxis += `<g class="axis"><line x1="${m.l}" x2="${m.l + iw}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 3.5}" text-anchor="end">${mode === "price" ? "$" + (v % 1 ? v.toFixed(2) : v) : v + "%"}</text></g>`;

  const xt = [];
  const sd = new Date(start);
  if (end - start <= 400 * DAY_MS) {
    for (let dt = new Date(Date.UTC(sd.getUTCFullYear(), Math.floor(sd.getUTCMonth() / 3) * 3 + 3, 1)); dt.getTime() < end; dt.setUTCMonth(dt.getUTCMonth() + 3))
      xt.push([dt.getTime(), fmtDate(dt, { month: "short", year: "numeric" })]);
  } else {
    const every = (end - start) / (365 * DAY_MS) > 6 ? 2 : 1;
    for (let yr = sd.getUTCFullYear() + 1; Date.UTC(yr, 0, 1) < end; yr += every) xt.push([Date.UTC(yr, 0, 1), String(yr)]);
  }
  const xAxis = xt.map(([t, l]) => `<g class="axis"><line x1="${x(t)}" x2="${x(t)}" y1="${m.t + ih}" y2="${m.t + ih + 5}"/><text x="${x(t)}" y="${m.t + ih + 18}" text-anchor="middle">${l}</text></g>`).join("");

  const atl = mode === "price" && g.lowest_ever != null
    ? `<line x1="${m.l}" x2="${m.l + iw}" y1="${y(g.lowest_ever)}" y2="${y(g.lowest_ever)}" stroke="var(--green)" stroke-width="2" stroke-dasharray="6 4"/>
       <rect x="${m.l + iw - 150}" y="${y(g.lowest_ever) - 19}" width="150" height="16" fill="var(--bg)"/>
       <text x="${m.l + iw}" y="${y(g.lowest_ever) - 7}" text-anchor="end" font-family="var(--mono)" font-size="11" fill="var(--green)">all-time low ${money(g.lowest_ever)}</text>` : "";

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${mode === "price" ? "Price" : "Discount"} history for ${esc(g.name)}">
    ${bands}${yAxis}${xAxis}${atl}
    <line x1="${m.l}" x2="${m.l + iw}" y1="${m.t + ih}" y2="${m.t + ih}" stroke="var(--ink)" stroke-width="2"/>
    <path d="${d}" fill="none" stroke="var(--ink)" stroke-width="2" stroke-linejoin="miter"/>
    ${evMarks}
    <line id="cursor" y1="${m.t}" y2="${m.t + ih}" stroke="var(--orange)" stroke-width="1.5" opacity="0"/>
    <rect id="dot" width="8" height="8" fill="var(--orange)" opacity="0"/>
    <rect id="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/>
  </svg>`;

  const svg = el.querySelector("svg"), cur = svg.querySelector("#cursor"), dot = svg.querySelector("#dot");
  const toX = (cx) => { const r = svg.getBoundingClientRect(); return ((cx - r.left) / r.width) * W; };
  svg.querySelector("#hit").addEventListener("mousemove", (e) => {
    const t = start + ((toX(e.clientX) - m.l) / iw) * (end - start);
    const s = view[Math.max(0, view.findLastIndex((p) => p.t <= t))];
    const px = x(t);
    cur.setAttribute("x1", px); cur.setAttribute("x2", px); cur.setAttribute("opacity", 1);
    dot.setAttribute("x", px - 4); dot.setAttribute("y", y(val(s)) - 4); dot.setAttribute("opacity", 1);
    showTip(`${fmtDate(new Date(t))}<br>${money(s.price)}${s.cut ? ` · −${s.cut}%` : " · full price"}`, e.clientX, e.clientY);
  });
  svg.querySelector("#hit").addEventListener("mouseleave", () => { hideTip(); cur.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); });
  svg.querySelectorAll(".ev").forEach((n) => {
    n.addEventListener("mousemove", (e) => showTip(`${esc(n.dataset.name)}<br>${fmtDate(parseDay(n.dataset.date))}`, e.clientX, e.clientY));
    n.addEventListener("mouseleave", hideTip);
  });
}
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

// ---------------------------------------------------------------- Browse
async function browsePage(which) {
  const b = await browse();
  const keys = Object.keys(LISTS);
  const sel = keys.includes(which) ? which : "likely_soon";
  const L = LISTS[sel];
  const items = b[sel] ?? [];

  $app.innerHTML = `
    <section class="band"><div class="wrap"><div class="page-head">
      <h1 class="h1" style="font-size:clamp(48px,7vw,88px)">Browse</h1>
      <p>Today's forecasts, sorted four ways.</p>
    </div></div></section>
    <div class="wrap">
      <nav class="tabs" aria-label="Lists">${keys.map((k) => `<button data-k="${k}" aria-pressed="${sel === k}">${LISTS[k].title}</button>`).join("")}</nav>
      <p class="sec-sub" style="margin-top:18px">${L.blurb} <span class="mono small">${items.length} games</span></p>
      <div class="grid">${items.slice(0, 8).map(L.card).join("")}</div>
      ${items.length > 8 ? `<ul class="rows" style="margin-top:40px">${items.slice(8, 60).map(L.row).join("")}</ul>` : ""}
      ${items.length ? "" : `<p class="empty">Nothing here today.</p>`}
    </div>`;
  $app.querySelectorAll(".tabs button").forEach((btn) => btn.addEventListener("click", () => (location.hash = `#/browse/${btn.dataset.k}`)));
}

// ---------------------------------------------------------------- Watchlist
async function watchlistPage() {
  const ids = watchlist();
  const [idx, ...found] = await Promise.all([index(), ...ids.map((id) => game(id).catch(() => null))]);
  const games = found.filter(Boolean);
  const order = { on_sale: 0, wait: 1, toss_up: 2, buy: 3 };
  games.sort((a, b) => (order[a.verdict] ?? 4) - (order[b.verdict] ?? 4) || (b.p30 ?? 0) - (a.p30 ?? 0));
  const word = (g) => (VERDICTS[g.verdict ?? "none"] ?? VERDICTS.none).word;
  const color = (g) => (g.verdict === "wait" ? "hot" : g.verdict === "on_sale" || g.verdict === "buy" ? "up" : "");

  $app.innerHTML = `
    <section class="band"><div class="wrap"><div class="page-head">
      <h1 class="h1" style="font-size:clamp(48px,7vw,88px)">Watchlist</h1>
      <p>${games.length ? `${plural(games.length, "game")}, on sale first.` : "Nothing here yet. Open any game and hit Watch."} Saved in this browser only. No account, nothing sent anywhere.</p>
      <div style="margin-top:24px;max-width:640px">${searchHTML("Add a game…")}</div>
    </div></div></section>
    <div class="wrap" style="margin-top:36px">
      ${games.length ? `<ul class="rows">${games.map((g) => `<li style="display:grid;grid-template-columns:1fr auto;gap:16px;align-items:center;border-bottom:1px solid var(--rule)">
        <a class="row" style="border:0" href="#/game/${g.id}">${art(g.id, g.name)}
          <span><span class="row__name">${esc(g.name)}</span><br><span class="row__sub">${g.status === "on_sale" ? `−${g.cut_now}% today${g.price <= (g.lowest_ever ?? 0) + 0.005 ? " · all-time low" : ""}` : g.price_range ? `likely ${money(g.price_range[0])}–${money(g.price_range[1])} on sale` : "no sales on record"}</span></span>
          <span class="row__num">${money(g.price)}</span>
          <span class="row__big ${color(g)}" style="font-size:16px;text-transform:uppercase">${word(g)}${g.p30 != null ? ` ${pct(g.p30)}` : ""}</span></a>
        <button class="btn" data-rm="${g.id}" style="padding:5px 9px;font-size:11px">Remove</button></li>`).join("")}</ul>` : ""}
    </div>`;
  mountSearch($app.querySelector(".search"), idx.games);
  $app.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { toggleWatch(Number(b.dataset.rm)); watchlistPage(); }));
}

// ---------------------------------------------------------------- How it works
const DQ_ISSUES = [
  ["Sales that started before tracking did", "If tracking began mid-sale, the first record looked like a fresh discount. About 13,000 of these are flagged and excluded."],
  ["Two prices at the same moment", "Steam snapshots and IsThereAnyDeal sometimes logged the same instant with different prices. Collapsed to one change per instant."],
  ["Overlapping festivals", "Two events on one day doubled the calendar rows. Merged into a single day carrying both names."],
  ["Duplicate sale IDs", "Sale keys weren't unique across games. Rebuilt as appid plus sale number."],
  ["Things that aren't games", "A Steam gift card was listed as a game. Only type = game is modelled."],
  ["Impossible prices", "Old records with a sale price above the regular price, or cuts outside 0–100%. Filtered at staging and guarded by tests."],
];
const DRIVER_NAMES = {
  days_to_next_major_event: "Days until the next Steam sale",
  days_since_major_event: "Days since the last Steam sale",
  after_sale_number: "Number of past sales",
  regular_dollars: "Regular price",
  game_age_years: "Game age",
  prev_sale_length_days: "Length of its last sale",
  prev_gap_days: "Previous gap between sales",
  days_waited: "Days since its last sale ended",
  check_month: "Month of the year",
  median_prev_gap: "Typical gap between sales",
  last_cut: "Depth of its last sale",
};

function reliabilitySVG(rel, label) {
  const W = 220, s = 184, m = 28;
  const X = (p) => m + p * s, Y = (p) => 8 + s - p * s;
  const pts = rel.filter((r) => r.n >= 10);
  return `<svg viewBox="0 0 ${W} ${s + 30}" role="img" aria-label="Reliability diagram, ${label}">
    <rect x="${m}" y="8" width="${s}" height="${s}" fill="none" stroke="var(--rule)"/>
    <line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="var(--grey)" stroke-dasharray="3 3"/>
    <polyline fill="none" stroke="var(--orange)" stroke-width="2" points="${pts.map((r) => `${X(r.predicted)},${Y(r.actual)}`).join(" ")}"/>
    ${pts.map((r) => `<rect x="${X(r.predicted) - 3.5}" y="${Y(r.actual) - 3.5}" width="7" height="7" fill="var(--orange)"><title>predicted ${pct(r.predicted)}, happened ${pct(r.actual)} (n=${r.n})</title></rect>`).join("")}
    <g class="axis"><text x="${m}" y="${s + 24}">0</text><text x="${m + s}" y="${s + 24}" text-anchor="end">100%</text></g>
    <text x="${m + 8}" y="26" font-family="var(--mono)" font-size="11" fill="var(--ink)">${label}</text>
  </svg>`;
}

async function dataPage() {
  const [idx, mc, st] = await Promise.all([index(), modelCard(), status().catch(() => null)]);
  const H = [["7 days", mc.timing_7d], ["14 days", mc.timing_14d], ["30 days", mc.timing_30d]].filter((h) => h[1]);
  const d = mc.discount;
  const drivers = Object.entries(mc.top_drivers ?? {}).slice(0, 8);
  const dmax = Math.max(...drivers.map((x) => x[1]), 1e-9);
  const res = st?.dbt?.results ?? {};
  const fails = (res.fail ?? 0) + (res.error ?? 0);
  const lastRun = st?.dbt?.finished_at ? new Date(st.dbt.finished_at) : null;

  $app.innerHTML = `
    <section class="band"><div class="wrap"><div class="page-head">
      <h1 class="h1" style="font-size:clamp(48px,7vw,88px)">How it works</h1>
      <p>Where the numbers come from, what was wrong with the raw data, and how well the forecasts hold up on a year the model never saw.</p>
    </div></div></section>
    <div class="wrap">
      <section class="section">
        <div class="sec-head"><h2 class="h2">System status</h2><span class="label">last pipeline run</span></div>
        <div class="status" style="margin-top:14px">
          <div>
            <div class="mono small"><span class="light ${fails ? "light--bad" : ""}"></span>${fails ? `${fails} checks failed on the last run` : "All checks passing"}</div>
            <div class="label" style="margin-top:18px">Prices as of</div><div class="fig">${st?.price_snapshot_date ?? idx.as_of}</div>
            <div class="small muted">Warehouse rebuilt ${lastRun ? fmtDate(lastRun, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC" : "—"}${st?.dbt?.elapsed_seconds ? ` in ${st.dbt.elapsed_seconds.toFixed(0)}s` : ""}</div>
            <ul class="checks"><li>${res.pass ?? 0} data tests passed${res.warn ? ` · ${res.warn} warnings` : ""}</li><li>${res.success ?? 0} models built</li><li>history back to ${st?.history_since ?? "—"}</li></ul>
          </div>
          <div class="status__grid">
            <div><div class="label">Steam apps</div><div class="fig">${num(st?.games_tracked)}</div></div>
            <div><div class="label">With store data</div><div class="fig">${num(st?.games_with_metadata)}</div></div>
            <div><div class="label">Price changes</div><div class="fig">${num(st?.price_changes)}</div></div>
            <div><div class="label">Sales detected</div><div class="fig">${num(st?.sale_events)}</div></div>
            <div><div class="label">Forecasts today</div><div class="fig">${num(st?.predictions?.forecast)}</div></div>
            <div><div class="label">On sale now</div><div class="fig">${num(st?.predictions?.on_sale)}</div></div>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">The pipeline</h2><span class="label">raw API → this page</span></div>
        <div class="pipeline" style="margin-top:14px">
          <div class="pipe"><span class="pipe__n">01 Collect</span><h4>Every price, twice a day</h4><p>Steam's store API, 200 games per call, on a GitHub Actions schedule. Ten years of history backfilled from IsThereAnyDeal.</p><p class="pipe__tech">Python · GitHub Actions</p></div>
          <div class="pipe"><span class="pipe__n">02 Store</span><h4>Only what changed</h4><p>A row is written only when a price moves. Parquet files, versioned on Hugging Face.</p><p class="pipe__tech">Parquet · HF Datasets</p></div>
          <div class="pipe"><span class="pipe__n">03 Model the data</span><h4>A tested warehouse</h4><p>Staging, intermediate and mart layers turn raw logs into sales, gaps between sales, and Steam's event calendar.</p><p class="pipe__tech">dbt · DuckDB · ${res.pass ?? 27} tests</p></div>
          <div class="pipe"><span class="pipe__n">04 Predict</span><h4>When, and how deep</h4><p>Gradient-boosted classifiers at 7, 14 and 30 days. Quantile models with conformal calibration for the discount range.</p><p class="pipe__tech">LightGBM · SHAP · CQR</p></div>
          <div class="pipe"><span class="pipe__n">05 Publish</span><h4>Static files</h4><p>Forecasts written as sharded JSON and served as a static site. No server, no database at read time.</p><p class="pipe__tech">JSON shards · GitHub Pages</p></div>
        </div>
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">What the raw data got wrong</h2></div>
        <div class="dq" style="margin-top:14px">
          <div>
            <div class="dq__big">13,000</div>
            <div class="h3" style="margin-top:12px">Sales that never happened</div>
            <p class="muted">The biggest trap in the dataset. Leave them in and the model learns from thousands of fake sale starts. Each problem here was caught by a dbt test and fixed in the model layer, not patched by hand.</p>
          </div>
          <ul class="dq-list">${DQ_ISSUES.map(([t, b], i) => `<li><span class="mono">0${i + 1}</span><span><b>${t}.</b> ${b}</span></li>`).join("")}</ul>
        </div>
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">How good are the forecasts?</h2><span class="label">tested on ${esc(H[0]?.[1].test_period ?? "")}</span></div>
        <div class="prose"><p>The question: <b>will this game's next sale start within N days?</b> Asked weekly for every game across its whole history. The model trains on everything before the last year and is scored on that final year, the way it's actually used.
        It's compared against the rule a savvy buyer would use: <b>a big Steam sale is coming, so a discount is likely.</b></p></div>
        <table class="perf" style="margin-top:14px">
          <thead><tr><th>Window</th><th>Average precision: model / seasonal rule</th><th>Lift</th><th>ROC AUC</th><th>Base rate</th><th>Test rows</th></tr></thead>
          <tbody>${H.map(([lbl, h]) => `<tr><td><b>${lbl}</b></td>
            <td><div class="bar-cell"><span class="b" style="width:${h.average_precision * 200}px"></span>${h.average_precision.toFixed(3)}</div>
                <div class="bar-cell muted" style="margin-top:4px"><span class="b base" style="width:${h.seasonal_rule_average_precision * 200}px"></span>${h.seasonal_rule_average_precision.toFixed(3)}</div></td>
            <td class="num up">+${((h.average_precision / h.seasonal_rule_average_precision - 1) * 100).toFixed(0)}%</td>
            <td class="num">${h.roc_auc.toFixed(3)}</td><td class="num">${pct(h.positive_rate)}</td><td class="num">${num(h.test_rows)}</td></tr>`).join("")}</tbody>
        </table>
        <p class="note">Average precision is the headline because sales are rare on any given day. A model that always says “no sale” would score well on accuracy and be useless.</p>

        <div class="cols" style="margin-top:44px">
          <div>
            <div class="h3">When it says 70%, does it happen 70% of the time?</div>
            <p class="small muted">Predicted vs actual on the held-out year. Squares on the dashed line mean the percentages can be taken at face value. The raw model was already close, so no extra calibration layer was added.</p>
            <div style="display:grid;grid-template-columns:repeat(${H.length},1fr);gap:8px;margin-top:12px">${H.map(([lbl, h]) => reliabilitySVG(h.reliability ?? [], lbl)).join("")}</div>
          </div>
          <div>
            <div class="h3">What the model pays attention to</div>
            <p class="small muted">Mean absolute SHAP value, 30-day model. Steam's own sale calendar dominates: the model mostly learns which games join which events.</p>
            <ul class="rows" style="margin-top:12px">${drivers.map(([k, v]) => `<li style="display:grid;grid-template-columns:1fr 150px;gap:12px;padding:8px 0;border-bottom:1px solid var(--rule);font-size:14px">
              <span>${esc(DRIVER_NAMES[k] ?? k.replace(/_/g, " "))}</span><span class="bar-cell"><span class="b" style="width:${(v / dmax) * 140}px;background:var(--orange)"></span></span></li>`).join("")}</ul>
          </div>
        </div>

        ${d ? `<div class="cols" style="margin-top:44px">
          <div><div class="h3">How deep will the cut be?</div>
          <p class="small muted">Quantile regression gives a low, likely and high cut. Conformal calibration on held-out sales widens the range until it covers the promised share of real outcomes.</p></div>
          <div class="figs" style="border-top:3px solid var(--ink)">
            <div><div class="label">Average miss</div><div class="fig">${d.mae_points} pts</div><div class="small muted">vs ${d.baseline_same_as_last_mae_points} for “same as last time”</div></div>
            <div><div class="label">Range coverage</div><div class="fig">${pct(d.widened_range_coverage)}</div><div class="small muted">of real cuts inside the range (target 80%)</div></div>
            <div><div class="label">Checked on</div><div class="fig">${num(d.calibration_sales)}</div><div class="small muted">held-out sales</div></div>
          </div></div>` : ""}
      </section>

      <section class="section">
        <div class="sec-head"><h2 class="h2">Method and limits</h2></div>
        <div class="cols prose" style="max-width:none;margin-top:14px">
          <div>
            <p><b>Survival analysis first.</b> Kaplan–Meier and Cox models of time between sales showed the gap is driven mostly by Steam's calendar, not by each game's own rhythm.</p>
            <p><b>Then classification.</b> Each forecast is a weekly check-in on an open gap, labelled by whether a sale started within N days. Rows whose outcome isn't known yet are dropped, not guessed. Games with many sales are down-weighted so a few hundred titles don't dominate.</p>
            <p><b>Time-based splits only.</b> No random shuffles. The test year is strictly after the training data.</p>
          </div>
          <div>
            <p><b>Probabilities, not promises.</b> Publishers decide discounts. A 90% forecast still misses about one time in ten.</p>
            <p><b>Surprise sales.</b> One-off publisher sales and brand-new festivals are hard to see coming.</p>
            <p><b>New games.</b> Games that have never been discounted get no forecast.</p>
            <p><b>Region.</b> Prices are Steam Canada (CAD). Other regions follow the same schedule at different prices.</p>
          </div>
        </div>
      </section>
    </div>`;
}

// ---------------------------------------------------------------- boot
window.addEventListener("hashchange", () => { document.title = "Sale Waiting Game: buy now or wait for the Steam sale?"; route(); });
paintWatchCount();
index().then((idx) => { document.getElementById("asof").textContent = `prices as of ${fmtDate(parseDay(idx.as_of))}`; }).catch(() => {});
route();
