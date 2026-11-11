"""
Static files in production: hashed names, gzip and brotli copies, and the
JavaScript modules' own imports rewritten to the hashed names.

Rasova's page scripts are native ES modules that import each other by
relative path (static/js/core/app.js imports "./http.js"). The manifest
storage renames every file to name.<hash>.ext so browsers can cache it for
a year; without the flag below, the import inside the module would still
say "./http.js", which no longer exists under that name once the manifest
is in charge. Django rewrites those imports when the flag is on.

The rewriting is regex based. It handles static relative imports
(import x from "./y.js", export ... from "./y.js"), which is all Rasova's
modules use. core/tests/test_static_modules.py collects the files with
this storage and checks that every import in every collected module
resolves, so a form the regex misses fails in CI, not in a browser.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class RasovaStaticStorage(CompressedManifestStaticFilesStorage):
    support_js_module_import_aggregation = True
