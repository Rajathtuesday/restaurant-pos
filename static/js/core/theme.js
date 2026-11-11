/*
 * Dark and light mode.
 *
 * The class on <html> is set before the first paint by the three-line
 * script in base.html's <head> (it has to run before the theme CSS loads,
 * or dark-mode users see a flash of the light page). This only keeps the
 * toggle icon in step and flips the class when the button is pressed.
 */
const KEY = "dark";

export function createThemeManager({
  doc = document,
  storage = globalThis.localStorage,
  win = globalThis,
} = {}) {
  const icon = () => doc.getElementById("themeIcon");

  const manager = {
    isDark() {
      return doc.documentElement.classList.contains("dark");
    },

    init() {
      const el = icon();
      if (el) el.className = manager.isDark() ? "bi bi-sun" : "bi bi-moon";
    },

    toggle() {
      const dark = doc.documentElement.classList.toggle("dark");
      try {
        storage.setItem(KEY, dark);
      } catch (noStorage) {
        /* a private window: the choice lasts for this page only */
      }
      const el = icon();
      if (el) el.className = dark ? "bi bi-sun" : "bi bi-moon";
      win.dispatchEvent(new CustomEvent("themeChanged", { detail: { theme: dark ? "dark" : "light" } }));
    },
  };
  return manager;
}
