/*
 * How a live screen (the kitchen tickets, the floor, one order, the live
 * orders board, today's numbers) asks the server for its data: every few
 * seconds, and again after every tap. One reader for all of them.
 *
 * Answers can arrive out of order. An answer is shown when its question
 * was asked after whatever is on screen now. An older answer arriving late
 * is dropped (it would put a dish back to how it was before a tap), and so
 * is an older failure (it would wipe a board drawn since). A newer answer
 * is never dropped because an even newer question is on its way. Each
 * screen used to draw only the answer to its very latest question, and on
 * a slow phone link the 5-second poll and the read after each tap kept
 * overtaking one another: the kitchen board drew nothing for seconds after
 * every answer had said the whisky was ready, and the cook tapped Ready on
 * it twice more (28 Nov 2026).
 *
 *   refresh()  ask now (after a tap: the screen must show what it did)
 *   poll()     the timer's call: ask unless a question is still on its way,
 *              so a slow link is not buried under more of them; one out
 *              longer than `patience` does not count (a request can hang
 *              without ever failing)
 *   restart()  drop the answers to every question asked so far, then ask
 *              (a station switch: those answers are for the station left)
 *   forget()   the same drop without asking (nothing to look at now)
 *
 * `timeout`, when given, gives a read up after that long: a screen that
 * shows a hung read as a failure (the billing panel's Retry) uses it.
 *
 * Signed out (core/signed_out.js), the notice is shown once and the screen
 * stops asking, so nothing on it goes on looking current; once the notice
 * is up, a feed stops before its next question even if another script on
 * the page found out first. A failure goes
 * to onFailure in words for the person: the server's own words, "Check the
 * connection." when the request got no answer, "Server error (502)." for
 * an error page. It uses fetch itself, not the shared client: a poll must
 * not flash the loading bar or raise a toast while the connection is down.
 * Everything comes in as an argument, so node can test it with fakes.
 */
import { knownSignedOut, showSignedOut, signedOut } from "./signed_out.js";

const SIGNED_OUT = Symbol("signed out");

export function createLiveFeed({
  fetchFn,
  address,                                      // the address, or a function giving it
  read = (answer) => answer,                    // what the screen needs; throws Error(words) when unusable
  words = (answer) => answer.error || answer.message,   // a refusal's words
  onData,
  onFailure = () => {},
  onSignedOut = () => showSignedOut(),
  pageSignedOut = () => knownSignedOut(),        // another script on the page found out first
  now = () => Date.now(),
  patience = 15000,
  timeout = 0,                                   // ms after which a read is given up (0: never)
  init = {},
}) {
  let asked = 0;                    // questions asked so far
  let shown = 0;                    // the question whose answer, or failure, is on screen
  let floor = 0;                    // answers to questions up to this one are dropped
  let stopped = false;
  const onTheWay = new Map();       // question -> when it was asked

  async function ask() {
    const url = typeof address === "function" ? address() : address;
    // A screen that shows a hung read as a failure (the billing panel's
    // Retry) gives it up after `timeout`; the fetch then fails like a
    // dropped connection.
    const giveUp = timeout && typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = giveUp ? setTimeout(() => giveUp.abort(), timeout) : null;
    try {
      let response;
      try {
        response = await fetchFn(url, {
          ...init,
          ...(giveUp ? { signal: giveUp.signal } : {}),
          headers: { Accept: "application/json", ...(init.headers || {}) },
        });
      } catch (noAnswer) {
        throw new Error("Check the connection.");
      }
      if (signedOut(response)) throw SIGNED_OUT;
      let answer;
      try {
        answer = await response.json();
      } catch (notJson) {
        // A page where data should be. With a 200 it was not Rasova that
        // answered (a hotel Wi-Fi sign-in page, a proxy): the sign-in page
        // itself is caught above as signed out.
        throw new Error(response.ok ? "Check the connection." : `Server error (${response.status}).`);
      }
      if (!response.ok) {
        throw new Error((answer && typeof answer === "object" && words(answer)) || `Server error (${response.status}).`);
      }
      return read(answer);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function refresh() {
    if (!stopped && pageSignedOut()) stopped = true;
    if (stopped) return false;
    const mine = ++asked;
    onTheWay.set(mine, now());
    let data;
    let failure = null;
    try {
      data = await ask();
    } catch (err) {
      failure = err;
    } finally {
      onTheWay.delete(mine);
    }
    if (failure === SIGNED_OUT) {
      if (!stopped) {
        stopped = true;
        onSignedOut();
      }
      return false;
    }
    if (stopped || mine <= shown || mine <= floor) return false;
    shown = mine;
    if (failure) {
      onFailure((failure && failure.message) || String(failure));
      return false;
    }
    onData(data);
    return true;
  }

  function poll() {
    const t = now();
    for (const since of onTheWay.values()) {
      if (t - since < patience) return Promise.resolve(false);
    }
    return refresh();
  }

  /* Drop the answers to every question asked so far, without asking again
     (the screen has nothing to look at now: no table chosen). */
  function forget() {
    floor = asked;
  }

  function restart() {
    forget();
    return refresh();
  }

  return { refresh, poll, restart, forget, stopped: () => stopped };
}
