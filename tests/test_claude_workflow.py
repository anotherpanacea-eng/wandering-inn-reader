#!/usr/bin/env python3
"""Safety boundaries of the comment-triggered @claude workflow.

This job hands a write-capable App token and the Claude OAuth token to a
model acting on comment text, so a few properties must not drift in a later
edit: every action is pinned to an immutable commit, only owners, members,
and collaborators can start a run, fork PRs are refused before checkout,
and the workflow's own GITHUB_TOKEN carries no write scope.

  python3 tests/test_claude_workflow.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "claude.yml").read_text(encoding="utf-8")

TRUSTED = """contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'),"""


class ClaudeWorkflow(unittest.TestCase):

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        refs = re.findall(r"^\s*-?\s*uses:\s*(\S+)", WORKFLOW, re.MULTILINE)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"@[0-9a-f]{40}$", f"{ref} is not pinned to a commit SHA")

    def test_every_trigger_requires_a_trusted_author(self) -> None:
        events = re.findall(r"github\.event_name == '(\w+)'", WORKFLOW)
        self.assertEqual(
            sorted(events),
            ["issue_comment", "issues", "pull_request_review", "pull_request_review_comment"])
        self.assertEqual(WORKFLOW.count(TRUSTED), len(events))

    def test_fork_guard_runs_before_checkout(self) -> None:
        guard = WORKFLOW.find("isCrossRepository")
        checkout = WORKFLOW.find("uses: actions/checkout@")
        self.assertNotEqual(guard, -1)
        self.assertLess(guard, checkout)

    def test_workflow_token_has_no_write_scope_but_oidc(self) -> None:
        block = re.search(r"^    permissions:\n((?:      .*\n)+)", WORKFLOW, re.MULTILINE)
        self.assertIsNotNone(block)
        scopes = dict(re.findall(r"^\s+([\w-]+):\s*(\w+)", block.group(1), re.MULTILINE))
        writes = {k for k, v in scopes.items() if v == "write"}
        self.assertEqual(writes, {"id-token"})


if __name__ == "__main__":
    unittest.main()
