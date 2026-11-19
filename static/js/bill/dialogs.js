/*
 * The small forms the bill page asks with: a dish's discount, a cover's
 * code, how many people split the bill, the cash in the drawer when the
 * register is opened, why a bill is closed unpaid.
 *
 * All through SweetAlert2, in the page's theme. Three of them used to be
 * the browser's own prompt() and confirm(), which never appear inside the
 * Android app, so Split, opening the register from the bill, and closing
 * a bill without payment silently did nothing there.
 *
 * Each resolves to what was typed, or null if the cashier backed out.
 */
import { readItemDiscount } from "./discounts.js";
import { splitShare } from "./amount.js";
import { rupees } from "../core/money.js";

export function createDialogs({ Swal = globalThis.Swal, isDark = () => false, doc = document }) {
  const field = (id) => doc.getElementById(id);

  async function form({ title, html, confirmText, focus, read }) {
    const result = await Swal.fire({
      titleText: title,
      html,
      showCancelButton: true,
      confirmButtonText: confirmText,
      confirmButtonColor: "var(--accent-gold)",
      cancelButtonColor: "var(--text-muted)",
      background: isDark() ? "var(--panel-bg)" : "#ffffff",
      color: "var(--text-main)",
      customClass: { popup: "rounded-0", confirmButton: "rounded-0 btn btn-accent", cancelButton: "rounded-0 btn btn-luxury" },
      focusConfirm: false,
      didOpen: () => {
        const el = field(focus);
        if (el) {
          el.focus();
          if (el.select) el.select();
        }
      },
      preConfirm: () => {
        const answer = read();
        if (answer && answer.message) {
          Swal.showValidationMessage(answer.message);
          return false;
        }
        return answer;
      },
    });
    return result.isConfirmed ? result.value : null;
  }

  const label = (forId, text, first) => `<label for="${forId}" class="bill-form-label${first ? " bill-form-label--first" : ""}">${text}</label>`;

  return {
    /* {percent, reason} for a dish; `name` is shown as text, never as HTML. */
    itemDiscount(name, currentPercent) {
      return form({
        title: `Discount on ${name}`,
        html: label("item-discount-pct", "Discount %", true)
          + `<input id="item-discount-pct" type="number" min="0" max="100" step="0.01" inputmode="decimal" class="swal2-input bill-form-input" value="${Number(currentPercent) || 0}">`
          + '<div class="bill-form-hint">0 takes the discount off.</div>'
          + label("item-discount-reason", "Reason")
          + '<input id="item-discount-reason" type="text" maxlength="200" class="swal2-input bill-form-input" placeholder="For example: regular guest, food complaint">',
        confirmText: "Apply",
        focus: "item-discount-pct",
        read: () => readItemDiscount(field("item-discount-pct").value, field("item-discount-reason").value),
      });
    },

    /* {code} from the slip a guest bought at the door. */
    coverCode() {
      return form({
        title: "Pay from a cover",
        html: label("cover-code", "Code on the slip", true)
          + '<input id="cover-code" class="swal2-input bill-form-input bill-form-input--code" maxlength="8" autocomplete="off">',
        confirmText: "Use cover",
        focus: "cover-code",
        read: () => {
          const code = field("cover-code").value.trim();
          return code ? { code } : { message: "Type the code from the slip." };
        },
      });
    },

    /* One person's share of `balance`, as "123.45". */
    split(balance) {
      return form({
        title: "Split the bill",
        html: label("split-people", "How many people?", true)
          + '<input id="split-people" type="number" min="1" step="1" inputmode="numeric" class="swal2-input bill-form-input" value="2">',
        confirmText: "Split",
        focus: "split-people",
        read: () => splitShare(balance, field("split-people").value) || { message: "Enter a whole number of people, 1 or more." },
      });
    },

    /* The opening float as text ("0" if left empty), when a payment needs a register. */
    openRegister() {
      return form({
        title: "No cash register is open",
        html: '<div class="bill-form-hint bill-form-hint--lead">A payment needs an open register. Open it now?</div>'
          + label("register-float", "Cash already in the drawer")
          + '<input id="register-float" type="number" min="0" step="0.01" inputmode="decimal" class="swal2-input bill-form-input" value="0">',
        confirmText: "Open register",
        focus: "register-float",
        read: () => {
          const raw = field("register-float").value.trim() || "0";
          return Number.isFinite(Number(raw)) && Number(raw) >= 0 ? raw : { message: "Enter the cash in the drawer, 0 or more." };
        },
      });
    },

    /* The reason a bill is closed without payment; it goes on the audit report. */
    closeUnpaid(remaining) {
      return form({
        title: "Close this bill without payment?",
        html: `<div class="bill-form-hint bill-form-hint--lead">${rupees(remaining)} stays unpaid. It goes on the audit report with your name and reason.</div>`
          + label("close-reason", "Why? (for example: guest walked out)")
          + '<input id="close-reason" type="text" maxlength="200" class="swal2-input bill-form-input">',
        confirmText: "Close Bill",
        focus: "close-reason",
        read: () => {
          const reason = field("close-reason").value.trim();
          return reason.length >= 3 ? reason : { message: "Closing without payment needs a reason." };
        },
      });
    },
  };
}
