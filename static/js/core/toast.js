/*
 * The one toast, the one confirm box and the loading bar.
 *
 * Wraps SweetAlert2 (window.Swal, loaded from static/vendor) so every
 * screen's messages look the same and pick up dark mode. `createUi` takes
 * its dependencies as arguments so the tests can hand it fakes; app.js
 * builds the real one.
 */
export function createUi({
  swal = () => globalThis.Swal,
  isDark = () => document.documentElement.classList.contains("dark"),
  doc = document,
} = {}) {
  return {
    toast(message, icon = "info") {
      const Swal = swal();
      if (!Swal) {
        console.warn("toast:", message);
        return;
      }
      Swal.mixin({
        toast: true,
        position: "bottom-end",
        showConfirmButton: false,
        timer: 3000,
        timerProgressBar: true,
        background: isDark() ? "var(--panel-bg)" : "#fff",
        color: "var(--text-main)",
        didOpen: (toast) => {
          toast.onmouseenter = Swal.stopTimer;
          toast.onmouseleave = Swal.resumeTimer;
        },
      }).fire({ icon, title: message });
    },

    showLoading() {
      doc.body.classList.add("loading");
    },

    hideLoading() {
      doc.body.classList.remove("loading");
    },

    confirm(title, text, confirmText = "Yes, do it") {
      return swal().fire({
        // Text, never HTML: titles carry names people typed (a dish, a station).
        titleText: title,
        text,
        icon: "warning",
        showCancelButton: true,
        confirmButtonColor: "var(--accent-gold)",
        cancelButtonColor: "var(--text-muted)",
        confirmButtonText: confirmText,
        background: isDark() ? "var(--panel-bg)" : "#ffffff",
        color: "var(--text-main)",
        customClass: {
          popup: "rounded-0",
          confirmButton: "rounded-0 btn btn-accent",
          cancelButton: "rounded-0 btn btn-luxury",
        },
      });
    },

    /* One line of text, typed in Rasova's own sheet: the replacement for
       the browser's prompt(), which never shows in the Android app.
       Resolves to the trimmed text, or null when the person backs out or
       leaves it as it was. With required: false an empty answer is ""
       (a reason that is optional). The title is text, never read as HTML. */
    async ask(title, { value = "", label = "", confirmText = "Save", maxLength = 100, required = true } = {}) {
      const result = await swal().fire({
        titleText: title,
        input: "text",
        inputLabel: label,
        inputValue: value,
        inputAttributes: { maxlength: String(maxLength), autocapitalize: "sentences" },
        showCancelButton: true,
        confirmButtonText: confirmText,
        confirmButtonColor: "var(--accent-gold)",
        cancelButtonColor: "var(--text-muted)",
        background: isDark() ? "var(--panel-bg)" : "#ffffff",
        color: "var(--text-main)",
        customClass: {
          popup: "rounded-0",
          confirmButton: "rounded-0 btn btn-accent",
          cancelButton: "rounded-0 btn btn-luxury",
        },
        inputValidator: required ? (typed) => ((typed || "").trim() ? null : "Type something, or tap Cancel.") : undefined,
      });
      if (!result.isConfirmed) return null;
      const typed = String(result.value || "").trim();
      if (!required) return typed;
      return typed && typed !== String(value).trim() ? typed : null;
    },
  };
}
