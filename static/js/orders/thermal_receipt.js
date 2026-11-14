/*
 * The browser receipt (orders/thermal_receipt.html) prints itself once it
 * has loaded, after a short pause so its fonts are drawn, and closes its
 * tab after printing (cancelling the print dialog keeps it open).
 */
export function autoPrint(win, delayMs = 400) {
  win.addEventListener("load", () => {
    win.setTimeout(() => {
      win.addEventListener("afterprint", () => win.close());
      win.print();
    }, delayMs);
  });
}

if (typeof window !== "undefined") autoPrint(window);
