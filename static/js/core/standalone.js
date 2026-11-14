/*
 * For a page without base.html (a printout, a guest page): handles the
 * shared page buttons (core/page_actions.js) and nothing else.
 */
import { delegate } from "./dom.js";
import { pageActions, pageChangeActions } from "./page_actions.js";

delegate(document.body, pageActions(window));
delegate(document.body, pageChangeActions(), "change");
