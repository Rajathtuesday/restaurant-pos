/*
 * A manager's PIN for a change that needs one (orders/services/approvals.py).
 *
 * When the server answers 403 {"needs_approval": true, "approvers": [...]},
 * this asks for a manager and their PIN, then sends the same request again
 * with the headers X-Approver-Id and X-Approver-Pin. A wrong PIN answers the
 * same way with "refused": true, so the prompt opens again with the message.
 *
 *   RasovaApproval.send(function (extraHeaders) { return fetch(url, {..., headers: {...extraHeaders}}); })
 *     -> Promise<Response>: the server's answer to the last try (the 403 if
 *        the person backed out, so callers show its error as usual).
 *
 * apiClient (templates/core/base.html) and OrderEdit (static/js/order_edit.js)
 * send through this, so every screen asks the same way. Needs SweetAlert2.
 */
(function () {
  "use strict";

  var ID_HEADER = "X-Approver-Id";
  var PIN_HEADER = "X-Approver-Pin";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function readJson(response) {
    return response.clone().json().catch(function () { return null; });
  }

  function isDark() {
    return document.documentElement.classList.contains("dark") || document.body.classList.contains("dark");
  }

  // Ask for a manager and a PIN. Resolves {id, pin}, or null if they back out.
  function ask(data, lastId) {
    var people = data.approvers || [];
    if (!people.length) {
      return window.Swal.fire({
        icon: "info",
        title: "A manager is needed",
        html: esc(data.error) + "<br><br>No manager has set an approval PIN yet. A manager sets one under " +
          "<b>Setup, Approval PIN</b>.",
        confirmButtonText: "OK",
        background: isDark() ? "#1a1d24" : "#fff", color: isDark() ? "#f9fafb" : "#111",
      }).then(function () { return null; });
    }
    var options = people.map(function (p) {
      var selected = String(p.id) === String(lastId) ? " selected" : "";
      return '<option value="' + esc(p.id) + '"' + selected + ">" + esc(p.name) +
        (p.role === "owner" ? " (owner)" : "") + "</option>";
    }).join("");
    var note = data.refused ? '<div class="ra-error" style="color:#dc2626;font-size:.85rem;margin:.2rem 0 .6rem;">' +
      esc(data.error) + "</div>" : '<div style="font-size:.85rem;margin:.2rem 0 .6rem;">' + esc(data.error) + "</div>";
    return window.Swal.fire({
      title: "Manager's PIN",
      html: note +
        '<label for="ra-who" style="display:block;text-align:left;font-size:.8rem;">Manager</label>' +
        '<select id="ra-who" class="swal2-select" style="width:100%;margin:.3rem 0 .8rem;">' + options + "</select>" +
        '<label for="ra-pin" style="display:block;text-align:left;font-size:.8rem;">PIN</label>' +
        '<input id="ra-pin" type="password" inputmode="numeric" autocomplete="off" maxlength="6" ' +
        'class="swal2-input" style="width:100%;margin:.3rem 0 0;letter-spacing:.4em;text-align:center;">',
      showCancelButton: true,
      confirmButtonText: "Approve",
      cancelButtonText: "Cancel",
      focusConfirm: false,
      background: isDark() ? "#1a1d24" : "#fff", color: isDark() ? "#f9fafb" : "#111",
      didOpen: function () { document.getElementById("ra-pin").focus(); },
      preConfirm: function () {
        var pin = document.getElementById("ra-pin").value.trim();
        if (!/^\d{4,6}$/.test(pin)) {
          window.Swal.showValidationMessage("Type the 4 to 6 digit PIN.");
          return false;
        }
        return { id: document.getElementById("ra-who").value, pin: pin };
      },
    }).then(function (result) { return result.isConfirmed ? result.value : null; });
  }

  function send(doRequest) {
    var lastId = null;
    function attempt(extra) {
      return Promise.resolve(doRequest(extra || {})).then(function (response) {
        if (response.status !== 403) return response;
        return readJson(response).then(function (data) {
          if (!data || !data.needs_approval || !window.Swal) return response;
          // The page's loading cover (apiClient's) would sit over the prompt.
          var loading = document.body.classList.contains("loading");
          document.body.classList.remove("loading");
          return ask(data, lastId).then(function (answer) {
            if (loading) document.body.classList.add("loading");
            if (!answer) return response;
            lastId = answer.id;
            var headers = {};
            headers[ID_HEADER] = answer.id;
            headers[PIN_HEADER] = answer.pin;
            return attempt(headers);
          });
        });
      });
    }
    return attempt({});
  }

  window.RasovaApproval = { send: send, ID_HEADER: ID_HEADER, PIN_HEADER: PIN_HEADER };
})();
