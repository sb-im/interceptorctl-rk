import importlib.util
import sys
import unittest
from pathlib import Path


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "aircraft_485_loss_test.py"
SPEC = importlib.util.spec_from_file_location("aircraft_485_loss_test", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


class FakeSimulator:
    invalid_frames = 0

    def received(self, sequence: int) -> bool:
        return sequence in {1, 2}

    def replied(self, sequence: int) -> bool:
        return sequence == 1


class AircraftFrameTests(unittest.TestCase):
    def test_frame_is_exactly_thirty_bytes_and_round_trips(self) -> None:
        raw = tool.make_frame(tool.FRAME_KIND_REQUEST, 123, 0x10203040, 987654321)
        self.assertEqual(len(raw), 30)
        decoded = tool.TestFrame.decode(raw)
        self.assertEqual(decoded.kind, tool.FRAME_KIND_REQUEST)
        self.assertEqual(decoded.sequence, 123)
        self.assertEqual(decoded.session, 0x10203040)
        self.assertEqual(decoded.timestamp_us, 987654321)

    def test_response_is_deterministic_and_corruption_is_detected(self) -> None:
        request_raw = tool.make_frame(tool.FRAME_KIND_REQUEST, 9, 7, 12345)
        request = tool.TestFrame.decode(request_raw)
        response = tool.response_for(request)
        self.assertEqual(response, tool.response_for(request))
        self.assertEqual(tool.TestFrame.decode(response).kind, tool.FRAME_KIND_RESPONSE)

        corrupt = bytearray(response)
        corrupt[11] ^= 0x01
        with self.assertRaises(tool.FrameError):
            tool.TestFrame.decode(bytes(corrupt))

    def test_stats_reports_loss_rate_and_path_classification(self) -> None:
        simulator = FakeSimulator()
        stats = tool.TestStats(attempted=4, received_ok=1)
        stats.record_loss_path(simulator, 1)
        stats.record_loss_path(simulator, 2)
        stats.record_loss_path(simulator, 3)
        summary = stats.summary(simulator, 0x55)
        self.assertEqual(summary["lost"], 3)
        self.assertEqual(summary["loss_rate_percent"], 75.0)
        self.assertEqual(summary["return_path_lost"], 1)
        self.assertEqual(summary["unclassified_lost"], 1)
        self.assertEqual(summary["forward_path_lost"], 1)


if __name__ == "__main__":
    unittest.main()
