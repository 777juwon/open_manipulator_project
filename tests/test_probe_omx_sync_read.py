"""SDK 수신 결과를 바꾸지 않고 기록하는지 장치 연결 없이 검사한다."""

import importlib.util
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_probe", ROOT / "tools/probe_omx_sync_read.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyncReadProbeTests(unittest.TestCase):
    def test_handler_preserves_the_exact_sdk_response(self):
        response = ([], -3001, 0)

        class FakeHandler:
            def readRx(self, port, motor_id, length):
                return response

        handler = MODULE.make_traced_handler(FakeHandler)
        self.assertIs(handler.readRx(None, 14, 4), response)
        self.assertEqual(handler.responses, [(14, -3001, 0)])

    def test_failed_read_records_waiting_id_without_retry(self):
        handler = types.SimpleNamespace(responses=[(99, 0, 0)])

        class Reader:
            calls = 0

            def txRxPacket(self):
                self.calls += 1
                handler.responses.extend([(11, 0, 0), (12, 0, 0), (14, -3001, 0)])
                return -3001

        reader = Reader()
        clock = iter([1.0, 1.035])
        row, start = MODULE.read_once(reader, handler, 4, 0.5, 0.96, lambda: next(clock))
        self.assertEqual(reader.calls, 1)
        self.assertEqual(row["comm_result"], -3001)
        self.assertEqual(row["waiting_id"], 14)
        self.assertNotIn("99", row["responses"])
        self.assertAlmostEqual(row["read_ms"], 35)
        self.assertAlmostEqual(row["interval_ms"], 40)
        self.assertEqual(start, 1.0)

    def test_successful_read_has_no_waiting_id(self):
        handler = types.SimpleNamespace(responses=[])

        class Reader:
            def txRxPacket(self):
                handler.responses.extend((motor_id, 0, 0) for motor_id in MODULE.MOTOR_IDS)
                return 0

        clock = iter([1.0, 1.002])
        row, _ = MODULE.read_once(Reader(), handler, 1, 1.0, None, lambda: next(clock))
        self.assertEqual(row["waiting_id"], "")
        self.assertEqual(row["interval_ms"], "")
        self.assertEqual(row["comm_result"], 0)
        self.assertAlmostEqual(row["read_ms"], 2)


if __name__ == "__main__":
    unittest.main()
