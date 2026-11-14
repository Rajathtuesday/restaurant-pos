/*
 * A button or a form that posts to the server and then reloads the page or
 * takes a row away: the commonest thing a page does, written once. The
 * markup says everything; core/app.js handles it on every base.html page,
 * so such a page needs no script of its own.
 *
 *   <button type="button" data-action="post"
 *           data-url="{% url 'expense-delete' e.id %}"      where (reversed by the server)
 *           data-body='{"status": "seated"}'                what to send (JSON, optional)
 *           data-confirm="Delete this expense?"             ask first (optional), in Rasova's
 *           data-confirm-text="This cannot be undone."      own sheet, never confirm()
 *           data-confirm-button="Delete"
 *           data-ask="Reason for rejection"                 ask for one line of text (optional),
 *           data-ask-field="reason" data-ask-optional       sent as this field; may be left empty
 *           data-done="Expense deleted."                    a toast once done (optional)
 *           data-remove="#expense-row-7"                    take this away once done, or
 *           data-go="{% url 'menu_management' %}"           go here once done, or
 *           data-then="reload">                             "reload" (the default) or "none"
 *
 *   <form data-action="post" data-url="..." data-done="..."> sends its named
 *   fields as JSON (a checkbox as true or false) when submitted, so the
 *   browser's own checks (required, min) run first.
 *
 * The shared client (core/http.js) shows a refusal as a toast; a 200 that
 * carries "error" is a refusal too. A second tap while the first is out is
 * ignored.
 */

/* A form's named fields, as the server's JSON views read them. */
export function formBody(form) {
  const body = {};
  for (const field of Array.from(form.elements || [])) {
    if (!field.name || field.disabled || field.name === "csrfmiddlewaretoken") continue;
    if (field.type === "checkbox") body[field.name] = Boolean(field.checked);
    else if (field.type === "radio") { if (field.checked) body[field.name] = field.value; }
    else if (field.type !== "submit" && field.type !== "button") body[field.name] = field.value;
  }
  return body;
}

function extraBody(el) {
  if (!el.dataset.body) return {};
  try {
    const parsed = JSON.parse(el.dataset.body);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (bad) {
    return {};
  }
}

export function createPostActions({ api, ui, doc, reload, go = (url) => { globalThis.location.href = url; }, wait = (fn, ms) => setTimeout(fn, ms) }) {
  const out = new Set();

  async function send(el, body) {
    if (out.has(el)) return false;
    const d = el.dataset;
    if (d.confirm) {
      const answer = await ui.confirm(d.confirm, d.confirmText || "", d.confirmButton || "Yes, do it");
      if (!answer || !answer.isConfirmed) return false;
    }
    if (d.ask) {
      const typed = await ui.ask(d.ask, { label: d.askLabel || "", confirmText: d.askButton || "Send", required: !("askOptional" in d) });
      if (typed === null) return false;
      body = { ...body, [d.askField || "reason"]: typed };
    }
    out.add(el);
    el.setAttribute("aria-busy", "true");
    let data;
    try {
      data = await api.post(d.url, { ...body, ...extraBody(el) });
    } catch (shown) {
      return false;                      // the client showed why
    } finally {
      out.delete(el);
      el.removeAttribute("aria-busy");
    }
    if (data && data.error) {
      ui.toast(data.error, "error");
      return false;
    }
    if (d.done) ui.toast(d.done, "success");
    if (d.remove) {
      const gone = doc.querySelector(d.remove);
      if (gone) gone.remove();
    } else if (d.go) {
      wait(() => go(d.go), d.done ? 700 : 0);
    } else if ((d.then || "reload") === "reload") {
      // Long enough to read the toast, when there is one.
      wait(() => reload(), d.done ? 700 : 0);
    }
    return true;
  }

  return {
    /* A click on a button with data-action="post" (a form's own submit
       button is the form's business, through submit()). */
    click(el) {
      if (el.tagName === "FORM") return Promise.resolve(false);
      return send(el, {});
    },
    submit(form, event) {
      if (event) event.preventDefault();
      return send(form, formBody(form));
    },
  };
}
