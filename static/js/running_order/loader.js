/*
 * Asks the server for the order every few seconds (poll) and after every
 * action (refresh). Which answer is drawn, and what a failure or a
 * sign-out shows, is core/live_feed.js: the one reader every live screen
 * shares. A failure goes to onFailure with words for the person; the page
 * keeps the last list it drew, never shows "no dishes" for a failure.
 */
import { createLiveFeed } from "../core/live_feed.js";

export function createOrderLoader({ fetchFn, url, onOrder, onFailure, ...feedOptions }) {
  const feed = createLiveFeed({
    ...feedOptions,
    fetchFn,
    address: url,
    read: (d) => {
      if (!d || !Array.isArray(d.items)) throw new Error((d && d.error) || "Invalid data from server.");
      return d;
    },
    onData: onOrder,
    onFailure,
  });
  return { refresh: feed.refresh, poll: feed.poll };
}
