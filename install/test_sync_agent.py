import tempfile
import unittest
from pathlib import Path

from sync_agent import LABEL, build_plist


class SyncAgentTests(unittest.TestCase):
    def test_plist_runs_at_load_and_every_five_calendar_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = root / "quill-sync"
            state = root / "state"
            value = build_plist(program, state)

        self.assertEqual(value["Label"], LABEL)
        self.assertEqual(value["ProgramArguments"], [str(program)])
        self.assertTrue(value["RunAtLoad"])
        self.assertEqual(
            value["StartCalendarInterval"],
            [{"Minute": minute} for minute in range(0, 60, 5)],
        )
        self.assertNotIn("StartInterval", value)
        self.assertEqual(value["ProcessType"], "Background")


if __name__ == "__main__":
    unittest.main()
