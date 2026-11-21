"""
Static files in production: hashed names, gzip and brotli copies, and the
JavaScript modules' own imports rewritten to the hashed names.

Rasova's page scripts are native ES modules that import each other by
relative path (static/js/core/app.js imports "./http.js"). The manifest
storage renames every file to name.<hash>.ext so browsers can cache it for
a year; without the flag below, the import inside the module would still
say "./http.js", which no longer exists under that name once the manifest
is in charge. Django rewrites those imports when the flag is on.

The rewriting is regex based, and Django's own patterns (HashedFilesMixin.
_js_module_import_aggregation_patterns) are too loose for prose: they are
compiled case-insensitively, match anywhere in the text, and let a
statement run across lines and past semicolons. So the words "the Export
menu," in a comment read as the start of `export ... from "..."`, running
on to the next import, which the import pattern had already rewritten; the
address was rewritten twice (21 Nov 2026, reports/dashboard.js). Which way
that failed depended on the order the files were processed in: on CI
(Linux) collectstatic stopped with MissingFileError on "app.<hash>.js"; on
Windows it finished and wrote "app.<hash>.<hash>.js" into the built module,
a file that does not exist, so the page's scripts would not have loaded.

Rasova's patterns below take statements only: in lower case, at the start
of a line (where every import and export of ours is), and never past a
semicolon. The templates are Django's. core/tests/test_static_modules.py
collects every module with this storage and checks every import resolves
to a file that exists, and checks these patterns against prose.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage

# (?m)^ : a statement starts a line. (?-i:...) : the keyword in lower case
# (Django compiles every pattern with re.IGNORECASE). [^;] : a statement
# never runs past its own semicolon.
_IMPORT_FROM = (
    r"""(?m)^(?P<matched>(?-i:import)"""
    r"""(?P<import>[\s\{][^;]*?|\*\s*as\s*\w+)"""
    r"""\s*(?-i:from)\s*['"](?P<url>[./][^'"]*?)["']\s*;)"""
)
_EXPORT_FROM = (
    r"""(?m)^(?P<matched>(?-i:export)(?P<exports>[\s\{][^;]*?)"""
    r"""\s*(?-i:from)\s*["'](?P<url>[./][^'"]*?)["']\s*;)"""
)
_IMPORT_FILE = r"""(?m)^(?P<matched>(?-i:import)\s*['"](?P<url>[./][^'"]*?)["']\s*;)"""
# A dynamic import can sit anywhere in a line.
_IMPORT_CALL = r"""(?P<matched>(?-i:import)\(["'](?P<url>[^'"]*?)["']\))"""


class RasovaStaticStorage(CompressedManifestStaticFilesStorage):
    support_js_module_import_aggregation = True
    _js_module_import_aggregation_patterns = (
        "*.js",
        (
            (_IMPORT_FROM, """import%(import)s from "%(url)s";"""),
            (_EXPORT_FROM, """export%(exports)s from "%(url)s";"""),
            (_IMPORT_FILE, """import"%(url)s";"""),
            (_IMPORT_CALL, """import("%(url)s")"""),
        ),
    )
