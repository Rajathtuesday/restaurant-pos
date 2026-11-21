"""
deploy.sh's undo (B1, option B, 13 Nov 2026), run for real.

The real deploy.sh runs against a throwaway git repository: an "origin"
whose qsr branch has a good commit A and a newer commit B, and an app folder
running A. The server's commands (python, pip, pg_dump, systemctl, curl,
sudo...) are small stand-ins that write what they were asked to a log and
answer as each case needs: B's /health/ answers 500, migrate fails, the
dump fails. git, gzip and bash itself are real.

What it proves, case by case:
  a good deploy moves to B, after saving A, its libraries, its static file
  names and a database dump;
  a new version that does not come up is replaced by A by itself, with A's
  libraries and static file names back;
  a step that fails half way (migrate) puts A back;
  --undo after a good deploy puts A back;
  --undo with nothing saved changes nothing;
  a failed dump stops the deploy before anything changes.

Skipped where there is no bash (on Windows, Git's own bash is used, never
the WSL one).

Run: python manage.py test core.tests.test_deploy_script
"""
import gzip
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.test import SimpleTestCase

DEPLOY = Path(settings.BASE_DIR) / "deploy.sh"

STUBS = {
    "python": r'''#!/bin/bash
echo "python $*" >> "$STUB_LOG"
case "$*" in
  *"import weasyprint"*) exit 0 ;;
  *"manage.py migrate"*) exit "$(cat "$STUB_DIR/migrate_exit" 2>/dev/null || echo 0)" ;;
  *"manage.py collectstatic"*) mkdir -p staticfiles; echo "NEW $(git rev-parse --short HEAD)" > staticfiles/staticfiles.json ;;
esac
exit 0
''',
    # Like the real pip: `install -r` puts in the versions the file names and
    # leaves every other library where it was; `uninstall` takes them out.
    "pip": r'''#!/bin/bash
echo "pip $*" >> "$STUB_LOG"
F="$STUB_DIR/freeze"
case "$1" in
  freeze) cat "$F" ;;
  install)
    if [ "$2" = "-r" ]; then
      for line in $(grep -v '^[-#]' "$3"); do
        name=${line%%==*}
        { grep -v "^$name==" "$F"; echo "$line"; } | sort > "$F.new"; mv "$F.new" "$F"
      done
    fi ;;
  uninstall)
    shift
    for name in "$@"; do
      case "$name" in -*) ;; *) grep -v "^$name==" "$F" > "$F.new"; mv "$F.new" "$F" ;; esac
    done ;;
esac
exit 0
''',
    "curl": r'''#!/bin/bash
sha=$(git -C "$RASOVA_APP_DIR" rev-parse HEAD)
if [ -f "$STUB_DIR/health_$sha" ]; then cat "$STUB_DIR/health_$sha"; else echo -n 200; fi
''',
    "pg_dump": r'''#!/bin/bash
echo "pg_dump $*" >> "$STUB_LOG"
[ -f "$STUB_DIR/dump_fail" ] && exit 1
echo "DUMP $PGPASSWORD $*"
''',
    "sudo": r'''#!/bin/bash
echo "sudo $*" >> "$STUB_LOG"
[ "$1" = "-u" ] && shift 2
case "$1" in
  test|python|.venv/bin/python) exec "$@" ;;
esac
exit 0
''',
    "systemctl": r'''#!/bin/bash
case "$*" in
  *"show -p MainPID"*) echo 123 ;;
  *is-active*) echo active ;;
esac
exit 0
''',
    "pgrep": r'''#!/bin/bash
[ "$1" = "-fc" ] && echo 1 && exit 0
exit 1
''',
    "crontab": r'''#!/bin/bash
[ "$1" = "-l" ] && echo "# rasova-backups-managed" && exit 0
cat > /dev/null
''',
}
SILENT = ("pkill", "setsid", "nohup", "fuser", "sleep", "gunicorn", "celery", "setfacl")


def find_bash():
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    for parent in Path(git).resolve().parents:
        candidate = parent / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    return None


class DeployUndoTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bash = find_bash()
        if not cls.bash:
            raise SkipTest("no bash (Git's bash on Windows)")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rasova_deploy_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stub_dir = self.root / "stubstate"
        self.stub_dir.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            self.write(self.bin / name, body)
        for name in SILENT:
            self.write(self.bin / name, "#!/bin/bash\nexit 0\n")
        self.log = self.stub_dir / "log"
        self.log.write_text("")
        (self.stub_dir / "freeze").write_text("lib==1\n")

        # The origin, with A; the app folder runs A; then B lands on origin.
        origin = self.root / "origin.git"
        self.git(self.root, "init", "--bare", "-b", "qsr", str(origin))
        work = self.root / "work"
        self.git(self.root, "clone", "-q", str(origin), str(work))
        self.git(work, "checkout", "-q", "-b", "qsr")
        self.write(work / "app.txt", "A\n")
        self.write(work / "requirements.txt", "lib==1\n")
        self.write(work / "static" / "app.css", "a {}\n")
        self.write(work / "systemd" / "celery-beat.service", "[Unit]\n")
        self.commit(work, "A")
        self.git(work, "push", "-q", "origin", "qsr")
        self.a = self.git(work, "rev-parse", "HEAD").strip()

        self.app = self.root / "app"
        self.git(self.root, "clone", "-q", "-b", "qsr", str(origin), str(self.app))
        self.write(self.app / ".venv" / "bin" / "activate", "")
        self.write(self.app / ".venv" / "bin" / "python", STUBS["python"])
        self.write(self.app / ".env", 'DB_NAME=rasova\nDB_USER=rasova\nDB_PASSWORD="s3cret"\nDB_HOST=localhost\nDB_PORT=5432\n')
        self.write(self.app / "staticfiles" / "staticfiles.json", "OLD\n")
        (self.app / "logs").mkdir()

        self.write(work / "app.txt", "B\n")
        self.write(work / "requirements.txt", "lib==2\nextra==1\n")    # B adds a library
        self.write(work / "static" / "app.css", "b {}\n")
        self.commit(work, "B")
        self.git(work, "push", "-q", "origin", "qsr")
        self.b = self.git(work, "rev-parse", "HEAD").strip()

    # ---- helpers ------------------------------------------------------------

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        path.chmod(0o755)

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    def commit(self, work, message):
        self.git(work, "add", "-A")
        self.git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)

    def deploy(self, *args):
        env = dict(os.environ, RASOVA_APP_DIR=self.app.as_posix(), RASOVA_HEALTH_WAIT="2",
                   STUB_DIR=self.stub_dir.as_posix(), STUB_LOG=self.log.as_posix(), STUB_BIN=str(self.bin))
        # The stand-ins go first on PATH from inside bash: Git's bash.exe puts
        # its own tool folders (with a real curl) before the PATH it inherits.
        first = ('p="$STUB_BIN"; command -v cygpath >/dev/null && p=$(cygpath -u "$p"); '
                 'export PATH="$p:$PATH"; exec bash "$0" "$@"')
        done = subprocess.run([self.bash, "-c", first, DEPLOY.as_posix(), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def head(self):
        return self.git(self.app, "rev-parse", "HEAD").strip()

    def state(self, name):
        return (self.app / "versions" / "last" / name)

    def static_names(self):
        return (self.app / "staticfiles" / "staticfiles.json").read_text().strip()

    # ---- the cases ----------------------------------------------------------

    def test_a_good_deploy_saves_what_ran_then_moves_on(self):
        code, out = self.deploy()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.head(), self.b)
        self.assertEqual(self.state("commit").read_text().strip(), self.a)
        self.assertEqual(self.state("requirements.freeze").read_text(), "lib==1\n")
        self.assertEqual(self.state("staticfiles.json").read_text().strip(), "OLD")
        self.assertEqual(self.static_names(), f"NEW {self.b[:7]}")
        dump = gzip.decompress(self.state("before.sql.gz").read_bytes()).decode()
        self.assertIn("DUMP s3cret -h localhost -p 5432 -U rasova --no-owner --no-privileges rasova", dump)
        log = self.log.read_text()
        self.assertLess(log.index("pg_dump"), log.index("manage.py migrate"), "the dump must come before migrate")
        self.assertIn("Deploy complete", out)
        self.assertNotIn("UNDO", out)

    def test_a_new_version_that_does_not_come_up_is_replaced_by_the_old_one(self):
        (self.stub_dir / f"health_{self.b}").write_text("500")
        code, out = self.deploy()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.head(), self.a)
        self.assertEqual(self.static_names(), "OLD", "the old static file names must be back")
        self.assertIn("pip install -r " + self.state("requirements.freeze").as_posix(), self.log.read_text(),
                      "the saved libraries must be reinstalled")
        self.assertEqual((self.stub_dir / "freeze").read_text(), "lib==1\n",
                         "the libraries must be exactly the old version's")
        self.assertIn("did not answer", out)
        self.assertIn(f"Undo done: {self.a} is running again", out)
        self.assertIn("The database was NOT rolled back", out)

    def test_a_step_that_fails_half_way_puts_the_old_version_back(self):
        (self.stub_dir / "migrate_exit").write_text("1")
        code, out = self.deploy()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.head(), self.a)
        self.assertIn("Deploy failed (line", out)
        self.assertIn("Undo done", out)

    def test_undo_by_hand_after_a_good_deploy(self):
        self.assertEqual(self.deploy()[0], 0)
        code, out = self.deploy("--undo")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.head(), self.a)
        self.assertEqual(self.static_names(), "OLD")
        self.assertIn(f"from {self.b} back to {self.a}", out)

    def test_undo_leaves_exactly_the_old_libraries(self):
        """pip install -r of the saved list puts A's versions back but leaves
        what B added. On the server (21 Nov 2026, the first undo rehearsal)
        B's httpx2 stayed and clashed with A's idna. The undo also takes out
        what is not in A's list."""
        self.assertEqual(self.deploy()[0], 0)
        self.assertIn("extra==1", (self.stub_dir / "freeze").read_text())
        code, out = self.deploy("--undo")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.stub_dir / "freeze").read_text(), "lib==1\n")
        self.assertIn("Removing libraries the undone version added: extra", out)

    def test_undo_with_nothing_saved_changes_nothing(self):
        code, out = self.deploy("--undo")
        self.assertEqual(code, 1, out)
        self.assertIn("nothing saved to undo to", out)
        self.assertEqual(self.head(), self.a)

    def test_a_failed_dump_stops_before_anything_changes(self):
        (self.stub_dir / "dump_fail").write_text("")
        code, out = self.deploy()
        self.assertEqual(code, 1, out)
        self.assertIn("database dump before migrate failed", out)
        self.assertEqual(self.head(), self.a)
        self.assertNotIn("manage.py migrate", self.log.read_text())
        self.assertFalse(self.state("before.sql.gz").exists())
