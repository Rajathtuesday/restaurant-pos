/*
 * Outlet Settings (setup/templates/setup/outlet_settings.html): the logo
 * preview, upper-casing GSTIN and the bill code as they are typed, a
 * warning on a GSTIN or FSSAI number of the wrong length when the field
 * is left (a warning only: the server decides), and "Saving..." while the
 * form is sent. The boxes that light up when ticked are CSS (:has).
 */
import { delegate } from "../core/dom.js";

/* Why a GSTIN or FSSAI number looks wrong, or "" when it looks right. */
export function fieldProblem(kind, value) {
  const v = String(value || "").trim();
  if (!v) return "";
  if (kind === "gstin" && v.length !== 15) return "GSTIN must be exactly 15 characters";
  if (kind === "fssai" && !/^\d{14}$/.test(v)) return "FSSAI must be exactly 14 digits";
  return "";
}

export function showLogo(doc, dataUrl) {
  let prev = doc.getElementById("logoPreview");
  if (prev.tagName !== "IMG") {
    const img = doc.createElement("img");
    img.className = "logo-img";
    img.id = "logoPreview";
    img.alt = "Logo preview";
    prev.replaceWith(img);
    prev = img;
  }
  prev.src = dataUrl;
}

if (typeof document !== "undefined") {
  delegate(document.body, {
    "field.upper": (input) => { input.value = input.value.toUpperCase(); },
  }, "input");
  delegate(document.body, {
    "logo.preview": (input) => {
      if (!input.files || !input.files[0]) return;
      const reader = new FileReader();
      reader.onload = (e) => showLogo(document, e.target.result);
      reader.readAsDataURL(input.files[0]);
    },
  }, "change");
  document.body.addEventListener("focusout", (event) => {
    const input = event.target.closest("[data-check]");
    if (!input) return;
    const problem = fieldProblem(input.dataset.check, input.value);
    input.classList.toggle("field-invalid", Boolean(problem));
    input.title = problem;
    input.setAttribute("aria-invalid", problem ? "true" : "false");
  });
  document.getElementById("settingsForm").addEventListener("submit", () => {
    const button = document.getElementById("saveBtn");
    button.disabled = true;
    button.innerHTML = '<i class="bi bi-hourglass-split me-2" aria-hidden="true"></i>Saving...';
  });
}
