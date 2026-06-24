from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import TypedDict, cast


class ManifestInterface(TypedDict):
    longDescription: str


class PluginManifest(TypedDict):
    skills: str
    interface: ManifestInterface


class DiscordPluginPackagingTests(unittest.TestCase):
    def test_plugin_packages_workflow_skills(self) -> None:
        plugin_root = Path("plugins/codex-discord-harness")
        manifest = cast(
            PluginManifest,
            json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")),
        )
        skill_text = (plugin_root / "skills/deep-interview/SKILL.md").read_text(encoding="utf-8")
        auto_research = plugin_root / "skills/deep-interview/auto-research-greenfield.md"
        auto_answer = plugin_root / "skills/deep-interview/auto-answer-uncertain.md"
        notice = plugin_root / "skills/deep-interview/NOTICE.md"
        github_triage = plugin_root / "skills/github-project-triage/SKILL.md"
        github_triage_notice = plugin_root / "skills/github-project-triage/NOTICE.md"
        maintainer_orchestrator = plugin_root / "skills/maintainer-orchestrator/SKILL.md"
        maintainer_orchestrator_notice = plugin_root / "skills/maintainer-orchestrator/NOTICE.md"
        readme = Path("README.md").read_text(encoding="utf-8")
        root_notice = Path("NOTICE.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["skills"], "./skills/")
        self.assertIn("deep interview", manifest["interface"]["longDescription"])
        self.assertIn("GitHub triage", manifest["interface"]["longDescription"])
        self.assertIn("maintainer orchestration", manifest["interface"]["longDescription"])
        self.assertIn("name: deep-interview", skill_text)
        self.assertIn("\uc791\uc5c5 \uad6c\uc870", skill_text)
        self.assertIn("Phase 0: Resolve Ambiguity Threshold", skill_text)
        self.assertIn("Ontology Convergence", skill_text)
        self.assertIn("pending-approval ticket", skill_text)
        self.assertIn("Do not silently fill missing requirements", skill_text)
        self.assertTrue(auto_research.is_file())
        self.assertTrue(auto_answer.is_file())
        self.assertIn("MIT License", notice.read_text(encoding="utf-8"))
        self.assertIn("`deep-interview`", readme)
        self.assertIn('name: "github-project-triage"', github_triage.read_text(encoding="utf-8"))
        self.assertIn("name: maintainer-orchestrator", maintainer_orchestrator.read_text(encoding="utf-8"))
        self.assertIn("steipete/agent-scripts", github_triage_notice.read_text(encoding="utf-8"))
        self.assertIn("steipete/agent-scripts", maintainer_orchestrator_notice.read_text(encoding="utf-8"))
        self.assertIn("steipete/agent-scripts", root_notice)
        self.assertIn("`github-project-triage`", readme)
        self.assertIn("`maintainer-orchestrator`", readme)


if __name__ == "__main__":
    _ = unittest.main()
