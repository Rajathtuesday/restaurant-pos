/*
 * Asks the server for the kitchen's tickets and hands them to the page:
 * every few seconds (poll), after every tap (refresh), and when the cook
 * picks a station (setStation, which drops every answer for the station
 * just left). Which answer is drawn, and what a failure or a sign-out
 * shows, is core/live_feed.js: the one reader every live screen shares.
 * A failure is shown in the grid instead, never as "All Caught Up".
 */
import { createLiveFeed } from "../core/live_feed.js";

export function createKitchenLoader({ fetchFn, url, onKots, onFailure, ...feedOptions }) {
  let station = "";
  const feed = createLiveFeed({
    ...feedOptions,
    fetchFn,
    address: () => url + (station ? "?station=" + encodeURIComponent(station) : ""),
    read: (d) => {
      if (!d || !Array.isArray(d.kots)) throw new Error((d && d.error) || "Invalid data from server.");
      return d.kots;
    },
    onData: onKots,
    onFailure,
  });

  return {
    refresh: feed.refresh,
    poll: feed.poll,
    station: () => station,
    /* Show one station's tickets ("" for all of them). */
    setStation(name) {
      station = name || "";
      return feed.restart();
    },
  };
}
