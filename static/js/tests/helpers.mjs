/* Fakes shared by the module tests. */

export function fakeUi() {
  const calls = { toast: [], loading: 0 };
  return {
    calls,
    toast: (message, icon) => calls.toast.push([message, icon]),
    confirm: async () => ({ isConfirmed: true }),
    showLoading: () => { calls.loading += 1; },
    hideLoading: () => { calls.loading -= 1; },
  };
}

/* A Response-like object. `body` undefined means "not JSON". */
export function reply(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new SyntaxError("not json");
      return body;
    },
  };
}

/* A fetch that answers from a script: each call takes the next entry.
   An entry that is an Error is thrown (the network failing). */
export function scriptedFetch(script) {
  const calls = [];
  const fetchFn = async (url, options = {}) => {
    calls.push({ url, options });
    const next = script.shift();
    if (next === undefined) throw new Error("unexpected fetch " + url);
    if (next instanceof Error) throw next;
    return next;
  };
  fetchFn.calls = calls;
  return fetchFn;
}

export function networkDown() {
  return new TypeError("Failed to fetch");
}
