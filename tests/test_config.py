from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.config import logging_level


class ConfigTests(unittest.TestCase):
    def test_logging_level_comes_from_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".legacylens.json"
            config_path.write_text(json.dumps({"logging": {"level": "DEBUG"}}), encoding="utf-8")
            with patch.dict("os.environ", {"LEGACYLENS_CONFIG": str(config_path)}, clear=True):
                self.assertEqual(logging_level(), "DEBUG")

    def test_logging_level_environment_still_overrides_for_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".legacylens.json"
            config_path.write_text(json.dumps({"logging": {"level": "INFO"}}), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {"LEGACYLENS_CONFIG": str(config_path), "LEGACYLENS_LOG_LEVEL": "WARNING"},
                clear=True,
            ):
                self.assertEqual(logging_level(), "WARNING")


if __name__ == "__main__":
    unittest.main()
