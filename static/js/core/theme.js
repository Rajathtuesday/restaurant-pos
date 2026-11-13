/*
 * Dark and light mode.
 *
 * The class on <html> is set before the first paint by the boot script in
 * base.html's <head> (it has to run before the theme CSS loads, or
 * dark-mode users see a flash of the light page). The theme's CSS paints
 * <html> and <body> from that class. This keeps the toggle icon and the
 * browser's own colours (the color-scheme and theme-color meta tags: its
 * scrollbars, form controls and address bar) in step, and flips the class
 * when the button is pressed.
 */
const KEY = "dark";
const BROWSER_BAR = { dark: "#000000", light: "#fafafa" };     // --bg-color in each mode

export function createThemeManager({
  doc = document,
  storage = globalThis.localStorage,
  win = globalThis,
} = {}) {
  const icon = () => doc.getElementById("themeIcon");

  function paintBrowser(dark) {
    const scheme = doc.querySelector('meta[name="color-scheme"]');
    const bar = doc.querySelector('meta[name="theme-color"]');
    if (scheme) scheme.setAttribute("content", dark ? "dark" : "light");
    if (bar) bar.setAttribute("content", dark ? BROWSER_BAR.dark : BROWSER_BAR.light);
  }

  const manager = {
    isDark() {
      return doc.documentElement.classList.contains("dark");
    },

    init() {
      const el = icon();
      if (el) el.className = manager.isDark() ? "bi bi-sun" : "bi bi-moon";
      paintBrowser(manager.isDark());
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
      paintBrowser(dark);
      win.dispatchEvent(new CustomEvent("themeChanged", { detail: { theme: dark ? "dark" : "light" } }));
    },
  };
  return manager;
}
