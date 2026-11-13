/*
 * The kitchen's own light switch.
 *
 * The kitchen display is dark whatever the rest of Rasova is set to (it
 * is read under bright, hot lights), and a cook who prefers it light can
 * flip this one screen without changing the others. The choice is kept
 * under its own key. static/js/kitchen/boot.js applies it before the
 * first paint; this keeps the icon in step and handles the button.
 */
export const KEY = "kitchen_light";

export function createKitchenLight({ doc = document, storage = globalThis.localStorage } = {}) {
  let light = false;
  try {
    light = storage.getItem(KEY) === "true";
  } catch (noStorage) {
    /* a private window: dark, and the choice lasts for this page only */
  }

  function show() {
    const root = doc.documentElement;
    root.classList.add("dark");
    root.classList.toggle("kitchen-light", light);
    // The browser's own colours (scrollbars, form controls) follow the switch.
    const scheme = doc.querySelector('meta[name="color-scheme"]');
    if (scheme) scheme.setAttribute("content", light ? "light" : "dark");
    const icon = doc.getElementById("themeIcon");
    if (icon) icon.className = light ? "bi bi-moon" : "bi bi-sun";
  }

  return {
    show,
    isLight: () => light,
    toggle() {
      light = !light;
      try {
        storage.setItem(KEY, light);
      } catch (noStorage) {
        /* as above */
      }
      show();
      return light;
    },
  };
}
