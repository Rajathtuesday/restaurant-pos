"""
Build step of the Rasova box image: Rasova's own Python code becomes
compiled machine code, and no .py source goes into the box.

    python box/docker/compile.py <source tree> <output tree> [--keep-tests [--without-tests-into <tree>]]

  * Copies the parts of the project a box runs (not the docs, the Android
    app, the marketing site, dev scripts or, unless --keep-tests, the tests).
  * Compiles every .py file with Cython into a .so extension module, then
    deletes the .py and the generated .c. What is left is machine code: it
    runs the same, but there is no source to read, copy or edit.
  * Keeps as plain .py only what can't or needn't be compiled: manage.py
    (a three-line launcher), the packages' __init__.py files (empty, or
    one import line each) and the migrations (their names start with a
    digit, which a compiled module can't have; they describe the database's
    tables, which anyone holding the database can see anyway).
  * Templates, static files and fonts are copied as they are: a browser sees
    the pages anyway.

--keep-tests leaves the tests as .py (and compiles everything else), so the
whole test suite can run against the compiled code: the proof that
compiling changed nothing. --without-tests-into then copies the same
compiled tree minus the tests: what ships is exactly what was tested, and
it is compiled once.
"""
import os
import shutil
import sys
from pathlib import Path

SKIP_TOP = {
    ".git", ".github", ".venv", "venv", "node_modules", "md_files", "graft", "public", "loadtest_logs", "logs",
    "media", "staticfiles", "rasova_android", "agent", "scripts", "docs", "er_diagrams_pos", "js", "golden",
    ".claude", "__pycache__", "examples.json", ".mcp.json", ".ignore", ".env", "db.sqlite3",
}
SKIP_ANY = {"__pycache__", "node_modules", ".pytest_cache", ".hypothesis"}
KEEP_AS_SOURCE = {"manage.py", "__init__.py"}


def keep_as_source(rel):
    return rel.name in KEEP_AS_SOURCE or "migrations" in rel.parts[:-1]

DIRECTIVES = {
    "language_level": 3,
    "binding": True,                 # functions keep their signatures for Django/Celery introspection
    "annotation_typing": False,      # annotations stay hints, never C types
    "always_allow_keywords": True,
    "emit_code_comments": False,     # no source lines copied into the .c
    "embedsignature": False,
}


def is_test(rel):
    parts = rel.parts
    return "tests" in parts[:-1] or rel.name in ("tests.py", "conftest.py")


def copy_tree(src, out, keep_tests):
    files = []
    for root, dirs, names in os.walk(src):
        root_p = Path(root)
        rel_root = root_p.relative_to(src)
        if rel_root == Path("."):
            dirs[:] = [d for d in dirs if d not in SKIP_TOP]
            names = [n for n in names if n not in SKIP_TOP]
        dirs[:] = [d for d in dirs if d not in SKIP_ANY and not (rel_root == Path("box") and d in ("docker", "host"))]
        for name in names:
            rel = rel_root / name
            if name.endswith((".pyc", ".pyo", ".c", ".so", ".pyd")):
                continue
            if not keep_tests and name.endswith(".py") and is_test(rel):
                continue
            if not keep_tests and "tests" in rel.parts[:-1]:
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_p / name, dest)
            files.append(rel)
    return files


def main():
    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    keep_tests = "--keep-tests" in sys.argv
    if out.exists():
        shutil.rmtree(out)
    files = copy_tree(src, out, keep_tests)
    to_compile = [f for f in files if f.suffix == ".py" and not keep_as_source(f)
                  and not (keep_tests and is_test(f))]

    from Cython.Build import cythonize
    from setuptools import Extension, setup

    os.chdir(out)
    extensions = [Extension(".".join(f.with_suffix("").parts), [str(f)]) for f in to_compile]
    setup(
        name="rasova",
        script_args=["build_ext", "--inplace", "-j", str(os.cpu_count() or 2)],
        ext_modules=cythonize(extensions, compiler_directives=DIRECTIVES, nthreads=os.cpu_count() or 2, quiet=True),
    )
    for f in to_compile:
        (out / f).unlink()
        (out / f.with_suffix(".c")).unlink(missing_ok=True)
    shutil.rmtree(out / "build", ignore_errors=True)
    left = [p for p in out.rglob("*.py")
            if not keep_as_source(p.relative_to(out)) and not (keep_tests and is_test(p.relative_to(out)))]
    if left:
        sys.exit(f"Source left behind: {left[:5]}")
    if "--without-tests-into" in sys.argv:
        ship = Path(sys.argv[sys.argv.index("--without-tests-into") + 1]).resolve()
        if ship.exists():
            shutil.rmtree(ship)
        shutil.copytree(out, ship, ignore=lambda folder, names: [
            n for n in names if n == "tests" or n in ("tests.py", "conftest.py")])
        stray = [p for p in ship.rglob("*.py") if not keep_as_source(p.relative_to(ship))]
        if stray:
            sys.exit(f"Source left behind in {ship}: {stray[:5]}")
    print(f"compiled {len(to_compile)} modules; source kept only for {sorted(KEEP_AS_SOURCE)} and migrations"
          + (" and the tests" if keep_tests else ""))


if __name__ == "__main__":
    main()
