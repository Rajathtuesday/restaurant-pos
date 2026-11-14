/*
 * The login page: its theme switch, the same switch as every other page
 * (core/theme.js). The theme was set before the first paint by
 * core/theme_boot.js.
 */
import { delegate } from "../core/dom.js";
import { createThemeManager } from "../core/theme.js";

const theme = createThemeManager();
theme.init();
delegate(document.body, { "theme.toggle": () => theme.toggle() });
