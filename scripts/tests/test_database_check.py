import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database_check


class DatabaseCheckTests(unittest.TestCase):
    def test_exports_into_requested_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            frontend = Path(directory) / "alternate RhineLabUI"
            (frontend / "node_modules").mkdir(parents=True)
            (frontend / "package.json").write_text("{}", encoding="utf-8")

            with patch.object(database_check, "_server_alive", return_value=True):
                code = database_check.main([
                    "--rhinelab-dir", str(frontend), "--no-open",
                ])

            self.assertEqual(code, 0)
            content = json.loads(
                (frontend / "content" / "archives.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(content["categories"]), 5)
