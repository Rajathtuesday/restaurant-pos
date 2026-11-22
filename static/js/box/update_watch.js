/*
 * Following a pen drive update from Box health (box/templates/box/health.html).
 *
 * While the box looks at the pen drive or installs, this asks how it is going
 * every few seconds. During an install Rasova itself restarts, so for a
 * minute nothing answers: that is expected, and it keeps asking. When the
 * state changes (found, done, failed) the page reloads to show it.
 *
 * Everything comes in as an argument so node can test it with fakes.
 */
export const EVERY_MS = 3000;
export const GIVE_UP_MS = 30 * 60 * 1000;      // an update the box never finishes: stop asking

export function createUpdateWatch({ fetchFn, url, startState, reload, show,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), now = () => Date.now() }) {
  return {
    async run() {
      const until = now() + GIVE_UP_MS;
      let quiet = 0;
      while (now() < until) {
        await sleep(EVERY_MS);
        let answer = null;
        try {
          const response = await fetchFn(url, { headers: { Accept: "application/json" }, cache: "no-store" });
          answer = response.ok ? await response.json() : null;
        } catch (restarting) {
          answer = null;                          // Rasova is restarting during the install
        }
        if (!answer) {
          quiet += 1;
          if (quiet === 2) show("Rasova is restarting with the new version. This page comes back by itself.");
          continue;
        }
        quiet = 0;
        if (answer.state !== startState || !answer.working) {
          reload();
          return answer.state;
        }
        if (answer.message) show(answer.message);
      }
      show("The box hasn't finished. Reload this page in a few minutes, or call Rasova.");
      return null;
    },
  };
}
