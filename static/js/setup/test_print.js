/*
 * Setup's Test Print, waiting for the real result.
 *
 * It used to say "Test queued" the moment the server accepted the job,
 * which proved nothing about the printer. Now it asks for the job until a
 * print agent finishes it: printed, refused (with the agent's reason), or
 * nobody took it within the time a working agent needs. Each answer says
 * what to do next.
 *
 * Everything comes in as an argument so node can test it with fakes:
 *   api      core/http.js's client (posts, shows a refusal as a toast)
 *   fetchFn  for the status, which must not flash the loading bar each time
 *   show     (level, words) => the result line; level "wait", "ok" or "bad"
 *   box      true on a Rasova box: the box prints its own queue, so a page
 *            nobody took means the box's printing has stopped, not that a
 *            phone app is closed
 */
import { withId } from "../billing/urls.js";

export const WAIT_FOR_SECONDS = 20;

export function createTestPrint({
  api, fetchFn, urls, show, box = false,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now = () => Date.now(),
  every = 1500,
}) {
  let running = false;

  async function status(jobId) {
    try {
      const response = await fetchFn(withId(urls.job_status, jobId), { headers: { Accept: "application/json" } });
      return response.ok ? await response.json() : null;
    } catch (connectionDropped) {
      return null;                       // asked again on the next turn
    }
  }

  return {
    get running() {
      return running;
    },

    /* Returns the last level shown ("ok", "bad"), or null when ignored. */
    async run(ip) {
      if (running) return null;
      if (!(ip || "").trim()) {
        show("bad", "Enter a printer IP and Save first.");
        return "bad";
      }
      running = true;
      try {
        show("wait", "Sending a test page...");
        let job;
        try {
          job = await api.post(urls.test_print, {});
        } catch (refused) {
          show("bad", refused.message || "Could not send the test page.");
          return "bad";
        }
        show("wait", "Sent. Waiting for the printer...");
        const until = now() + WAIT_FOR_SECONDS * 1000;
        let last = "pending";
        while (now() < until) {
          await sleep(every);
          const answer = await status(job.job_id);
          if (!answer) continue;
          last = answer.status;
          if (answer.status === "done") {
            show("ok", "Printed. The printer took the test page.");
            return "ok";
          }
          if (answer.status === "failed") {
            show("bad", "The printer refused it: " + (answer.error || "no reason given") + ". Check the IP, the paper and the Wi-Fi.");
            return "bad";
          }
        }
        if (box) {
          show("bad", last === "processing"
            ? "The box sent the page but the printer has not answered. Check it is on, has paper and is on the same network as the box."
            : `The box did not print it in ${WAIT_FOR_SECONDS} seconds. Restart the box; if it happens again, call Rasova support.`);
        } else {
          show("bad", last === "processing"
            ? "The print app took the page but the printer has not answered. Check it is on and has paper."
            : `Nothing picked it up in ${WAIT_FOR_SECONDS} seconds. Open the Rasova app on the counter phone and keep it logged in.`);
        }
        return "bad";
      } finally {
        running = false;
      }
    },
  };
}
