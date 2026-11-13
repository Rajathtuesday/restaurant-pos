/*
 * What a list drawn by a script says when it has nothing yet: the same
 * markup as templates/core/_empty_state.html, so both look alike. Every
 * value is escaped; a page passes its own words and address.
 */
import { esc } from "../order_edit.js";

export function emptyStateHtml({ icon = "inbox", title, text = "", href = "", label = "" }) {
  const button = href && label ? `<a class="btn-luxury text-decoration-none" href="${esc(href)}">${esc(label)}</a>` : "";
  return `<div class="rv-empty" data-empty-state>
    <i class="bi bi-${esc(icon)}" aria-hidden="true"></i>
    <h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}${button}
  </div>`;
}
