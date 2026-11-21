/*
 * The header: one row that fits the screen (21 Nov 2026).
 *
 * The floor plan's header was 13 controls and wrapped to three rows on a
 * tablet. Now every control in the header that carries data-fit has a name
 * (data-fit), an importance (data-fit-rank, 1 = most important) and an icon,
 * and this fits them into one row:
 *   Auto ("Fit the screen", the default): names on the most important
 *     buttons as width allows. Less important names fold to their icons
 *     first; then the least used move under ... More, so the core buttons
 *     (rank 1 to CORE_RANK) keep their names from tablet size up; only on a
 *     phone do those fold to icons too.
 *   Icons and words / Icons only / Words only: that look everywhere, and
 *     what does not fit goes under More.
 * Each person picks theirs in the More menu (User.header_style, saved by
 * accounts/views/preference_views.py; freeze lifted by the owner for this).
 *
 * A folded control stays on the page, hidden, so its badge and its own
 * handlers keep working; the More menu repeats it and clicks it.
 * Preview with the reasoning: md_files/header_icons_preview_2026-11-21.html.
 */
export const CORE_RANK = 6;
export const STYLES = [
  ["auto", "Fit the screen"],
  ["both", "Icons and words"],
  ["icons", "Icons only"],
  ["words", "Words only"],
];
const START = { auto: "both", both: "both", icons: "icon", words: "word" };

/*
 * What each control shows so the row fits `available` px: "both", "icon",
 * "word" or "more". items: [{id, rank, round, width: {both, icon, word}}];
 * reserved: px taken by controls that do not fit (and the More button).
 * Pure: node tests it (static/js/tests/header_fit.test.mjs).
 */
export function plan(items, available, style = "auto", { gap = 8, reserved = 0 } = {}) {
  const look = Object.fromEntries(items.map((c) => [c.id, style === "auto" && c.round ? "icon" : (START[style] || "both")]));
  const width = () => items.filter((c) => look[c.id] !== "more")
    .reduce((sum, c) => sum + c.width[look[c.id]] + gap, reserved);
  const lowFirst = [...items].sort((a, b) => b.rank - a.rank);
  let guard = items.length * 4 + 4;
  while (width() > available && guard-- > 0) {
    if (style === "auto") {
      const extra = lowFirst.find((c) => c.rank > CORE_RANK && look[c.id] === "both");
      if (extra) { look[extra.id] = "icon"; continue; }
      const rare = lowFirst.find((c) => c.rank > CORE_RANK && look[c.id] !== "more");
      if (rare) { look[rare.id] = "more"; continue; }
      const core = lowFirst.find((c) => look[c.id] === "both");
      if (core) { look[core.id] = "icon"; continue; }
    }
    const last = lowFirst.find((c) => look[c.id] !== "more");
    if (!last) break;
    look[last.id] = "more";
  }
  if (style === "auto") {
    // Room left: names come back strictly by importance, so a less important
    // button never has its name while a more important one does not.
    for (const c of [...items].sort((a, b) => a.rank - b.rank)) {
      if (look[c.id] !== "icon" || c.round) continue;
      look[c.id] = "both";
      if (width() > available) { look[c.id] = "icon"; break; }
    }
  }
  return look;
}

/* ---- the page --------------------------------------------------------- */

function iconOf(el) {
  const i = el.querySelector("i.bi");
  return i ? [...i.classList].find((c) => c.startsWith("bi-")) : "bi-dot";
}

function badgeOf(el) {
  const b = el.querySelector(".notif-badge:not(.hidden)");
  return b ? b.textContent.trim() : "";
}

function targetOf(el) {
  return el.matches("a, button") ? el : el.querySelector("a, button");
}

export function startHeaderFit({ doc = document, win = window, csrf = () => "", post = null } = {}) {
  const row = doc.querySelector("[data-header-fit]");
  if (!row || !win.ResizeObserver) return null;

  const send = post || ((url, body) => win.fetch(url, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
    body: JSON.stringify(body),
  }));

  // The More button, always last: the folded controls, then the style chooser.
  const more = doc.createElement("div");
  more.className = "fit-more";
  more.innerHTML = '<button type="button" class="btn-icon fit-more-btn" aria-haspopup="true" aria-expanded="false" '
    + 'title="More" aria-label="More"><i class="bi bi-three-dots"></i><span class="notif-badge hidden"></span></button>'
    + '<div class="fit-menu" role="menu" hidden></div>';
  row.append(more);
  const moreBtn = more.querySelector(".fit-more-btn");
  const menu = more.querySelector(".fit-menu");
  const moreBadge = more.querySelector(".notif-badge");

  let folded = [];
  let scheduled = false;
  let observer = null;

  function controls() {
    return [...row.querySelectorAll("[data-fit]")].filter((el) => !el.hidden && !el.closest("[hidden]"));
  }

  function measure(el) {
    el.classList.remove("fit-folded");
    const width = {};
    for (const look of ["both", "icon", "word"]) {
      el.dataset.look = look;
      width[look] = Math.ceil(el.getBoundingClientRect().width);
    }
    return width;
  }

  function fit() {
    scheduled = false;
    row.dataset.fitReady = "";
    const els = controls();
    const items = els.map((el, id) => ({
      id, el, rank: Number(el.dataset.fitRank) || 5, round: el.hasAttribute("data-fit-round"), width: measure(el),
    }));
    // Controls without data-fit (a page's own switch) keep their width.
    const fixed = [...row.children].filter((el) => el !== more && !el.querySelector("[data-fit]") && !el.hasAttribute("data-fit")
      && !el.hidden).reduce((sum, el) => sum + el.getBoundingClientRect().width + 8, 0);
    const reserved = fixed + Math.ceil(moreBtn.getBoundingClientRect().width || 44) + 8;
    const style = row.dataset.headerStyle || "auto";
    const look = plan(items, row.clientWidth, style, { reserved });
    for (const c of items) {
      c.el.dataset.look = look[c.id] === "more" ? (START[style] || "both") : look[c.id];
      c.el.classList.toggle("fit-folded", look[c.id] === "more");
    }
    // The measuring is an estimate (nested gaps); the row is the truth.
    const lowFirst = [...items].sort((a, b) => b.rank - a.rank);
    for (const c of lowFirst) {
      if (row.scrollWidth <= row.clientWidth + 1) break;
      if (!c.el.classList.contains("fit-folded")) c.el.classList.add("fit-folded");
    }
    folded = items.filter((c) => c.el.classList.contains("fit-folded")).sort((a, b) => a.rank - b.rank);
    const total = folded.reduce((n, c) => n + (Number(badgeOf(c.el)) || 0), 0);
    moreBadge.textContent = total > 99 ? "99+" : String(total);
    moreBadge.classList.toggle("hidden", total === 0);
    if (!menu.hidden) drawMenu();
    // What this fit changed itself is not a reason to fit again.
    if (observer) observer.takeRecords();
  }

  function refit() {
    if (scheduled) return;
    scheduled = true;
    win.requestAnimationFrame(fit);
  }

  function drawMenu() {
    menu.replaceChildren();
    for (const c of folded) {
      const item = doc.createElement("button");
      item.type = "button";
      item.className = "fit-menu-item";
      item.setAttribute("role", "menuitem");
      const icon = doc.createElement("i");
      icon.className = "bi " + iconOf(c.el);
      const name = doc.createElement("span");
      name.textContent = c.el.dataset.fit;
      item.append(icon, name);
      const badge = badgeOf(c.el);
      if (badge) {
        const b = doc.createElement("span");
        b.className = "fit-menu-badge";
        b.textContent = badge;
        item.append(b);
      }
      item.addEventListener("click", () => { close(); const t = targetOf(c.el); if (t) t.click(); });
      menu.append(item);
    }
    const title = doc.createElement("div");
    title.className = "fit-menu-title";
    title.textContent = "Show buttons as";
    menu.append(title);
    const current = row.dataset.headerStyle || "auto";
    for (const [value, label] of STYLES) {
      const choice = doc.createElement("button");
      choice.type = "button";
      choice.className = "fit-menu-item fit-style";
      choice.setAttribute("role", "menuitemradio");
      choice.setAttribute("aria-checked", String(value === current));
      choice.dataset.style = value;
      const tick = doc.createElement("i");
      tick.className = "bi " + (value === current ? "bi-check2" : "bi-dot");
      const text = doc.createElement("span");
      text.textContent = label;
      choice.append(tick, text);
      choice.addEventListener("click", () => choose(value));
      menu.append(choice);
    }
  }

  async function choose(value) {
    const before = row.dataset.headerStyle || "auto";
    row.dataset.headerStyle = value;
    fit();
    drawMenu();
    try {
      const r = await send(row.dataset.headerStyleUrl, { style: value });
      if (!r.ok) throw new Error(String(r.status));
    } catch (notSaved) {
      row.dataset.headerStyle = before;   // not saved: show what is saved
      fit();
      drawMenu();
    }
  }

  function open() { drawMenu(); menu.hidden = false; moreBtn.setAttribute("aria-expanded", "true"); }
  function close() { menu.hidden = true; moreBtn.setAttribute("aria-expanded", "false"); }
  moreBtn.addEventListener("click", (e) => { e.stopPropagation(); if (menu.hidden) open(); else close(); });
  doc.addEventListener("click", (e) => { if (!more.contains(e.target)) close(); });
  doc.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  new win.ResizeObserver(refit).observe(row);
  // The printer chip appears after the first status answer and its words
  // change: fit again then.
  observer = new win.MutationObserver(refit);
  observer.observe(row, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
  fit();
  return { fit, plan: () => folded.map((c) => c.el.dataset.fit) };
}
