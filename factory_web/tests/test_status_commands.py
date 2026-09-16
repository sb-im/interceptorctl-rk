import unittest

from commands import STATUS_COMMANDS


class StatusCommandTests(unittest.TestCase):
    def test_component_ids_are_unique(self):
        ids = [item[0] for item in STATUS_COMMANDS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_component_commands_are_present(self):
        commands = {item[0]: item[2] for item in STATUS_COMMANDS}
        self.assertEqual(commands["version"], ("version",))
        self.assertEqual(commands["button_angle"], ("door", "angle"))
        self.assertEqual(commands["motor"], ("motor", "status"))
        self.assertEqual(commands["power"], ("power", "status"))
        self.assertEqual(commands["ac"], ("ac", "status"))


if __name__ == "__main__":
    unittest.main()
