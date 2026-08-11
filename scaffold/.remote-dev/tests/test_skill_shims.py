from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ClaudeSkillShimTests(unittest.TestCase):
    def test_shim_check_passes(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / ".remote-dev/tools/sync_claude_skills.py"),
                "--check",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_shims_point_to_canonical_skills(self) -> None:
        for source in sorted((ROOT / ".agents/skills").glob("*/SKILL.md")):
            target = ROOT / ".claude/skills" / source.parent.name / "SKILL.md"
            with self.subTest(skill=source.parent.name):
                body = target.read_text(encoding="utf-8")
                self.assertIn(
                    f"`.agents/skills/{source.parent.name}/SKILL.md`", body
                )
                self.assertLessEqual(len(body.splitlines()), 60)


if __name__ == "__main__":
    unittest.main()
