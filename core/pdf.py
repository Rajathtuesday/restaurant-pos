"""
Rendering Rasova's PDFs (the bill, the subscription invoice, the purchase
order) with WeasyPrint, without the server fetching from itself.

WeasyPrint loads every stylesheet and image a page links. Left to itself
it fetches them over HTTP, from the address the page was rendered for:
for the bill PDF that is the server's own public address, so a PDF on the
production box went out through Cloudflare and back to fetch Bootstrap,
the theme and the bill's stylesheet. On a small server that is slow, and
when that round trip fails the PDF comes out unstyled. It would also fetch
any address a page happened to name.

So a PDF reads only:
  - Rasova's static files, from disk (STATIC_ROOT once collected, the app
    folders before), whatever host the link names
  - uploaded media (a restaurant's logo): from disk when it is stored
    locally, from its own address when it is on R2
  - data: URIs, which carry their content with them
Anything else is refused, and WeasyPrint leaves it out of the PDF.
"""
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings


def resolve(url):
    """What a PDF may read for `url`: ("file", path), ("network", url),
    ("data", url), or ("refuse", why). Pure, so it is tested without
    WeasyPrint (which needs system libraries Windows does not have)."""
    if url.startswith("data:"):
        return "data", url
    parsed = urlparse(url)
    path = unquote(parsed.path)

    static_url = urlparse(settings.STATIC_URL).path
    if path.startswith(static_url):
        found = _static_file(path[len(static_url):])
        return ("file", found) if found else ("refuse", f"no such static file: {path}")

    media = urlparse(settings.MEDIA_URL)
    if media.netloc:
        # Media on R2: a logo is only on its own host.
        if parsed.netloc == media.netloc and path.startswith(media.path or "/"):
            return "network", url
    elif path.startswith(media.path):
        found = _inside(Path(settings.MEDIA_ROOT), path[len(media.path):])
        return ("file", found) if found else ("refuse", f"no such media file: {path}")

    return "refuse", f"a PDF reads only Rasova's own files: {url}"


def _static_file(name):
    """A static file's path on disk: the collected copy (production, where
    the page links the hashed name) or the one in the app folders."""
    root = getattr(settings, "STATIC_ROOT", None)
    if root:
        collected = _inside(Path(root), name)
        if collected:
            return collected
    from django.contrib.staticfiles import finders
    from django.core.exceptions import SuspiciousFileOperation
    try:
        found = finders.find(name)
    except (SuspiciousFileOperation, ValueError):
        return None                     # "../" and the like: never a static file
    return str(found) if found else None


def _inside(root, name):
    """root/name if it is a file under root (never a path climbing out)."""
    try:
        root = root.resolve()
        target = (root / name).resolve()
    except (OSError, ValueError):
        return None
    if root not in target.parents or not target.is_file():
        return None
    return str(target)


def _fetcher():
    import weasyprint.urls as wurls

    class OwnFilesFetcher(wurls.URLFetcher):
        def fetch(self, url, headers=None):
            kind, value = resolve(url)
            if kind == "file":
                mime = mimetypes.guess_type(value)[0] or "application/octet-stream"
                return wurls.URLFetcherResponse(url, Path(value).read_bytes(), {"Content-Type": mime})
            if kind in ("network", "data"):
                return super().fetch(url, headers)
            raise ValueError(value)

    # A logo slow to answer must not hold a request for the default 10 s.
    return OwnFilesFetcher(timeout=5, allowed_protocols=("data", "https", "http"))


def render_pdf(html_string, base_url):
    """The PDF for `html_string`. base_url is where relative links in the
    page point (the site's address); static ones are read from disk.

    presentational_hints=False, WeasyPrint's default, stated: the pages
    render guest-supplied text, and the CSS-injection bug
    GHSA-jhhc-3hcp-qhm5 (CVE-2026-49452) fires only with it on."""
    import weasyprint
    return weasyprint.HTML(string=html_string, base_url=base_url, url_fetcher=_fetcher()).write_pdf(
        presentational_hints=False)
