/*
 * The floor plan as markup: what tables-data sends, turned into the alert
 * strip, the cards (grouped by section) and the four numbers at the top.
 *
 * Nothing here touches the page or the server, so node can test it with a
 * list of tables. Every name a person typed (a table, a section, a waiter)
 * is escaped where it is drawn.
 */
import { emptyStateHtml } from "../core/empty_state.js";
import { esc } from "../order_edit.js";
import { withId } from "../billing/urls.js";

const ACTIVE = ["ordering", "preparing", "served", "ready"];
const STUCK_AFTER = 30;   // minutes
const SLOW_AFTER = 15;

/* status -> [text colour class, top line colour class] */
const LOOK = {
  free: ["status-free", "bg-free"],
  ordering: ["status-ordering", "bg-order"],
  preparing: ["status-preparing", "bg-prepare"],
  served: ["status-served", "bg-serve"],
  ready: ["status-served", "bg-serve"],
  billing: ["status-billing", "bg-billing"],
  cleaning: ["status-cleaning", "bg-clean"],
  merged: ["status-merged", "bg-merged"],
  needs_approval: ["status-approval", "bg-approval"],
};

export const statusOf = (table) => table.status || "free";
const isActive = (table) => ACTIVE.includes(statusOf(table));

/* The numbers the strip and the metric cards show. */
export function summarise(tables) {
  const count = (test) => tables.filter(test).length;
  const total = tables.length;
  const free = count((t) => statusOf(t) === "free");
  const idle = count((t) => statusOf(t) === "cleaning" || statusOf(t) === "merged");
  return {
    total,
    free,
    active: total - free - idle,
    billing: count((t) => statusOf(t) === "billing"),
    stuck: count((t) => isActive(t) && (t.elapsed || 0) > STUCK_AFTER),
    approval: count((t) => statusOf(t) === "needs_approval"),
  };
}

/* Names the way people count them: T2 before T10 (orders/services/table_order.py). */
const counting = (a, b) => String(a).localeCompare(String(b), "en", { numeric: true, sensitivity: "base" });

/* [[section, tables], ...] in section order; tables with no section first,
   each section's tables T1, T2 ... T10, never T1, T10, T2. */
export function bySection(tables) {
  const sections = new Map();
  tables.forEach((t) => {
    const name = t.section || "";
    if (!sections.has(name)) sections.set(name, []);
    sections.get(name).push(t);
  });
  sections.forEach((list) => list.sort((a, b) => counting(a.name, b.name)));
  return Array.from(sections.entries()).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

export function addresses(urls) {
  return {
    billing: (tableId) => urls.billing + "?table=" + encodeURIComponent(tableId),
    bill: (orderId) => withId(urls.bill, orderId),
    order: (orderId) => withId(urls.running_order, orderId),
  };
}

const button = ({ action, label, cls = "", attrs = {}, title = "" }) => {
  const data = Object.entries(attrs).map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
  return `<button type="button" data-action="${action}"${data} class="${cls}"${title ? ` title="${esc(title)}"` : ""}>${label}</button>`;
};
const stacked = (icon, text) => `<i class="bi ${icon} d-block mb-1 mx-auto fs-5"></i> ${text}`;

/* The timer chip and the buttons of one card. */
function cardParts(t, to) {
  const status = statusOf(t);
  const [statusClass] = LOOK[status] || LOOK.free;
  const go = (url, label, cls, title) => button({ action: "table.go", attrs: { url }, label, cls, title });
  const more = button({ action: "table.more", attrs: { id: t.id }, label: stacked("bi-three-dots", "More"), cls: "btn-card-action", title: "More Options" });

  if (status === "free") {
    let actions = go(to.billing(t.id), '<i class="bi bi-plus-lg me-1"></i> New Order', "btn-card-action w-100");
    if (t.is_primary) {
      actions += button({ action: "table.unmerge", attrs: { id: t.id }, label: '<i class="bi bi-scissors"></i> Unmerge', cls: "btn-card-action btn-card-danger w-100" });
    }
    return { timer: "", actions };
  }
  if (status === "merged") {
    return {
      timer: '<div class="table-timer status-merged"><i class="bi bi-link"></i> Linked</div>',
      // Unmerge here too: a joined table's card must be able to undo the
      // merge, or a tangled floor has no button that can (19 Nov 2026).
      actions: go(to.billing(t.primary_table), `Go to Primary (${esc(t.primary_table_name || t.primary_table)})`, "btn-card-action w-100")
        + button({ action: "table.unmerge", attrs: { id: t.id }, label: '<i class="bi bi-scissors"></i> Unmerge', cls: "btn-card-action btn-card-danger w-100" }),
    };
  }
  if (status === "cleaning") {
    return {
      timer: '<div class="table-timer text-muted"><i class="bi bi-stars"></i> Table Dirty</div>',
      actions: button({ action: "table.clean", attrs: { id: t.id }, label: "Mark Clean", cls: "btn-card-action btn-card-primary w-100" }),
    };
  }
  if (status === "billing") {
    return {
      timer: '<div class="table-timer status-billing"><i class="bi bi-receipt"></i> Billing</div>',
      actions: go(to.bill(t.order_id), '<i class="bi bi-credit-card me-1"></i> Pay Bill', "btn-card-action btn-card-primary btn-card-billing w-100") + more,
    };
  }
  if (status === "needs_approval") {
    return {
      timer: '<div class="table-timer bg-approval text-white"><i class="bi bi-exclamation-triangle"></i> Review</div>',
      actions: button({ action: "table.more", attrs: { id: t.id }, label: '<i class="bi bi-check-circle me-1"></i> Approve QR', cls: "btn-card-action btn-card-primary btn-card-approval w-100" }),
    };
  }
  return {
    timer: t.elapsed !== undefined ? `<div class="table-timer ${statusClass}"><i class="bi bi-clock"></i> ${esc(t.elapsed)}m</div>` : "",
    actions: go(to.billing(t.id), stacked("bi-cart-plus", "Add"), "btn-card-action", "Add Items")
      + button({ action: "table.bill", attrs: { order: t.order_id || "" }, label: stacked("bi-receipt", "Bill"), cls: "btn-card-action", title: "Generate Bill" })
      + more,
  };
}

function card(t, to) {
  const status = statusOf(t);
  const [statusClass, lineClass] = LOOK[status] || LOOK.free;
  const { timer, actions } = cardParts(t, to);

  let urgency = "";
  if (isActive(t)) {
    const elapsed = t.elapsed || 0;
    if (elapsed > STUCK_AFTER) urgency = "card-urgent";
    else if (elapsed > SLOW_AFTER) urgency = "card-warn";
  }

  const waiter = t.waiter_initials
    ? `<div class="waiter-badge${t.waiter_initials === "QR" ? " qr-badge" : ""}" title="Waiter: ${esc(t.waiter_name)}">${esc(t.waiter_initials)}</div>`
    : "";
  const cooking = t.cooking_items > 0
    ? `<span class="cooking-badge"><i class="bi bi-fire"></i> ${esc(t.cooking_items)} in kitchen</span>`
    : "";
  let merge = "";
  if (status === "merged") merge = `<div class="occupancy"><i class="bi bi-link-45deg"></i> Linked to ${esc(t.primary_table_name || "Primary")}</div>`;
  else if (t.is_primary) merge = `<div class="occupancy"><i class="bi bi-diagram-3"></i> Merged w/ ${esc(t.merged_with_names)}</div>`;

  return `<div class="col-12 col-md-6 col-xl-4 col-xxl-3 d-flex">
    <div class="table-card w-100 shadow-sm ${urgency}" data-table="${esc(t.id)}" data-status="${esc(status)}">
      <div class="table-status-line ${lineClass}"></div>
      ${waiter}
      <div class="table-header">
        <div class="d-flex align-items-center flex-wrap"><h3 class="table-name mb-0">${esc(t.name)}</h3></div>
        ${timer}
      </div>
      <div class="table-status ${statusClass}">${esc(status.replace("_", " "))}</div>
      ${cooking}
      ${merge}
      <div class="mt-auto"><div class="card-actions">${actions}</div></div>
    </div>
  </div>`;
}

function strip(sum) {
  const clean = sum.stuck === 0 && sum.approval === 0;
  let html = `<div class="col-12"><div class="alert-strip${clean ? " clean" : ""}"><span class="alert-strip-label">Floor</span>`;
  if (sum.stuck > 0) html += `<span class="alert-chip chip-danger"><i class="bi bi-exclamation-triangle-fill"></i> ${sum.stuck} stuck &gt;30m</span>`;
  if (sum.approval > 0) html += `<span class="alert-chip chip-warn"><i class="bi bi-hourglass-split"></i> ${sum.approval} need approval</span>`;
  if (sum.billing > 0) html += `<span class="alert-chip chip-info"><i class="bi bi-receipt"></i> ${sum.billing} awaiting payment</span>`;
  if (clean) html += '<span class="alert-chip chip-ok"><i class="bi bi-check-circle-fill"></i> All clear</span>';
  return html + "</div></div>";
}

/* The whole grid: the strip, then each section's heading and cards. */
export function floorHtml(tables, urls) {
  if (!tables.length) {
    return `<div class="col-12">${emptyStateHtml({
      icon: "grid-3x3-gap", title: "No tables yet",
      text: "Add your tables and the floor shows each one: free, ordering, cooking, waiting to pay.",
      href: urls.setup_tables, label: "Add tables",
    })}</div>`;
  }
  const to = addresses(urls);
  let html = strip(summarise(tables));
  bySection(tables).forEach(([section, list]) => {
    if (section) html += `<div class="col-12"><div class="section-header"><i class="bi bi-grid-3x3-gap me-1"></i>${esc(section)}</div></div>`;
    list.forEach((t) => { html += card(t, to); });
  });
  return html;
}

export function failedHtml(message) {
  return `<div class="col-12 text-center text-danger py-5"><i class="bi bi-exclamation-triangle fs-1 d-block mb-3"></i>Failed to load floor plan. ${esc(message || "")}</div>`;
}

/* Each dish a guest ordered by QR that waits for a yes, with a tick to
   accept it and a cross to not accept it, one tap each, right at the table. */
export function guestDishes(t) {
  const dishes = Array.isArray(t.guest_items) ? t.guest_items : [];
  if (!dishes.length) return "";
  const rows = dishes.map((d) => {
    const label = `${d.quantity} x ${d.name}`;
    return `<li class="guest-dish" data-guest-item="${esc(d.id)}">
      <span class="guest-dish-name">${esc(label)}</span>
      ${button({ action: "table.approveOne", attrs: { order: t.order_id, item: d.id, name: d.name }, label: '<i class="bi bi-check-lg" aria-hidden="true"></i>', cls: "guest-dish-btn is-yes", title: "Approve " + label })}
      ${button({ action: "table.refuse", attrs: { item: d.id, name: d.name, quantity: d.quantity }, label: '<i class="bi bi-x-lg" aria-hidden="true"></i>', cls: "guest-dish-btn is-no", title: "Not accepted: " + label })}
    </li>`;
  }).join("");
  return `<ul class="guest-dishes" aria-label="Guest dishes waiting for approval">${rows}</ul>`;
}

/* The buttons of the "More" sheet for one table. */
export function moreSheet(t, urls) {
  const to = addresses(urls);
  const status = statusOf(t);
  const go = (url, label, cls = "") => button({ action: "table.go", attrs: { url }, label, cls: `btn-luxury w-100 ${cls}`.trim() });
  const unmerge = t.is_primary
    ? button({ action: "table.unmerge", attrs: { id: t.id }, label: "Unmerge Tables", cls: "btn-luxury w-100 btn-sheet-danger" })
    : "";

  if (status === "cleaning") {
    return {
      note: "Table needs to be cleaned",
      buttons: button({ action: "table.clean", attrs: { id: t.id }, label: '<i class="bi bi-stars me-2"></i>Mark as Cleaned', cls: "btn-luxury w-100 btn-sheet-clean" }),
    };
  }
  if (status === "billing") {
    return {
      note: `Order Active (${status})`,
      buttons: go(to.bill(t.order_id), "Pay Bill", "btn-sheet-billing") + go(to.billing(t.id), "Add More Items") + unmerge,
    };
  }
  if (status === "needs_approval") {
    return {
      note: "Customer added items via QR",
      buttons: guestDishes(t)
        + button({ action: "table.approve", attrs: { order: t.order_id }, label: '<i class="bi bi-check-circle me-2"></i>Approve all', cls: "btn-luxury w-100 btn-sheet-approval" })
        + go(to.order(t.order_id), "Show Running Order"),
    };
  }
  return {
    note: `Order Active (${status})`,
    buttons: go(to.billing(t.id), "Add Items")
      + go(to.order(t.order_id), "Show Running Order")
      + button({ action: "table.transfer", attrs: { id: t.id }, label: "Transfer Table", cls: "btn-luxury w-100" })
      + button({ action: "table.bill", attrs: { order: t.order_id || "" }, label: "Generate Bill", cls: "btn-luxury w-100 btn-sheet-bill" })
      + unmerge,
  };
}

/* ---- the Combine, Transfer and Tables sheets ------------------------------ */

/* An order can move while it is still open: not yet billed, not a linked table. */
export const canTransfer = (t) => Boolean(t.order_id) && !["billing", "merged", "free", "cleaning"].includes(statusOf(t));

const option = (t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`;
const placeholder = (text) => `<option value="" disabled selected>${text}</option>`;

/* The four pickers. A destination is always a free table: moving onto an
   occupied one would clobber its order. An empty list says why. */
export function pickerOptions(tables) {
  const free = tables.filter((t) => statusOf(t) === "free").map(option).join("");
  const none = '<option value="" disabled>No free tables available right now</option>';
  const moving = tables.filter(canTransfer).map(option).join("");
  return {
    mergePrimary: placeholder("Select Primary Table...") + tables.map(option).join(""),
    mergeSecondary: placeholder("Select a table to merge...") + (free || none),
    transferFrom: placeholder("Select source table...") + (moving || '<option value="" disabled>No table has an order to move</option>'),
    transferTo: placeholder("Select destination table...") + (free || none),
  };
}

export function manageRows(tables) {
  return tables.map((t) => `
    <tr data-row="${esc(t.id)}">
      <td><input type="text" data-field="name" aria-label="Table name" class="form-control form-select-luxury py-1 m-0" value="${esc(t.name)}"></td>
      <td><input type="text" data-field="section" aria-label="Section" class="form-control form-select-luxury py-1 m-0" value="${esc(t.section || "")}"></td>
      <td><button type="button" class="btn btn-sm btn-outline-secondary py-1" data-action="manage.qr" data-id="${esc(t.id)}" aria-label="QR code"><i class="bi bi-qr-code"></i></button></td>
      <td><button type="button" class="btn btn-sm btn-dark py-1" data-action="manage.save" data-id="${esc(t.id)}">Save</button></td>
    </tr>`).join("");
}

/* What the sheets are drawn from. While it is unchanged they are left
   alone, so the 5-second refresh does not wipe a name being typed or
   close a picker that is open. */
export function sheetsKey(tables) {
  return JSON.stringify(tables.map((t) => [t.id, t.name, t.section || "", t.qr_token || "", statusOf(t) === "free", canTransfer(t)]));
}

export function menuLink(origin, menuPath, token) {
  return `${origin}${menuPath}?table_token=${encodeURIComponent(token)}`;
}
