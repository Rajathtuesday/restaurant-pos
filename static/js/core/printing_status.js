/*
 * The printer chip: one line on the billing, tables and token screens that
 * says whether printing works, and the one thing to do when it does not.
 *
 * The server sends facts with the 8-second notifications answer
 * (printing.services.status.printing_state): when a print agent last asked
 * for jobs, how many jobs wait and for how long, failures in the last hour
 * and the last error. This turns them into words. Nothing here touches the
 * page except createPrinterChip, which takes its element as an argument.
 */

export const AGENT_GONE_SECONDS = 120;   // the agent asks every 2 s; two minutes of silence is not a blip
export const STUCK_SECONDS = 120;        // a job normally prints within seconds

function minutes(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  return m === 1 ? "1 min" : m + " min";
}

/*
 * {level, text, hint} for the chip, or null when there is nothing to say.
 *   level  "ok" (green), "warn" (amber), "bad" (red), "info" (grey)
 *   text   a few words on the chip
 *   hint   what to do, shown when the chip is tapped
 */
export function describePrinting(state, { platform = {} } = {}) {
  if (!state || typeof state !== "object") return null;
  const seen = state.agent_seen_seconds;
  const fresh = seen !== null && seen !== undefined && seen <= AGENT_GONE_SECONDS;
  const openTheApp = platform.android
    ? "Open the Rasova app on this phone and keep it logged in."
    : "Open the Rasova app on the counter phone, or start the print agent on the computer.";

  if (state.oldest_seconds !== null && state.oldest_seconds !== undefined && state.oldest_seconds > STUCK_SECONDS) {
    return {
      level: "bad",
      text: `${state.waiting} waiting to print (${minutes(state.oldest_seconds)})`,
      hint: fresh
        ? "The print app is connected but the printer is not taking jobs. Check it is on, has paper, and is on the same Wi-Fi."
        : openTheApp,
    };
  }
  if (seen === null || seen === undefined) {
    if (state.failed_recent > 0) {
      return { level: "warn", text: "Last print failed", hint: state.last_error || "Check the printer, then print again." };
    }
    return platform.ios
      ? { level: "info", text: "Prints from the bill", hint: "On iPhone and iPad the bill opens to print with AirPrint." }
      : { level: "info", text: "No print app connected", hint: "Set up printing in Setup, Outlet Settings, Printers." };
  }
  if (!fresh) {
    return { level: "bad", text: `Print app not seen for ${minutes(seen)}`, hint: openTheApp };
  }
  if (state.failed_recent > 0) {
    return { level: "warn", text: "Last print failed", hint: state.last_error || "Check the printer, then print again." };
  }
  if (state.waiting > 0) {
    return { level: "ok", text: `Printing ${state.waiting}`, hint: "The print app is taking the jobs." };
  }
  return { level: "ok", text: "Printer ready", hint: "The print app is connected." };
}

const LEVELS = ["ok", "warn", "bad", "info"];

/* Draws the chip into `el` (templates/core/_printer_chip.html). */
export function createPrinterChip({ el, platform = {} }) {
  let current = null;
  return {
    update(state) {
      if (!el) return null;
      current = describePrinting(state, { platform });
      if (!current) {
        el.hidden = true;
        return null;
      }
      LEVELS.forEach((level) => el.classList.toggle("is-" + level, level === current.level));
      const text = el.querySelector(".printer-chip-text");
      if (text) text.textContent = current.text;
      el.title = current.hint;
      el.setAttribute("aria-label", "Printing: " + current.text);
      el.hidden = false;
      return current;
    },
    current: () => current,
  };
}
