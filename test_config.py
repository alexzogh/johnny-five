import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bridge import load_config

class ConfigTests(unittest.TestCase):
    def test_private_file_and_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'HERMES_API_KEY': 'dummy-test-value', 'CODEX_BIN': '~/.local/bin/codex'}))
            path.chmod(0o600)
            with patch.dict(os.environ, {'HERMES_API_KEY': 'override'}, clear=True):
                config = load_config(path)
            self.assertEqual(config['HERMES_API_KEY'], 'override')
            self.assertEqual(config['CODEX_BIN'], str(Path.home() / '.local/bin/codex'))

    def test_world_readable_private_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_config(path)

    def test_non_object_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('[]')
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                load_config(path)
