/*
 * The control panel's feature page: each switch saves when it changes
 * (feature_toggle.js).
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { toggleFeature } from "./feature_toggle.js";

delegate(document.body, {
  "feature.toggle": (box) => toggleFeature(box, { api, ui, doc: document }),
}, "change");
