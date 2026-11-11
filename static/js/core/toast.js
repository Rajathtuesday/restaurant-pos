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
        title,
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
  };
}
