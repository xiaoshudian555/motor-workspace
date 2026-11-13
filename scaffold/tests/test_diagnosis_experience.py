import importlib.util
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools/diagnosis_experience.py"
SKILL = ROOT / ".agents/skills/motor-diagnosis/SKILL.md"
LOOKUP = Path.home() / "projects/doc/scripts/lookup-experience.py"
QUEUE = Path.home() / "projects/doc/user_model/evidence_queue.tsv"


def load_lookup():
    spec = importlib.util.spec_from_file_location("lookup_experience", LOOKUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiagnosisExperienceTest(unittest.TestCase):
    def run_entry(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(ENTRY), "--no-ledger", *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_skill_runs_this_entry(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("scaffold/tools/diagnosis_experience.py", text)
        self.assertIn("HIT", text)
        self.assertIn("no_match", text)

    def test_promoted_log_proof_is_a_hit_until_check_passes(self):
        queue = QUEUE.read_text(encoding="utf-8")
        self.assertIn("R074", queue)
        self.assertIn("promoted", queue)
        self.assertIn("EXP-LOG-PROOF.md", queue)
        result = self.run_entry(
            "--symptom",
            "日志看起来还是负载不均，这算证明打散了吗",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("diagnosis_entry=motor-diagnosis", result.stdout)
        self.assertIn("HIT\tEXP-LOG-PROOF", result.stdout)
        self.assertIn("use_check\t核对本轮实际看到的日志来自几个 instance", result.stdout)
        self.assertNotIn("APPLIED\tEXP-LOG-PROOF", result.stdout)
        self.assertIn("experience_query hit=1 applied=0", result.stdout)
        passed = self.run_entry(
            "--symptom",
            "日志看起来还是负载不均，这算证明打散了吗",
            "--check",
            "EXP-LOG-PROOF=pass",
            "--observed",
            "EXP-LOG-PROOF=本轮日志只来自一个 instance",
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("APPLIED\tEXP-LOG-PROOF", passed.stdout)
        self.assertIn("不能宣称负载已经打散", passed.stdout)
        self.assertIn("experience_query hit=0 applied=1", passed.stdout)

    def test_missing_check_is_not_applied(self):
        missing = self.run_entry("--symptom", "日志看起来还是负载不均，这算证明打散了吗")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertNotIn("APPLIED\tEXP-LOG-PROOF", missing.stdout)
        self.assertIn("HIT\tEXP-LOG-PROOF", missing.stdout)
        self.assertIn("no_match=false", missing.stdout)
        failed = self.run_entry(
            "--symptom",
            "日志看起来还是负载不均，这算证明打散了吗",
            "--check",
            "EXP-LOG-PROOF=fail",
            "--observed",
            "EXP-LOG-PROOF=本轮日志来自多个 instance",
        )
        self.assertNotIn("APPLIED\tEXP-LOG-PROOF", failed.stdout)
        self.assertIn("CHECK_FAILED\tEXP-LOG-PROOF", failed.stdout)

    def test_miss_is_observable_and_limit_holds(self):
        missing = self.run_entry("--symptom", "解释一下 Mermaid 流程图语法")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("no_match=true", missing.stdout)
        self.assertIn("no_match", missing.stdout)
        limited = self.run_entry(
            "--limit",
            "1",
            "--symptom",
            "motor-deploy 的日志看起来还是负载不均",
        )
        self.assertEqual(limited.returncode, 0, limited.stderr)
        self.assertEqual(limited.stdout.count("HIT\t"), 1)
        self.assertNotIn("APPLIED\t", limited.stdout)
        self.assertIn("limit=1", limited.stdout)

    def test_superseded_card_is_not_returned(self):
        lookup = load_lookup()
        rows = [
            {
                "id": "EXP-OLD-LOG",
                "aliases": "负载不均",
                "skills": "motor-diagnosis",
                "card": "EXP-LOG-PROOF.md",
                "status": "superseded",
            }
        ]
        result = lookup.diagnose("负载不均的日志", rows, {"observed_instances": "single"})
        self.assertTrue(result["no_match"])
        self.assertEqual(result["applied"], [])

    def test_kv_4096_hits_and_one_card_pass_does_not_apply_others(self):
        hit = self.run_entry("--symptom", "kv conductor 的 4096 miss 重要吗")
        self.assertEqual(hit.returncode, 0, hit.stderr)
        self.assertIn("HIT\tEXP-KV-4096", hit.stdout)
        self.assertIn("不把这次 miss 归因到 4096", hit.stdout)
        self.assertNotIn("APPLIED\t", hit.stdout)
        both = self.run_entry(
            "--symptom",
            "motor-deploy 拉起时日志看起来还是负载不均",
            "--check",
            "EXP-LOG-PROOF=pass",
            "--observed",
            "EXP-LOG-PROOF=本轮日志只来自一个 instance",
        )
        self.assertEqual(both.returncode, 0, both.stderr)
        self.assertIn("APPLIED\tEXP-LOG-PROOF", both.stdout)
        self.assertIn("HIT\tEXP-WRONG-PATH", both.stdout)
        self.assertNotIn("APPLIED\tEXP-WRONG-PATH", both.stdout)

    def test_existing_lookup_self_test_still_passes(self):
        result = subprocess.run(
            ["python3", str(LOOKUP), "--self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
