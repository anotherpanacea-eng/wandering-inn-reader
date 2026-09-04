#!/usr/bin/env python3
"""Adversarial tests for the dependency-free merge-train verifier."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import check_merge_train as policy
from tools import land_merge_train as landing


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class MergeTrainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        git(self.repo, "config", "remote.origin.url", policy.REMOTE_URL)
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", policy.BASE_REF, self.base)

        git(self.repo, "switch", "-c", "feat/one")
        (self.repo / "one.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "one")
        self.one = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/one", self.one)

        git(self.repo, "switch", "-c", "feat/two", self.base)
        (self.repo / "two.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "two")
        self.two = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/two", self.two)

        git(self.repo, "switch", "-c", "train/test", self.base)
        git(self.repo, "merge", "--no-ff", "--no-edit", self.one)
        self.merge_one = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "merge", "--no-ff", "--no-edit", self.two)
        self.merge_two = git(self.repo, "rev-parse", "HEAD")
        self.inventory = {
            "schema": policy.SCHEMA,
            "repository": policy.REPOSITORY,
            "base_ref": policy.BASE_REF,
            "base": self.base,
            "head": self.merge_two,
            "included": [
                {"pr": 1, "head_repo": policy.REPOSITORY, "head_ref": "feat/one",
                 "head": self.one, "merge": self.merge_one},
                {"pr": 2, "head_repo": policy.REPOSITORY, "head_ref": "feat/two",
                 "head": self.two, "merge": self.merge_two},
            ],
            "excluded": [],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_clean_train_passes(self) -> None:
        receipt = policy.verify_train(self.repo, self.inventory)
        self.assertEqual(len(receipt["included"]), 2)
        self.assertEqual(receipt["head"], self.merge_two)
        self.assertRegex(receipt["inventory_sha256"], r"^[0-9a-f]{64}$")

    def test_reordered_inventory_refuses(self) -> None:
        self.inventory["included"].reverse()
        with self.assertRaisesRegex(policy.TrainError, "first-parent position"):
            policy.verify_train(self.repo, self.inventory)

    def test_moved_base_ref_refuses(self) -> None:
        git(self.repo, "update-ref", policy.BASE_REF, self.one)
        with self.assertRaisesRegex(policy.TrainError, "moved"):
            policy.verify_train(self.repo, self.inventory)

    def test_extra_train_commit_refuses(self) -> None:
        (self.repo / "extra.txt").write_text("extra\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "uninventoried")
        self.inventory["head"] = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(policy.TrainError, "first-parent position"):
            policy.verify_train(self.repo, self.inventory)

    def test_duplicate_and_fork_identities_refuse(self) -> None:
        self.inventory["excluded"] = [{
            "pr": 1, "head_repo": policy.REPOSITORY, "head_ref": "feat/one",
            "head": self.one, "reason": "later",
        }]
        with self.assertRaisesRegex(policy.TrainError, "globally unique"):
            policy.verify_train(self.repo, self.inventory)
        self.inventory["excluded"][0].update({
            "pr": 3, "head_repo": "someone/fork", "head_ref": "fork/one",
            "head": "1" * 40,
        })
        with self.assertRaisesRegex(policy.TrainError, "this repository"):
            policy.verify_train(self.repo, self.inventory)

    def test_dirty_worktree_refuses(self) -> None:
        (self.repo / "untracked.txt").write_text("no\n", encoding="utf-8")
        with self.assertRaisesRegex(policy.TrainError, "untracked"):
            policy.verify_train(self.repo, self.inventory)

    def test_hidden_index_and_checkout_config_refuse(self) -> None:
        git(self.repo, "update-index", "--assume-unchanged", "base.txt")
        with self.assertRaisesRegex(policy.TrainError, "index flags"):
            policy.verify_train(self.repo, self.inventory)
        git(self.repo, "update-index", "--no-assume-unchanged", "base.txt")
        git(self.repo, "config", "merge.hostile.driver", "false")
        with self.assertRaisesRegex(policy.TrainError, "local Git config"):
            policy.verify_train(self.repo, self.inventory)
        git(self.repo, "config", "--unset", "merge.hostile.driver")
        git(self.repo, "config", "url.https://evil.invalid/.insteadOf", "https://github.com/")
        with self.assertRaisesRegex(policy.TrainError, "local Git config"):
            policy.verify_train(self.repo, self.inventory)

    def test_excluded_head_and_branch_must_be_real_and_bound(self) -> None:
        excluded = {
            "pr": 3, "head_repo": policy.REPOSITORY, "head_ref": "feat/later",
            "head": "f" * 40, "reason": "later batch",
        }
        self.inventory["excluded"] = [excluded]
        with self.assertRaisesRegex(policy.TrainError, "rev-parse"):
            policy.verify_train(self.repo, self.inventory)

        excluded.update({"head_ref": "not/a/real/ref/", "head": self.one})
        with self.assertRaisesRegex(policy.TrainError, "constituent branch"):
            policy.verify_train(self.repo, self.inventory)

        git(self.repo, "switch", "-c", "feat/later", self.base)
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "later")
        later = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "train/test")
        excluded["head_ref"] = "feat/later"
        git(self.repo, "update-ref", "refs/remotes/origin/feat/later", self.two)
        excluded["head"] = later
        with self.assertRaisesRegex(policy.TrainError, "moved"):
            policy.verify_train(self.repo, self.inventory)

        git(self.repo, "update-ref", "refs/remotes/origin/feat/later", later)
        receipt = policy.verify_train(self.repo, self.inventory)
        self.assertEqual(len(receipt["excluded"]), 1)

        prior_digest = receipt["inventory_sha256"]
        excluded["reason"] = "next periodic batch"
        changed = policy.verify_train(self.repo, self.inventory)
        self.assertNotEqual(changed["inventory_sha256"], prior_digest)

    def test_excluded_head_reachable_from_train_refuses(self) -> None:
        self.inventory["included"] = [self.inventory["included"][1]]
        self.inventory["excluded"] = [{
            "pr": 1, "head_repo": policy.REPOSITORY, "head_ref": "feat/one",
            "head": self.one, "reason": "not admitted",
        }]
        self.inventory["base"] = self.merge_one
        git(self.repo, "update-ref", policy.BASE_REF, self.merge_one)
        with self.assertRaisesRegex(policy.TrainError, "excluded PR head is reachable"):
            policy.verify_train(self.repo, self.inventory)

    def test_conflict_or_tampered_tree_refuses(self) -> None:
        with mock.patch.object(policy, "_automatic_tree", return_value="f" * 40):
            with self.assertRaisesRegex(policy.TrainError, "differs"):
                policy.verify_train(self.repo, self.inventory)

    def test_strict_json_rejects_duplicate_members(self) -> None:
        path = self.repo / "inventory.json"
        path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
        with self.assertRaisesRegex(policy.TrainError, "duplicate JSON"):
            policy.load_inventory(path)

    def test_replace_ref_refuses(self) -> None:
        git(self.repo, "replace", self.one, self.two)
        with self.assertRaisesRegex(policy.TrainError, "replacement"):
            policy.verify_train(self.repo, self.inventory)

    def test_landing_has_one_scrubbed_subprocess_gateway(self) -> None:
        source = inspect.getsource(landing)
        self.assertEqual(source.count("subprocess.run("), 1)
        poisoned = {
            "GIT_DIR": "wrong", "GIT_WORK_TREE": "wrong",
            "GIT_OBJECT_DIRECTORY": "wrong", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "wrong",
            "GIT_NAMESPACE": "wrong", "GIT_INDEX_FILE": "wrong",
            "GIT_CONFIG_GLOBAL": "wrong", "GIT_CONFIG_SYSTEM": "wrong",
            "GIT_CONFIG_COUNT": "9", "BASH_ENV": "wrong", "PYTHON": "wrong",
            "PYTHONPATH": "wrong", "NODE_OPTIONS": "wrong", "GH_HOST": "wrong",
            "HTTPS_PROXY": "wrong",
        }
        with mock.patch.dict(os.environ, poisoned):
            clean = policy._environment()
            with mock.patch.object(
                landing.subprocess, "run",
                return_value=subprocess.CompletedProcess(["git"], 0, "", ""),
            ) as invoked:
                landing._invoke(["git", "status"], self.repo)
                child_env = invoked.call_args.kwargs["env"]
        for key in poisoned:
            if key == "GIT_CONFIG_GLOBAL":
                self.assertEqual(clean[key], os.devnull)
            else:
                self.assertNotEqual(clean.get(key), "wrong")
            self.assertNotEqual(child_env.get(key), "wrong")
        self.assertEqual(clean["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(clean["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_train_approvals_bind_head_and_inventory_digest(self) -> None:
        digest = policy.inventory_digest(self.inventory)
        lines = [
            f"- generic: approved @ {self.merge_two} + {digest}; vp_open_prs_generic",
            f"- fleet-posture: approved @ {self.merge_two} + {digest}; vp_open_prs_posture",
            f"- CI: approved @ {self.merge_two} + {digest}; vp_ci_adaptation",
        ]
        body = "\n".join([
            "## Train review approvals",
            *lines,
        ])
        landing._prove_approvals(self.inventory, {"body": body})
        self.inventory["excluded"] = [{
            "pr": 3, "head_repo": policy.REPOSITORY, "head_ref": "feat/later",
            "head": self.one, "reason": "later",
        }]
        with self.assertRaisesRegex(landing.LandingError, "do not bind"):
            landing._prove_approvals(self.inventory, {"body": body})

        fenced = "```md\n## Train review approvals\n" + "\n".join(lines) + "\n```"
        with self.assertRaisesRegex(landing.LandingError, "decoys"):
            landing._prove_approvals(self.inventory, {"body": fenced})

        same_actor = body.replace("vp_open_prs_posture", "vp_open_prs_generic")
        with self.assertRaisesRegex(landing.LandingError, "do not bind"):
            landing._prove_approvals(self.inventory, {"body": same_actor})

    def test_landing_gate_pins_git_bash_and_current_python(self) -> None:
        with mock.patch.object(landing, "_command") as command:
            landing._run_gate(self.repo)
        args = command.call_args.args
        env = command.call_args.kwargs["extra_env"]
        self.assertEqual(Path(args[1]).name.lower(), "bash.exe" if os.name == "nt" else "bash")
        if os.name == "nt":
            self.assertNotIn("windows\\system32", str(Path(args[1]).resolve()).lower())
        self.assertEqual(env["PYTHON"], Path(sys.executable).as_posix())

    def test_landing_rejects_malformed_inventory_before_fetch(self) -> None:
        inventory_path = self.repo / "bad-inventory.json"
        inventory_path.write_text("{}", encoding="utf-8")
        with mock.patch.object(landing, "_fetch_live") as fetch:
            with self.assertRaisesRegex(policy.TrainError, "inventory keys"):
                landing.land(self.repo, inventory_path, 99, "train/test")
            fetch.assert_not_called()

        malformed = dict(self.inventory)
        malformed["included"] = [dict(self.inventory["included"][0], head_ref="bad/ref/")]
        with self.assertRaisesRegex(policy.TrainError, "constituent branch"):
            policy.validate_inventory_shape(self.repo, malformed)

    def test_live_inventory_requires_unique_main_targeting_drafts(self) -> None:
        def live(number: int, repo: str, ref: str, head: str) -> dict[str, object]:
            return {
                "number": number, "draft": True, "base": {"ref": "main"}, "body": "",
                "head": {"repo": {"full_name": repo}, "ref": ref, "sha": head},
            }

        rows = [
            live(row["pr"], row["head_repo"], row["head_ref"], row["head"])
            for row in self.inventory["included"]
        ]
        rows.append(live(99, policy.REPOSITORY, "train/test", self.merge_two))
        landing._prove_open_inventory(self.inventory, rows, 99, "train/test")

        for index, field, value in ((2, "draft", False), (0, "draft", False)):
            changed = json.loads(json.dumps(rows))
            changed[index][field] = value
            with self.assertRaisesRegex(landing.LandingError, "draft targeting main"):
                landing._prove_open_inventory(self.inventory, changed, 99, "train/test")
        changed = json.loads(json.dumps(rows))
        changed[1]["base"]["ref"] = "other"
        with self.assertRaisesRegex(landing.LandingError, "draft targeting main"):
            landing._prove_open_inventory(self.inventory, changed, 99, "train/test")
        with self.assertRaisesRegex(landing.LandingError, "duplicate"):
            landing._prove_open_inventory(self.inventory, [*rows, dict(rows[0])], 99, "train/test")

    def test_local_bare_end_to_end_landing_and_disposal(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            work = root / "work"
            remote = root / "remote.git"
            work.mkdir()
            git(work, "init", "-b", "main")
            git(work, "config", "user.name", "Test")
            git(work, "config", "user.email", "test@example.invalid")
            git(work, "config", "core.autocrlf", "false")
            (work / "check.sh").write_text(
                '#!/bin/sh\n[ "$PYTHON" != "true" ]\n"$PYTHON" -c "print(\'gate ok\')"\n',
                encoding="utf-8",
            )
            (work / "base.txt").write_text("base\n", encoding="utf-8")
            git(work, "add", ".")
            git(work, "commit", "-m", "base")
            base = git(work, "rev-parse", "HEAD")

            git(work, "switch", "-c", "feat/one")
            (work / "one.txt").write_text("one\n", encoding="utf-8")
            git(work, "add", ".")
            git(work, "commit", "-m", "one")
            one = git(work, "rev-parse", "HEAD")

            git(work, "switch", "-c", "train/test", base)
            git(work, "merge", "--no-ff", "--no-edit", one)
            train_head = git(work, "rev-parse", "HEAD")
            git(work, "switch", "-c", "synthetic", base)
            git(work, "merge", "--no-ff", "--no-edit", train_head)
            synthetic = git(work, "rev-parse", "HEAD")
            git(work, "switch", "train/test")

            result = subprocess.run(
                ["git", "clone", "--bare", str(work), str(remote)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def bare(*args: str) -> str:
                result = subprocess.run(
                    ["git", "--git-dir", str(remote), *args], capture_output=True,
                    text=True, encoding="utf-8", check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            bare("update-ref", "refs/pull/99/merge", synthetic)
            bare("symbolic-ref", "HEAD", "refs/heads/main")
            remote_url = str(remote.resolve())
            git(work, "config", "remote.origin.url", remote_url)
            inventory = {
                "schema": policy.SCHEMA, "repository": policy.REPOSITORY,
                "base_ref": policy.BASE_REF, "base": base, "head": train_head,
                "included": [{
                    "pr": 1, "head_repo": policy.REPOSITORY, "head_ref": "feat/one",
                    "head": one, "merge": train_head,
                }],
                "excluded": [],
            }
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            digest = policy.inventory_digest(inventory)
            approval_body = "\n".join([
                "## Train review approvals",
                f"- generic: approved @ {train_head} + {digest}; vp_open_prs_generic",
                f"- fleet-posture: approved @ {train_head} + {digest}; vp_open_prs_posture",
                f"- CI: approved @ {train_head} + {digest}; vp_ci_adaptation",
            ])

            def live(number: int, ref: str, head: str, body: str = "") -> dict[str, object]:
                return {
                    "number": number, "draft": True, "base": {"ref": "main"}, "body": body,
                    "head": {"repo": {"full_name": policy.REPOSITORY}, "ref": ref, "sha": head},
                }

            rows = [live(1, "feat/one", one), live(99, "train/test", train_head, approval_body)]
            closed: set[int] = set()

            def open_prs(_repo: Path) -> list[dict[str, Any]]:
                return [row for row in rows if int(row["number"]) not in closed]

            def close_pr(_repo: Path, number: int) -> None:
                closed.add(number)

            with mock.patch.object(policy, "REMOTE_URL", remote_url):
                receipt = landing.land(
                    work, inventory_path, 99, "train/test", True,
                    remote_url=remote_url, open_prs=open_prs, close_pr=close_pr,
                )
            self.assertTrue(receipt["landed"])
            self.assertEqual(set(receipt["closed_prs"]), {1, 99})
            self.assertEqual(set(receipt["deleted_branches"]), {"feat/one", "train/test"})
            self.assertEqual(bare("rev-parse", "refs/heads/main"), synthetic)
            self.assertFalse(bare("for-each-ref", "--format=%(refname)", "refs/heads/feat/one"))
            self.assertFalse(bare("for-each-ref", "--format=%(refname)", "refs/heads/train/test"))


if __name__ == "__main__":
    unittest.main()
