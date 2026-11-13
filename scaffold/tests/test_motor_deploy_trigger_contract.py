from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ROOT / "scaffold/.agents/skills/motor-deploy/SKILL.md"
CURSOR = ROOT / ".cursor/skills/motor-deploy/SKILL.md"
CLAUDE_SHIMS = (
    ROOT / ".claude/skills/motor-deploy/SKILL.md",
    ROOT / "scaffold/.claude/skills/motor-deploy/SKILL.md",
)


def description(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"missing description in {path}")


class MotorDeployTriggerContractTest(unittest.TestCase):
    def test_discovery_descriptions_share_the_narrow_gate(self) -> None:
        expected = description(CANONICAL)
        for path in (CURSOR, *CLAUDE_SHIMS):
            with self.subTest(path=path):
                self.assertEqual(description(path), expected)

        for phrase in (
            "Motor's native deployer",
            "named service/model",
            "concrete target environment",
            "live lifecycle action",
            "Docker-only",
            "RBG",
            "generic start/deploy wording",
        ):
            self.assertIn(phrase, expected)

    def test_dispatcher_documents_positive_and_negative_cases(self) -> None:
        body = CANONICAL.read_text(encoding="utf-8")
        for phrase in (
            "all three",
            "Motor-native anchor",
            "Concrete target and service",
            "Live lifecycle action",
            "在 120 集群用 Motor 部署 DeepSeek-V4 1P1D 服务",
            "起 Docker-only 1P1D",
            "起 RBG",
            "整理 Coordinator 一键脚本和适配指南",
        ):
            self.assertIn(phrase, body)

    def test_repository_entry_uses_the_same_conjunctive_gate(self) -> None:
        body = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in (
            "all three deployment anchors",
            "Motor's native deployer",
            "concrete target environment and named",
            "live lifecycle action",
            "Docker-only and",
            "RBG workflows",
        ):
            self.assertIn(phrase, body)


if __name__ == "__main__":
    unittest.main()
