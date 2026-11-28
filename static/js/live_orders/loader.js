/*
 * Asks for the open orders every few seconds (poll) and after every change
 * (refresh). Which answer is drawn, and what a failure or a sign-out
 * shows, is core/live_feed.js: the one reader every live screen shares. A
 * failure goes to onFailure; the board keeps the last orders it drew,
 * never shows "no open orders" for a failure.
 */
import { createLiveFeed } from "../core/live_feed.js";

export function createOrdersLoader({ fetchFn, url, onOrders, onFailure, ...feedOptions }) {
  const feed = createLiveFeed({
    ...feedOptions,
    fetchFn,
    address: url,
    init: { credentials: "same-origin" },
    read: (d) => {
      if (!d || !Array.isArray(d.orders)) throw new Error((d && d.error) || "Invalid data from server.");
      return d;
    },
    onData: onOrders,
    onFailure,
  });
  return { refresh: feed.refresh, poll: feed.poll };
}
