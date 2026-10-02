"""
The print agent is downloaded from rasova.net (/agent/<file>), not from the
GitHub repository: the Windows installer used to fetch rasova_agent.py from
raw.githubusercontent.com, which only works while the repository is public.

Run: python manage.py test core.test_agent_download
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase

AGENT = Path(settings.BASE_DIR) / "agent"


class AgentDownloadTest(TestCase):
    def test_the_script_is_served_without_login(self):
        resp = self.client.get("/agent/rasova_agent.py")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), (AGENT / "rasova_agent.py").read_bytes())
        self.assertIn('filename="rasova_agent.py"', resp["Content-Disposition"])

    def test_the_installer_is_served(self):
        resp = self.client.get("/agent/rasova_agent_installer.bat")
        self.assertEqual(resp.status_code, 200)

    def test_nothing_else_in_the_folder_is(self):
        for name in ("test_rasova_agent.py", "__init__.py", "../manage.py", "..%2Fmanage.py"):
            self.assertEqual(self.client.get(f"/agent/{name}").status_code, 404, name)

    def test_the_installer_downloads_from_rasova_not_github(self):
        installer = (AGENT / "rasova_agent_installer.bat").read_text(encoding="utf-8")
        self.assertIn("https://rasova.net/agent/rasova_agent.py", installer)
        self.assertNotIn("githubusercontent", installer)
