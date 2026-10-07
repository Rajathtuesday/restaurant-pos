"""The public marketing pages on rasova.net, in one list.

Each page is a static file served by WhiteNoise from public/<path>index.html.
The sitemap (core.urls.sitemap_xml) and the search-indexing rules
(core.middleware.SearchIndexingMiddleware) both read this list, so a new page
is added here once and can't end up in the sitemap but marked noindex, or the
other way round. core/tests/test_public_pages.py fails if a page in public/
is missing from this list.
"""
from collections import namedtuple

PublicPage = namedtuple("PublicPage", "path changefreq priority")

PUBLIC_PAGES = (
    PublicPage("/", "weekly", "1.0"),
    PublicPage("/compare/", "monthly", "0.8"),
    PublicPage("/fine-dining-pos/", "monthly", "0.8"),
    PublicPage("/cafe-pos/", "monthly", "0.8"),
    PublicPage("/qsr-pos/", "monthly", "0.8"),
    PublicPage("/bar-pos/", "monthly", "0.8"),
)

PUBLIC_PATHS = frozenset(page.path for page in PUBLIC_PAGES)
