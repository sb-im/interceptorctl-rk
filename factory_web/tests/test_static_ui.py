import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


class StaticUiTests(unittest.TestCase):
    def test_system_page_has_live_status_grid(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="system-status-grid"', html)
        self.assertIn('id="refresh-system"', html)

    def test_button_angle_control_uses_cli_contract(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="button-angle-form"', html)
        self.assertIn('<option value="90">90°</option>', html)
        self.assertIn('<option value="120">120°</option>', html)
        self.assertIn('args: ["door", "angle", angle]', javascript)
        self.assertIn('data-status-id', javascript)


if __name__ == "__main__":
    unittest.main()
