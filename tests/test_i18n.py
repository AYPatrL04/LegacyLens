from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.i18n import resolve_output_language


class I18nTests(unittest.TestCase):
    def test_resolves_common_editor_locales(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_config = Path(tmp) / "missing.json"
            with patch.dict("os.environ", {"LEGACYLENS_CONFIG": str(missing_config)}, clear=True):
                self.assertEqual(resolve_output_language("zh-CN").code, "zh-Hans")
                self.assertEqual(resolve_output_language("zh-TW").code, "zh-Hant")
                self.assertEqual(resolve_output_language("ja-JP").code, "ja")
                self.assertEqual(resolve_output_language("French_France").code, "fr")

    def test_auto_uses_vscode_locale_before_system_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_config = Path(tmp) / "missing.json"
            with patch.dict("os.environ", {"LEGACYLENS_CONFIG": str(missing_config), "LANG": "de_DE.UTF-8"}, clear=True):
                self.assertEqual(resolve_output_language("auto", "ja-JP").code, "ja")

    def test_config_output_language_wins_over_request_and_vscode_locale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".legacylens.json"
            config_path.write_text(json.dumps({"outputLanguage": "fr-FR"}), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {"LEGACYLENS_CONFIG": str(config_path)},
                clear=True,
            ):
                self.assertEqual(resolve_output_language("ja-JP", "en-US").code, "fr")

    def test_unknown_locale_falls_back_to_english(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_config = Path(tmp) / "missing.json"
            with patch.dict("os.environ", {"LEGACYLENS_CONFIG": str(missing_config)}, clear=True):
                self.assertEqual(resolve_output_language("zz-ZZ").code, "en")


if __name__ == "__main__":
    unittest.main()
