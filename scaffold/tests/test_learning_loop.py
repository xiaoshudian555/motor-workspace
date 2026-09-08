import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/agent-skill-evolution/scripts/learning_check.py"
spec = importlib.util.spec_from_file_location("learning_check", SCRIPT)
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)


class LearningLoopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".motor-workspace-local/learning/candidates/gap.json"
        self.event = dict(id="gap", owner="test", status="pending", expected="query job",
                          actual="repeat launch", evidence=["fixture: ambiguous response"],
                          scope="timeout; not confirmed failure", blocker="needs replay",
                          next_action="run authorized replay")
        self.receipt = dict(task="test", events=["gap"], consumed=[])

    def save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def check(self):
        self.save(self.path, self.event)
        self.save(self.root / "receipt.json", self.receipt)
        return loop.check_receipt(self.root, Path("receipt.json"))

    def test_pending_is_recoverable_not_promoted(self):
        self.assertEqual(self.check()["pending"][0]["id"], "gap")
        self.assertEqual(loop.candidate(self.root, self.path)["next_action"], "run authorized replay")

    def test_missing_event_fails(self):
        self.save(self.root / "receipt.json", self.receipt)
        with self.assertRaises(OSError):
            loop.check_receipt(self.root, Path("receipt.json"))

    def test_no_event_requires_reason(self):
        self.receipt["events"] = []
        with self.assertRaises(ValueError):
            self.check()
        self.receipt["no_event_reason"] = "ordinary environment failure; no decision gap"
        self.assertEqual(self.check()["pending"], [])

    def test_pending_needs_next_action(self):
        del self.event["next_action"]
        with self.assertRaises(ValueError):
            self.check()

    def test_unverified_promotion_fails(self):
        self.event["status"] = "promoted"
        with self.assertRaises(ValueError):
            self.check()

    def test_promotion_consumption_and_recurrence(self):
        self.save(self.root / "lesson.md", {"rule": "fixture"})
        self.save(self.root / "index.md", {"lesson": "lesson.md"})
        self.event.update(status="promoted", verification="fixture regression passed",
                          change="fixture change", lesson="lesson.md", index="index.md")
        self.receipt["consumed"] = [dict(lesson="lesson.md", result="queried existing job")]
        self.assertEqual(self.check()["consumed_count"], 1)
        self.assertEqual(self.check()["pending"], [])
        self.event.update(status="pending", blocker="recurrence", next_action="inspect new evidence")
        self.assertEqual(len(self.check()["pending"]), 1)

    def test_path_traversal_rejected(self):
        self.receipt["events"] = ["../gap"]
        with self.assertRaises(ValueError):
            self.check()

    def test_unindexed_promotion_fails(self):
        (self.root / "lesson.md").write_text("rule")
        (self.root / "index.md").write_text("empty")
        self.event.update(status="promoted", verification="actual check", change="edit",
                          lesson="lesson.md", index="index.md")
        with self.assertRaises(ValueError):
            self.check()


if __name__ == "__main__":
    unittest.main()
