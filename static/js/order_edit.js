/*
 * Shared "change a dish on a live order" controls, used by the Live Orders
 * board and the Running Order page so both behave the same way.
 * Needs SweetAlert2 (window.Swal) on the page.
 *
 *   OrderEdit.reduce(item)  take one off a line ("2 naan -> 1")
 *   OrderEdit.remove(item)  cancel the whole line
 *
 * item = {id, name, quantity, in_kitchen}. Both return a promise that
 * resolves to the server's JSON on success, or null if the user backed out.
 * Errors are shown as a toast and also resolve to null.
 */
(function () {
  "use strict";

  var URLS = { reduce: "/reduce-item/0/", cancel: "/cancel-item/0/" };
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
      ".oe-chip:focus-visible{outline:2px solid var(--accent-gold,#c5a059);outline-offset:2px}" +
      ".oe-other{width:100%;min-height:44px;padding:.55rem .7rem;border:1px solid var(--border-color,#eaeaea);background:transparent;" +
      "color:var(--text-main,#111);font:inherit;font-size:.9rem;border-radius:var(--border-radius,0)}" +
      ".oe-other:focus{outline:none;border-color:var(--accent-gold,#c5a059)}" +
      ".oe-confirm,.oe-cancel{border-radius:var(--border-radius,0)!important;min-height:44px;text-transform:uppercase;letter-spacing:1px;font-size:.78rem!important;font-weight:600}" +
      ".oe-confirm.oe-danger{background:var(--danger,#ef4444)!important}";
    var el = document.createElement("style");
    el.id = "oe-styles";
    el.textContent = css;
    document.head.appendChild(el);
  }

  function reasonBlock() {
    var chips = REASONS.map(function (r) {
      return '<button type="button" class="oe-chip" aria-pressed="false" data-reason="' + esc(r) + '">' + esc(r) + "</button>";
    }).join("");
    return (
      '<p class="oe-label" id="oe-reason-label">Why?</p>' +
      '<div class="oe-chips" role="group" aria-labelledby="oe-reason-label">' + chips + "</div>" +
      '<input class="oe-other" id="oe-other" type="text" maxlength="200" placeholder="Other reason (optional if you picked one)">'
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
    var typed = (popup.querySelector("#oe-other").value || "").trim();
    if (typed) return typed;
    var picked = popup.querySelector('.oe-chip[aria-pressed="true"]');
    return picked ? picked.getAttribute("data-reason") : "";
  }

  function ask(opts) {
    injectStyles();
    return window.Swal.fire({
      title: esc(opts.title),
      html: opts.html,
      showCancelButton: true,
      confirmButtonText: esc(opts.confirmText),
      cancelButtonText: "Keep it",
      reverseButtons: true,
      focusCancel: false,
      background: isDark() ? "var(--panel-bg, #1a1d24)" : "var(--panel-bg, #fff)",
      color: "var(--text-main, #111)",
      customClass: {
        popup: "oe-popup",
        confirmButton: "oe-confirm" + (opts.danger ? " oe-danger" : ""),
        cancelButton: "oe-cancel",
      },
      buttonsStyling: true,
      confirmButtonColor: "var(--accent-gold, #c5a059)",
      cancelButtonColor: "transparent",
      didOpen: function (popup) {
        var cancel = popup.querySelector(".swal2-cancel");
        if (cancel) { cancel.style.color = "var(--text-muted, #737373)"; cancel.style.border = "1px solid var(--border-color, #eaeaea)"; }
        if (opts.needReason) wireReasons(popup);
      },
      preConfirm: function () {
        if (!opts.needReason) return "";
        var reason = readReason(window.Swal.getPopup());
        if (!reason) {
          window.Swal.showValidationMessage("Pick a reason or type one, it goes in the void report.");
          return false;
        }
        return reason;
      },
    }).then(function (r) { return r.isConfirmed ? { reason: r.value || "" } : null; });
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
          throw new Error(data.error || "Could not save the change (HTTP " + res.status + ").");
        }
        return data;
      });
    }, function () {
      throw new Error("Network error, the change was not saved.");
    });
  }

  function reduce(item) {
    var last = item.quantity <= 1;
    var html =
      '<div class="oe-qty" aria-label="Quantity ' + item.quantity + " to " + (item.quantity - 1) + '">' +
      '<span class="oe-from">' + item.quantity + '</span><i class="bi bi-arrow-right" aria-hidden="true"></i>' +
      '<span class="oe-to">' + (item.quantity - 1) + "</span></div>" +
      (item.in_kitchen
        ? '<p class="oe-hint">Already with the kitchen, so it goes on the void report and the stock is put back.</p>' + reasonBlock()
        : '<p class="oe-hint">Not sent to the kitchen yet, so this is just a basket change.</p>');
    return ask({
      title: item.name,
      html: html,
      confirmText: last ? "Remove dish" : "Remove 1",
      danger: last,
      needReason: !!item.in_kitchen,
    }).then(function (answer) {
      if (!answer) return null;
      return post(urlFor(URLS.reduce, item.id), { reduce_by: 1, reason: answer.reason })
        .then(function (data) {
          toast(data.remaining > 0 ? item.name + " is now " + data.remaining : item.name + " removed", "success");
          return data;
        })
        .catch(function (err) { toast(err.message, "error"); return null; });
    });
  }

  function remove(item) {
    var html =
      '<p class="oe-hint">Cancels all ' + esc(item.quantity) + " of this dish on the order." +
      (item.in_kitchen ? " It goes on the void report and the stock is put back." : "") + "</p>" +
      (item.in_kitchen ? reasonBlock() : "");
    return ask({
      title: "Cancel " + item.name + "?",
      html: html,
      confirmText: "Cancel dish",
      danger: true,
      needReason: !!item.in_kitchen,
    }).then(function (answer) {
      if (!answer) return null;
      return post(urlFor(URLS.cancel, item.id), { reason: answer.reason })
        .then(function (data) { toast(item.name + " cancelled", "success"); return data; })
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
    esc: esc,
    toast: toast,
    post: post,
  };
})();
