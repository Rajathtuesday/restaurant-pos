/*
 * GST rates (menu/gst_management.html): a dish's rate saves when its list
 * changes; Apply sets every dish in a category; a category folds away by
 * its title. A dish moved off a retired rate (12%, 28%) loses its "old
 * rate" tag, and the banner goes once none is left. Each control names
 * its address (data-url).
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { clearRetired, setCollapsed } from "./gst_rows.js";

delegate(document.body, {
  "gst.collapse": (button) => {
    const id = button.dataset.category;
    setCollapsed(document, id, !document.getElementById(`card-${id}`).classList.contains("collapsed"));
  },
  "gst.bulk": async (button) => {
    const id = button.dataset.category;
    const value = document.getElementById(`bulk-${id}`).value;
    button.disabled = true;
    try {
      const data = await api.post(button.dataset.url, { gst_percentage: value });
      const selects = Array.from(document.getElementById(`cat-${id}`).querySelectorAll('select[id^="gst-"]'));
      selects.forEach((s) => { s.value = value; });
      clearRetired(document, selects.map((s) => s.id.slice(4)));
      ui.toast(data.message || "Rates updated.", "success");
    } catch (shown) {
      /* the client showed why */
    } finally {
      button.disabled = false;
    }
  },
});
delegate(document.body, {
  "gst.item": async (select) => {
    const dot = document.getElementById(`dot-${select.dataset.item}`);
    dot.hidden = false;
    try {
      const data = await api.post(select.dataset.url, { gst_percentage: select.value });
      ui.toast(data.message || "Rate saved.", "success");
      clearRetired(document, [select.dataset.item]);
    } catch (shown) {
      /* the client showed why */
    } finally {
      dot.hidden = true;
    }
  },
}, "change");
