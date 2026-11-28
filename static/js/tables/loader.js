/*
 * Asks the server for the floor and hands it to the page: every few
 * seconds (poll) and again after every action (refresh). Which answer is
 * drawn, and what a failure or a sign-out shows, is core/live_feed.js: the
 * one reader every live screen shares. A failure is shown in the grid
 * instead of the floor.
 */
import { createLiveFeed } from "../core/live_feed.js";

export function createFloorLoader({ fetchFn, url, onTables, onFailure, ...feedOptions }) {
  let tables = [];
  const feed = createLiveFeed({
    ...feedOptions,
    fetchFn,
    address: url,
    // tables_data answers a failure as {"error": "tables_data_failed", "message": words}.
    words: (d) => d.message || d.error,
    read: (d) => {
      if (!d || !Array.isArray(d.tables)) throw new Error((d && d.message) || "Invalid data from server");
      return d.tables;
    },
    onData: (found) => {
      tables = found;
      onTables(tables);
    },
    onFailure,
  });

  return {
    refresh: feed.refresh,
    poll: feed.poll,
    tables: () => tables,
    find: (id) => tables.find((t) => t.id === Number(id)),
  };
}
