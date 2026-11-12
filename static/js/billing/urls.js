/*
 * The server gives a page its addresses already reversed, with 0 standing
 * in for the id ("/pay/0/"). This puts the real id in.
 */
export function withId(template, id) {
  return template.replace("/0/", "/" + encodeURIComponent(id) + "/");
}
