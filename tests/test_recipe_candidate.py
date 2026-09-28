from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml
from runner.recipe_candidate import materialize_agent_configs, stage_agent_configs


class RecipeCandidateTest(unittest.TestCase):
    def make_recipe(self, root: Path) -> Path:
        recipe = root / "recipe"
        (recipe / "agents").mkdir(parents=True)
        (recipe / "package.json").write_text(
            json.dumps({"name": "legal-agent", "pi": {"agents": ["./agents/*.yaml"]}})
        )
        (recipe / "agents" / "agent.yaml").write_text(
            "name: agent\nai:\n  model: anthropic/claude-sonnet-4-6\n"
            "  thinking_level: medium\nsession:\n  tool_execution: parallel\n"
            "tools: [read_file, bash]\nskills: []\nsubagents: []\n"
        )
        return recipe

    def test_stage_hides_frozen_blocks_without_changing_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recipe = self.make_recipe(root)
            staged = root / "staged"
            staged.mkdir()
            (staged / "agents").mkdir()
            (staged / "package.json").write_bytes((recipe / "package.json").read_bytes())
            (staged / "agents" / "agent.yaml").write_bytes(
                (recipe / "agents" / "agent.yaml").read_bytes()
            )

            stage_agent_configs(staged)

            spec = yaml.safe_load((staged / "agents" / "agent.yaml").read_text())
            self.assertEqual(spec["tools"], ["read_file", "bash"])
            self.assertEqual(spec["skills"], [])
            self.assertNotIn("ai", spec)
            self.assertNotIn("session", spec)
            self.assertIn("ai", yaml.safe_load((recipe / "agents" / "agent.yaml").read_text()))

    def test_materialize_restores_frozen_blocks_and_keeps_mutable_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trusted = self.make_recipe(root)
            staged = root / "staged"
            staged.mkdir()
            (staged / "agents").mkdir()
            (staged / "package.json").write_bytes((trusted / "package.json").read_bytes())
            (staged / "agents" / "agent.yaml").write_bytes(
                (trusted / "agents" / "agent.yaml").read_bytes()
            )
            stage_agent_configs(staged)
            spec = yaml.safe_load((staged / "agents" / "agent.yaml").read_text())
            spec["tools"].append("new_tool")
            (staged / "agents" / "agent.yaml").write_text(yaml.safe_dump(spec))

            materialize_agent_configs(staged, trusted)

            effective = yaml.safe_load((staged / "agents" / "agent.yaml").read_text())
            self.assertEqual(effective["ai"]["model"], "anthropic/claude-sonnet-4-6")
            self.assertEqual(effective["session"], {"tool_execution": "parallel"})
            self.assertEqual(effective["tools"], ["read_file", "bash", "new_tool"])

    def test_materialize_preserves_unchanged_agent_yaml_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trusted = self.make_recipe(root)
            agent = trusted / "agents" / "agent.yaml"
            agent.write_text("# Keep this comment\n" + agent.read_text())
            staged = root / "staged"
            staged.mkdir()
            (staged / "agents").mkdir()
            (staged / "package.json").write_bytes((trusted / "package.json").read_bytes())
            (staged / "agents" / "agent.yaml").write_bytes(agent.read_bytes())
            stage_agent_configs(staged)

            materialize_agent_configs(staged, trusted)

            self.assertEqual((staged / "agents" / "agent.yaml").read_bytes(), agent.read_bytes())

    def test_materialize_rejects_candidate_model_or_agent_selector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trusted = self.make_recipe(root)
            staged = root / "staged"
            staged.mkdir()
            (staged / "agents").mkdir()
            (staged / "package.json").write_bytes((trusted / "package.json").read_bytes())
            (staged / "agents" / "agent.yaml").write_text(
                "name: agent\nai:\n  model: openai/other\ntools: [bash]\n"
            )
            with self.assertRaisesRegex(ValueError, "ai.*not editable"):
                materialize_agent_configs(staged, trusted)

            (staged / "agents" / "agent.yaml").write_text("name: agent\ntools: [bash]\n")
            (staged / "package.json").write_text(
                json.dumps({"name": "legal-agent", "pi": {"agents": ["./other/*.yaml"]}})
            )
            with self.assertRaisesRegex(ValueError, "pi.agents"):
                materialize_agent_configs(staged, trusted)


if __name__ == "__main__":
    unittest.main()
