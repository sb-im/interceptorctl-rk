"""Component status commands shared by the API and tests."""

STATUS_COMMANDS = (
    ("version", "MCU 固件", ("version",)),
    ("system", "整机状态", ("status",)),
    ("estop", "急停状态", ("estop",)),
    ("button_angle", "统一开门角度", ("door", "angle")),
    ("motor", "电机状态", ("motor", "status")),
    ("power", "直流电源", ("power", "status")),
    ("ups", "UPS", ("ups", "status")),
    ("environment", "环境温湿度", ("env", "status")),
    ("led", "LED", ("led", "status")),
    ("switch", "开关量", ("switch", "status")),
    ("ac", "空调", ("ac", "status")),
)
