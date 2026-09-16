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
        self.assertIn('data-cli="door angle"', html)
        self.assertIn("mcu_readback_command_id", javascript)
        self.assertIn('<option value="90">90°</option>', html)
        self.assertIn('<option value="120">120°</option>', html)
        self.assertIn('args: ["door", "angle", angle]', javascript)
        self.assertIn('data-status-id', javascript)

    def test_button_angle_internal_values_are_localized(self):
        javascript = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        for internal, label in (
            ("default", "默认值"),
            ("settings_file", "已保存配置"),
            ("environment", "环境变量"),
            ("command_line", "启动参数"),
            ("applied", "已生效"),
            ("unsupported_firmware", "固件不支持"),
        ):
            self.assertIn(f'{internal}: "{label}"', javascript)
        self.assertIn("localizedAngleValue(parsed.source, angleSourceLabels)", javascript)
        self.assertIn("localizedAngleValue(parsed.status, angleStatusLabels)", javascript)

    def test_motor_homing_configuration_uses_cli_contract(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="motor-config-card"', html)
        self.assertIn('data-cli="motor scan"', html)
        self.assertIn('data-cli="motor config read"', html)
        self.assertIn('data-cli="motor config auto"', html)
        self.assertIn("不会触发回零，也不会发送运动指令", html)
        self.assertIn('data-motor-config="homing_speed_rpm"', html)
        self.assertIn('data-motor-config="collision_current_ma"', html)
        self.assertIn("是（协议无回读）", html)
        self.assertIn('"requested_store"', javascript)
        self.assertIn("motorConfigTarget", javascript)
        self.assertIn("renderMotorConfig(result, action)", javascript)
        self.assertIn("appendLog(label, result)", javascript)


if __name__ == "__main__":
    unittest.main()
