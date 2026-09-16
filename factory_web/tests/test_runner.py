import unittest

from pathlib import Path

from runner import CliRunner, parse_json_output


class ParseJsonOutputTests(unittest.TestCase):
    def test_parses_plain_cli_json(self):
        self.assertEqual(
            parse_json_output('{"ok":true,"version":"0x003c"}'),
            {"ok": True, "version": "0x003c"},
        )

    def test_preserves_compatibility_with_a_prefixed_final_json_line(self):
        self.assertEqual(
            parse_json_output('提示信息\n{"ok":false,"error":"timeout"}'),
            {"ok": False, "error": "timeout"},
        )

    def test_non_json_output_returns_none(self):
        self.assertIsNone(parse_json_output("not json"))

    def test_explicit_wait_can_extend_process_timeout(self):
        runner = CliRunner(Path("cli.py"), "/tmp/test.sock", timeout=180)

        self.assertEqual(runner._effective_timeout(["door", "open", "--timeout", "300"]), 315)
        self.assertEqual(runner._effective_timeout(["door", "open", "--timeout", "20"]), 180)


if __name__ == "__main__":
    unittest.main()
