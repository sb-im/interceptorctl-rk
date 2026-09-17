import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


class StaticUiTests(unittest.TestCase):
    def test_system_page_has_live_status_grid(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="system-status-grid"', html)
        self.assertIn('id="refresh-system"', html)

    def test_static_assets_use_cache_busting_versions(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/styles.css?v=20260917-estop-angle-1', html)
        self.assertIn('/static/app.js?v=20260917-estop-angle-1', html)

    def test_button_angle_control_uses_cli_contract(self):
        html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="button-angle-form"', html)
        self.assertIn('data-cli="door angle"', html)
        self.assertIn("mcu_readback_command_id", javascript)
        self.assertIn('<option value="90">90°</option>', html)
        self.assertIn('<option value="120">120°</option>', html)
        self.assertIn("RK/API 开门、实体按钮开门和急停释放后的自动开门统一使用此角度", html)
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
        self.assertIn("motor_id_changed", javascript)
        self.assertIn("motor_id_verified", javascript)
        self.assertIn("扫描会向 ID 1～32 逐个发送只读 1F 6B，不使用广播", html)
        self.assertIn("正在逐个定向扫描电机 ID 1～32（只读 1F 6B，不使用广播）", javascript)
        self.assertIn("renderMotorConfig(result, action)", javascript)
        self.assertIn("appendLog(label, result)", javascript)


if __name__ == "__main__":
    unittest.main()
