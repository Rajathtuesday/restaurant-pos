/*
 * Shared "change a dish on a live order" controls, used by the Live Orders
 * board, the Running Order page and the billing screens so they all behave
 * the same way. Needs SweetAlert2 (window.Swal) on the page.
 *
 *   OrderEdit.reduce(item)            take one off a line ("2 naan -> 1")
 *   OrderEdit.remove(item)            cancel the whole line
 *   OrderEdit.cancelOrder(id, opts)   cancel a whole order
 *
 * item = {id, name, quantity, status} plus the kitchen hints from the
 * server (in_kitchen, made_locked, suggest_made, restock_needs_manager).
 * Each returns a promise that resolves to the server's JSON on success, or
 * null if the user backed out. Errors are shown as a toast and also resolve
 * to null.
 */
(function () {
  "use strict";

  var URLS = { reduce: "/reduce-item/0/", cancel: "/cancel-item/0/", cancelOrder: "/cancel-order/0/" };
  var REASONS = ["Customer changed mind", "Wrong order", "Out of stock", "Kitchen issue"];

  function urlFor(template, id) {
    return template.replace("/0/", "/" + encodeURIComponent(id) + "/");
  }

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute("content")) return meta.getAttribute("content");
    var m = document.cookie.match(/(?:^|;\s*)csrftoken2=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function isDark() {
    return document.documentElement.classList.contains("dark") || document.body.classList.contains("dark");
  }

  function toast(message, icon) {
    if (window.ui && typeof window.ui.toast === "function") return window.ui.toast(message, icon);
    window.Swal.fire({
      toast: true, position: "bottom-end", showConfirmButton: false, timer: 3000,
      icon: icon, title: message,
      background: isDark() ? "#1a1d24" : "#fff", color: isDark() ? "#f9fafb" : "#111",
    });
  }

  function injectStyles() {
    if (document.getElementById("oe-styles")) return;
    var css =
      ".oe-popup{border-radius:var(--border-radius,0)!important;padding:1.4rem 1.3rem 1.2rem!important}" +
      ".oe-popup .swal2-title{font-family:var(--font-display,'DM Serif Display',serif);font-weight:400;font-size:1.45rem;padding:0}" +
      ".oe-popup .swal2-actions{flex-wrap:wrap;gap:.4rem}" +
      ".oe-qty{display:flex;align-items:center;justify-content:center;gap:.9rem;margin:.4rem 0 1rem;font-family:var(--font-mono,'Space Mono',monospace)}" +
      ".oe-qty .oe-from{font-size:1.6rem;color:var(--text-muted,#737373)}" +
      ".oe-qty .oe-to{font-size:2.1rem;font-weight:700;color:var(--text-main,#111)}" +
      ".oe-qty .bi{color:var(--text-muted,#737373)}" +
      ".oe-hint{font-size:.85rem;color:var(--text-muted,#737373);margin:0 0 .9rem}" +
      ".oe-label{font-size:.68rem;text-transform:uppercase;letter-spacing:2px;color:var(--text-muted,#737373);margin:0 0 .5rem;text-align:left}" +
      ".oe-chips{display:flex;flex-wrap:wrap;gap:.45rem;margin-bottom:.7rem}" +
      ".oe-chip{min-height:44px;padding:.45rem .85rem;border:1px solid var(--border-color,#eaeaea);background:transparent;" +
      "color:var(--text-main,#111);font:inherit;font-size:.86rem;cursor:pointer;border-radius:var(--border-radius,0);transition:border-color .15s,background .15s}" +
      ".oe-chip:hover{border-color:var(--accent-gold,#c5a059)}" +
      ".oe-chip[aria-pressed=true]{border-color:var(--accent-gold,#c5a059);background:var(--accent-gold,#c5a059);color:#fff}" +
      ".oe-chip:focus-visible,.oe-opt:focus-visible{outline:2px solid var(--accent-gold,#c5a059);outline-offset:2px}" +
      ".oe-other{width:100%;min-height:44px;padding:.55rem .7rem;border:1px solid var(--border-color,#eaeaea);background:transparent;" +
      "color:var(--text-main,#111);font:inherit;font-size:.9rem;border-radius:var(--border-radius,0)}" +
      ".oe-other:focus{outline:none;border-color:var(--accent-gold,#c5a059)}" +
      ".oe-stock{display:grid;gap:.45rem;margin:0 0 1rem}" +
      ".oe-opt{display:flex;flex-direction:column;align-items:flex-start;gap:.15rem;width:100%;min-height:44px;padding:.6rem .8rem;text-align:left;" +
      "border:1px solid var(--border-color,#eaeaea);background:transparent;color:var(--text-main,#111);font:inherit;cursor:pointer;border-radius:var(--border-radius,0);transition:border-color .15s,background .15s}" +
      ".oe-opt:hover:not(:disabled){border-color:var(--accent-gold,#c5a059)}" +
      ".oe-opt[aria-checked=true]{border-color:var(--accent-gold,#c5a059);box-shadow:inset 3px 0 0 var(--accent-gold,#c5a059)}" +
      ".oe-opt:disabled{cursor:not-allowed;opacity:.55}" +
      ".oe-opt-t{font-size:.92rem;font-weight:600;display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}" +
      ".oe-opt-d{font-size:.8rem;color:var(--text-muted,#737373)}" +
      ".oe-tag{font-size:.64rem;font-weight:600;text-transform:uppercase;letter-spacing:1px;padding:.1rem .4rem;border:1px solid var(--border-color,#eaeaea);color:var(--text-muted,#737373)}" +
      ".oe-fixed{display:flex;gap:.55rem;align-items:flex-start;text-align:left;font-size:.85rem;padding:.6rem .8rem;margin:0 0 1rem;" +
      "border:1px solid var(--border-color,#eaeaea);color:var(--text-main,#111);box-shadow:inset 3px 0 0 var(--danger,#ef4444)}" +
      ".oe-fixed .bi{color:var(--danger,#ef4444);margin-top:.1rem}" +
      ".oe-list{list-style:none;padding:0;margin:0 0 1rem;text-align:left;border-top:1px solid var(--border-color,#eaeaea)}" +
      ".oe-list li{display:flex;justify-content:space-between;gap:.8rem;padding:.5rem 0;border-bottom:1px solid var(--border-color,#eaeaea);font-size:.9rem;color:var(--text-main,#111)}" +
      ".oe-list .oe-st{font-size:.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted,#737373);white-space:nowrap}" +
      ".oe-confirm,.oe-cancel,.oe-deny{border-radius:var(--border-radius,0)!important;min-height:44px;text-transform:uppercase;letter-spacing:1px;font-size:.78rem!important;font-weight:600}" +
      ".oe-confirm.oe-danger{background:var(--danger,#ef4444)!important}" +
      ".oe-deny{background:transparent!important;color:var(--text-main,#111)!important;border:1px solid var(--accent-gold,#c5a059)!important}";
    var el = document.createElement("style");
    el.id = "oe-styles";
    el.textContent = css;
    document.head.appendChild(el);
  }

  function reasonBlock(optional) {
    var chips = REASONS.map(function (r) {
      return '<button type="button" class="oe-chip" aria-pressed="false" data-reason="' + esc(r) + '">' + esc(r) + "</button>";
    }).join("");
    return (
      '<p class="oe-label" id="oe-reason-label">' + (optional ? "Why? (optional)" : "Why?") + "</p>" +
      '<div class="oe-chips" role="group" aria-labelledby="oe-reason-label">' + chips + "</div>" +
      '<input class="oe-other" id="oe-other" type="text" maxlength="200" placeholder="' +
      (optional ? "Other reason" : "Other reason (optional if you picked one)") + '">'
    );
  }

  function wireReasons(popup) {
    var chips = popup.querySelectorAll(".oe-chip");
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var on = chip.getAttribute("aria-pressed") !== "true";
        chips.forEach(function (c) { c.setAttribute("aria-pressed", "false"); });
        chip.setAttribute("aria-pressed", on ? "true" : "false");
      });
    });
  }

  function readReason(popup) {
    var other = popup.querySelector("#oe-other");
    var typed = other ? (other.value || "").trim() : "";
    if (typed) return typed;
    var picked = popup.querySelector('.oe-chip[aria-pressed="true"]');
    return picked ? picked.getAttribute("data-reason") : "";
  }

  // ── made or not made ────────────────────────────────────────────────

  function kitchenLine(item) {
    if (item.status === "preparing") return "The kitchen is cooking it.";
    if (item.status === "sent") return item.suggest_made ? "It went to the kitchen over 10 minutes ago." : "It just went to the kitchen.";
    return "";
  }

  // The ingredients were taken from stock when the ticket went to the
  // kitchen. This asks what happened to them. Ready and served dishes were
  // made, so there is nothing to ask.
  function stockBlock(item) {
    if (item.made_locked) {
      return (
        '<div class="oe-fixed" role="note"><i class="bi bi-trash3" aria-hidden="true"></i><span>' +
        "Already " + (item.status === "served" ? "served" : "ready") + ", so the ingredients count as wastage.</span></div>"
      );
    }
    var made = !!item.suggest_made;
    var lockRestock = !!item.restock_needs_manager;
    function opt(value, title, desc, checked, disabled, tag) {
      return (
        '<button type="button" role="radio" class="oe-opt" data-made="' + value + '" aria-checked="' + (checked ? "true" : "false") + '"' +
        (disabled ? ' disabled aria-disabled="true"' : "") + ">" +
        '<span class="oe-opt-t">' + title + (tag ? ' <span class="oe-tag">' + tag + "</span>" : "") + "</span>" +
        '<span class="oe-opt-d">' + desc + "</span></button>"
      );
    }
    var line = kitchenLine(item);
    return (
      (line ? '<p class="oe-hint">' + esc(line) + "</p>" : "") +
      '<p class="oe-label" id="oe-stock-label">Did the kitchen make it?</p>' +
      '<div class="oe-stock" role="radiogroup" aria-labelledby="oe-stock-label">' +
      opt("1", "Made, count as wastage", "The food is thrown away. It shows on the wastage report.", made, false, "") +
      opt("0", "Not made, put stock back", "The ingredients go back on the shelf.", !made, lockRestock, lockRestock ? "Manager needed" : "") +
      "</div>"
    );
  }

  function wireStock(popup) {
    var opts = popup.querySelectorAll(".oe-opt");
    opts.forEach(function (o) {
      o.addEventListener("click", function () {
        if (o.disabled) return;
        opts.forEach(function (x) { x.setAttribute("aria-checked", x === o ? "true" : "false"); });
      });
    });
  }

  function readMade(popup, item) {
    if (!item || !item.in_kitchen) return null;
    if (item.made_locked) return true;
    var picked = popup.querySelector('.oe-opt[aria-checked="true"]');
    return picked ? picked.getAttribute("data-made") === "1" : !!item.suggest_made;
  }

  function compHint(item) {
    return item.status === "served"
      ? '<p class="oe-hint">If the guest is keeping it free of charge, mark it complimentary instead.</p>'
      : "";
  }

  function doneText(name, data, made) {
    var base = name + (data && data.remaining > 0 ? " is now " + data.remaining : " cancelled");
    if (made === true) return base + ", counted as wastage";
    if (made === false) return base + ", stock put back";
    return base;
  }

  // ── the sheet ───────────────────────────────────────────────────────

  function ask(opts) {
    injectStyles();
    return window.Swal.fire({
      title: esc(opts.title),
      html: opts.html,
      showConfirmButton: !!opts.confirmText,
      showDenyButton: !!opts.denyText,
      showCancelButton: true,
      confirmButtonText: esc(opts.confirmText || ""),
      denyButtonText: esc(opts.denyText || ""),
      cancelButtonText: esc(opts.cancelText || "Keep it"),
      reverseButtons: true,
      focusCancel: false,
      background: isDark() ? "var(--panel-bg, #1a1d24)" : "var(--panel-bg, #fff)",
      color: "var(--text-main, #111)",
      customClass: {
        popup: "oe-popup",
        confirmButton: "oe-confirm" + (opts.danger ? " oe-danger" : ""),
        denyButton: "oe-deny",
        cancelButton: "oe-cancel",
      },
      buttonsStyling: true,
      confirmButtonColor: "var(--accent-gold, #c5a059)",
      cancelButtonColor: "transparent",
      didOpen: function (popup) {
        var cancel = popup.querySelector(".swal2-cancel");
        if (cancel) { cancel.style.color = "var(--text-muted, #737373)"; cancel.style.border = "1px solid var(--border-color, #eaeaea)"; }
        if (popup.querySelector(".oe-chip")) wireReasons(popup);
        if (popup.querySelector(".oe-opt")) wireStock(popup);
      },
      preConfirm: function () {
        var popup = window.Swal.getPopup();
        var reason = popup.querySelector(".oe-chips") ? readReason(popup) : "";
        if (opts.needReason && !reason) {
          window.Swal.showValidationMessage("Pick a reason or type one, it goes in the void report.");
          return false;
        }
        return { reason: reason, made: readMade(popup, opts.item) };
      },
    }).then(function (r) {
      if (r.isConfirmed) return r.value;
      if (r.isDenied) return { denied: true };
      return null;
    });
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.success === false) {
          var err = new Error(data.error || "Could not save the change (HTTP " + res.status + ").");
          err.status = res.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    }, function () {
      throw new Error("Network error, the change was not saved.");
    });
  }

  function editBody(answer, extra) {
    var body = Object.assign({ reason: answer.reason }, extra || {});
    if (answer.made === true || answer.made === false) body.made = answer.made;
    return body;
  }

  function reduce(item) {
    var last = item.quantity <= 1;
    var html =
      '<div class="oe-qty" aria-label="Quantity ' + esc(item.quantity) + " to " + esc(item.quantity - 1) + '">' +
      '<span class="oe-from">' + esc(item.quantity) + '</span><i class="bi bi-arrow-right" aria-hidden="true"></i>' +
      '<span class="oe-to">' + esc(item.quantity - 1) + "</span></div>" +
      (item.in_kitchen
        ? compHint(item) + stockBlock(item) + reasonBlock(false)
        : '<p class="oe-hint">Not sent to the kitchen yet, so this is just a basket change.</p>');
    return ask({
      title: item.name,
      html: html,
      confirmText: last ? "Remove dish" : "Remove 1",
      danger: last,
      needReason: !!item.in_kitchen,
      item: item,
    }).then(function (answer) {
      if (!answer) return null;
      return post(urlFor(URLS.reduce, item.id), editBody(answer, { reduce_by: 1 }))
        .then(function (data) { toast(doneText(item.name, data, answer.made), "success"); return data; })
        .catch(function (err) { toast(err.message, "error"); return null; });
    });
  }

  function remove(item) {
    var html =
      '<p class="oe-hint">Cancels all ' + esc(item.quantity) + " of this dish on the order.</p>" +
      (item.in_kitchen ? compHint(item) + stockBlock(item) + reasonBlock(false) : "");
    return ask({
      title: "Cancel " + item.name + "?",
      html: html,
      confirmText: "Cancel dish",
      danger: true,
      needReason: !!item.in_kitchen,
      item: item,
    }).then(function (answer) {
      if (!answer) return null;
      return post(urlFor(URLS.cancel, item.id), editBody(answer))
        .then(function (data) { toast(doneText(item.name, null, answer.made), "success"); return data; })
        .catch(function (err) { toast(err.message, "error"); return null; });
    });
  }

  // ── cancelling a whole order ────────────────────────────────────────

  var STATUS_LABEL = { sent: "In the kitchen", preparing: "Cooking", ready: "Ready", served: "Served" };

  // opts.billUrl: where "Keep them" takes the user. Leave it out on a page
  // that already is the bill; the button then just closes the sheet.
  function cancelOrder(orderId, opts) {
    opts = opts || {};
    var url = urlFor(URLS.cancelOrder, orderId);
    return ask({
      title: "Cancel this whole order?",
      html:
        '<p class="oe-hint">Every dish on it is cancelled and the table is freed. This cannot be undone.</p>' +
        reasonBlock(true),
      confirmText: "Cancel order",
      danger: true,
    }).then(function (answer) {
      if (!answer) return null;
      return post(url, { reason: answer.reason })
        .then(function (data) { toast("Order cancelled", "success"); return data; })
        .catch(function (err) {
          if (err.status === 409 && err.data && err.data.needs_confirm) {
            return confirmMade(url, err.data, answer.reason, opts);
          }
          toast(err.message, "error");
          return null;
        });
    });
  }

  function confirmMade(url, data, reason, opts) {
    var list = (data.made_items || []).map(function (i) {
      return "<li><span>" + esc(i.quantity) + " × " + esc(i.name) + '</span><span class="oe-st">' +
        esc(STATUS_LABEL[i.status] || i.status) + "</span></li>";
    }).join("");
    var managerOnly = !!data.needs_manager;
    var html =
      '<p class="oe-hint">' + esc(data.error) + "</p>" +
      '<ul class="oe-list">' + list + "</ul>" +
      (managerOnly
        ? '<div class="oe-fixed" role="note"><i class="bi bi-lock" aria-hidden="true"></i><span>' +
          "Some of these were already served. Only a manager can cancel served dishes.</span></div>"
        : '<p class="oe-hint">Cancelling them counts their ingredients as wastage.</p>' + (reason ? "" : reasonBlock(false)));
    return ask({
      title: "These dishes were already made",
      html: html,
      confirmText: managerOnly ? "" : "Cancel them as wastage",
      danger: true,
      denyText: opts.billUrl ? "Keep them, go to bill" : "Keep them and bill",
      cancelText: "Back",
      needReason: !managerOnly && !reason,
    }).then(function (answer) {
      if (!answer) return null;
      if (answer.denied) {
        if (opts.billUrl) window.location.href = opts.billUrl;
        return null;
      }
      return post(url, { reason: reason || answer.reason, include_made: true })
        .then(function (d) { toast("Order cancelled, made dishes counted as wastage", "success"); return d; })
        .catch(function (err) { toast(err.message, "error"); return null; });
    });
  }

  // True only while a sheet the user is working in is open. A toast is also a
  // Swal popup, so Swal.isVisible() alone would pause refreshes right after
  // a successful edit, which is exactly when the list needs to update.
  function sheetOpen() {
    if (!window.Swal || !window.Swal.isVisible()) return false;
    var popup = window.Swal.getPopup();
    return !!(popup && !popup.classList.contains("swal2-toast"));
  }

  window.OrderEdit = {
    configure: function (urls) { Object.assign(URLS, urls || {}); },
    sheetOpen: sheetOpen,
    reduce: reduce,
    remove: remove,
    cancelOrder: cancelOrder,
    esc: esc,
    toast: toast,
    post: post,
  };
})();
