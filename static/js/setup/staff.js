/*
 * Setup, Staff (setup/templates/setup/setup_staff.html): Edit Role, Edit
 * Location, Edit Pay Rate and Reset Password open their sheets
 * (staff_sheets.js) and save what was chosen. Deactivate and Reactivate
 * are post buttons (core/post_actions.js); "Show inactive" is CSS (:has).
 * Each button names the person (data-name) and its address (data-url).
 */
import { api, theme, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { askPassword, askPayRate, pickOne } from "./staff_sheets.js";

const ROLES = pageData("role-choices-data");
const OUTLETS = pageData("outlet-choices-data");
const swal = () => window.Swal;

async function save(url, body, fallback, reload) {
  try {
    const data = await api.post(url, body);
    ui.toast(data.message || fallback, "success");
    if (reload) setTimeout(() => window.location.reload(), 700);
  } catch (shown) {
    /* the client showed why */
  }
}

delegate(document.body, {
  "staff.role": async (b) => {
    const role = await pickOne(swal(), { title: `Change role for ${b.dataset.name}`, options: ROLES, current: b.dataset.current, confirmText: "Save Role", dark: theme.isDark() });
    if (role) save(b.dataset.url, { role }, "Role updated.", true);
  },
  "staff.outlet": async (b) => {
    const outlet = await pickOne(swal(), { title: `Move ${b.dataset.name} to a different location`, options: OUTLETS, current: b.dataset.current, confirmText: "Save Location", dark: theme.isDark() });
    if (outlet) save(b.dataset.url, { outlet_id: outlet }, "Location updated.", true);
  },
  "staff.pay": async (b) => {
    const rate = await askPayRate(swal(), { title: `Set pay rate for ${b.dataset.name}`, dark: theme.isDark() });
    if (rate) save(b.dataset.url, rate, "Pay rate updated.", false);
  },
  "staff.password": async (b) => {
    const password = await askPassword(swal(), { title: `Reset password for ${b.dataset.name}`, dark: theme.isDark() });
    if (password) save(b.dataset.url, { password }, "Password reset.", false);
  },
});
