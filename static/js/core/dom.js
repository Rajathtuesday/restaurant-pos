/*
 * The few DOM helpers every screen uses.
 */
export const qs = (selector, root = document) => root.querySelector(selector);
export const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/*
 * One listener on `root` serves every element that carries data-action.
 *
 *   delegate(screen, { "cart.inc": (el) => cart.inc(el.dataset.id) });
 *   <button data-action="cart.inc" data-id="42">+</button>
 *
 * Rows rendered after the listener was added work without re-binding, and
 * a template never needs an onclick attribute.
 */
export function delegate(root, actions, eventName = "click") {
  root.addEventListener(eventName, (event) => {
    const target = event.target.closest("[data-action]");
    if (!target || !root.contains(target)) return;
    const handler = actions[target.dataset.action];
    if (!handler) return;
    handler(target, event);
  });
}

/* Runs `fn` now if the document is parsed, otherwise at DOMContentLoaded. */
export function onReady(fn, doc = document) {
  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
}
