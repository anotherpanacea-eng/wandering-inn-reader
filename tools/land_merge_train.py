#!/usr/bin/env python3
"""Prove and optionally CAS-land one reviewed merge train under a clean Git environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from tools import check_merge_train as train
except ModuleNotFoundError:  # Direct execution places tools/, not the repo root, on sys.path.
    import check_merge_train as train


REMOTE_URL = train.REMOTE_URL
MAIN_REF = "refs/heads/main"
APPROVAL_RE = re.compile(
    r"^- (generic|fleet-posture|CI): approved @ ([0-9a-f]{40}) \+ "
    r"([0-9a-f]{64}); (vp_open_prs_generic|vp_open_prs_posture|vp_ci_adaptation)$",
)
APPROVAL_ACTORS = {
    "generic": "vp_open_prs_generic",
    "fleet-posture": "vp_open_prs_posture",
    "CI": "vp_ci_adaptation",
}


class LandingError(ValueError):
    """The live train evidence is incomplete or changed."""


def _invoke(command: Sequence[str], repo: Path,
            extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = train._environment()
    if extra_env:
        if any(key.upper().startswith("GIT_") for key in extra_env):
            raise LandingError("callers cannot restore ambient Git environment state")
        env.update(extra_env)
    return subprocess.run(
        list(command), cwd=repo.resolve(), capture_output=True, text=True,
        encoding="utf-8", errors="strict", check=False, env=env,
    )


def _command(repo: Path, *command: str,
             extra_env: dict[str, str] | None = None) -> str:
    result = _invoke(command, repo, extra_env)
    if result.returncode:
        raise LandingError(f"{' '.join(command[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def _git(repo: Path, *args: str) -> str:
    return _command(
        repo, "git", "--no-replace-objects", "-c", "credential.helper=",
        "-c", "credential.https://github.com.helper=!gh auth git-credential",
        "-c", f"core.hooksPath={os.devnull}",
        "-C", str(repo.resolve()), *args,
    )


def _canonical_train_ref(repo: Path, value: str) -> str:
    if not train.TRAIN_RE.fullmatch(value):
        raise LandingError("train_ref must be a bounded train/* branch")
    checked = _git(repo, "check-ref-format", "--branch", value).strip()
    if checked != value:
        raise LandingError("train_ref is not canonical")
    return value


def _fetch_live(repo: Path, inventory: dict[str, Any], train_pr: int,
                train_ref: str, remote_url: str = REMOTE_URL) -> str:
    refspecs = [f"+{MAIN_REF}:{train.BASE_REF}"]
    seen = {MAIN_REF}
    for row in [*inventory["included"], *inventory["excluded"]]:
        source = f"refs/heads/{row['head_ref']}"
        if source not in seen:
            refspecs.append(f"+{source}:refs/remotes/origin/{row['head_ref']}")
            seen.add(source)
    refspecs.extend([
        f"+refs/heads/{train_ref}:refs/remotes/origin/{train_ref}",
        f"+refs/pull/{train_pr}/merge:refs/codex/merge-train/{train_pr}",
    ])
    return _git(repo, "fetch", "--atomic", "--no-tags", remote_url, *refspecs)


def _open_prs(repo: Path) -> list[dict[str, Any]]:
    raw = _command(
        repo, "gh", "api", "--hostname", "github.com", "--method", "GET", "--paginate", "--slurp",
        f"repos/{train.REPOSITORY}/pulls", "-f", "state=open", "-f", "per_page=100",
    )
    pages = json.loads(raw)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise LandingError("GitHub open-PR query returned malformed pagination")
    return [row for page in pages for row in page]


def _close_pr(repo: Path, number: int) -> None:
    _command(repo, "gh", "pr", "close", str(number), "--repo", train.REPOSITORY)


def _pr_identity(row: dict[str, Any]) -> tuple[int, str, str, str]:
    try:
        return (
            int(row["number"]), str(row["head"]["repo"]["full_name"]).lower(),
            str(row["head"]["ref"]), str(row["head"]["sha"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LandingError("GitHub returned a malformed PR identity") from exc


def _prove_open_inventory(inventory: dict[str, Any], rows: list[dict[str, Any]],
                          train_pr: int, train_ref: str) -> dict[str, Any]:
    expected = {
        (row["pr"], row["head_repo"].lower(), row["head_ref"], row["head"])
        for row in [*inventory["included"], *inventory["excluded"]]
    }
    actual: list[tuple[int, str, str, str]] = []
    seen_numbers: set[int] = set()
    seen_identities: set[tuple[int, str, str, str]] = set()
    train_seen = 0
    train_row: dict[str, Any] | None = None
    for row in rows:
        identity = _pr_identity(row)
        if identity[0] in seen_numbers or identity in seen_identities:
            raise LandingError("GitHub returned a duplicate live PR identity")
        seen_numbers.add(identity[0]); seen_identities.add(identity)
        if row.get("base", {}).get("ref") != "main" or row.get("draft") is not True:
            raise LandingError("every inventoried PR and the train must be a draft targeting main")
        if identity[0] == train_pr:
            train_seen += 1
            train_row = row
            if identity != (train_pr, train.REPOSITORY.lower(), train_ref, inventory["head"]):
                raise LandingError("the train PR identity or head moved")
        else:
            actual.append(identity)
    if train_seen != 1 or len(actual) != len(expected) or set(actual) != expected:
        raise LandingError("live non-train open PRs do not equal the closed inventory")
    assert train_row is not None
    return train_row


def _prove_approvals(inventory: dict[str, Any], train_row: dict[str, Any]) -> None:
    digest = train.inventory_digest(inventory)
    body = str(train_row.get("body", ""))
    if "```" in body or any(line.startswith(">") for line in body.splitlines()):
        raise LandingError("train PR approval evidence cannot contain code or quote decoys")
    marker = "## Train review approvals"
    if body.count(marker) != 1:
        raise LandingError("train PR must contain one dedicated review approval section")
    section = body.split(marker, 1)[1]
    if "\n## " in section:
        section = section.split("\n## ", 1)[0]
    lines = [line for line in section.strip().splitlines() if line.strip()]
    approvals = [match.groups() for line in lines if (match := APPROVAL_RE.fullmatch(line))]
    if len(approvals) != 3 or {row[0] for row in approvals} != {"generic", "fleet-posture", "CI"}:
        raise LandingError("train PR must contain exactly three canonical review approvals")
    if len(lines) != 3:
        raise LandingError("the dedicated review approval section contains noncanonical content")
    if any(head != inventory["head"] or claimed != digest or APPROVAL_ACTORS[lane] != actor
           for lane, head, claimed, actor in approvals):
        raise LandingError("train reviews do not bind this exact head and inventory digest")


def _prove_synthetic(repo: Path, inventory: dict[str, Any], train_pr: int) -> str:
    synthetic_ref = f"refs/codex/merge-train/{train_pr}"
    synthetic = train._resolve(repo, synthetic_ref, "synthetic merge")
    parents = train._parents(repo, synthetic)
    if parents != [inventory["base"], inventory["head"]]:
        raise LandingError("GitHub synthetic merge has wrong exact parents")
    expected_tree = train._automatic_tree(repo, inventory["base"], inventory["head"])
    if train._tree(repo, synthetic) != expected_tree:
        raise LandingError("GitHub synthetic merge tree is not the exact clean merge tree")
    return synthetic


def _bash_executable() -> str:
    git_path = shutil.which("git")
    if os.name == "nt" and git_path:
        candidate = Path(git_path).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    bash = shutil.which("bash")
    if not bash or (os.name == "nt" and Path(bash).name.lower() == "bash.exe"
                    and "windows\\system32" in str(Path(bash).resolve()).lower()):
        raise LandingError("Git Bash is required on Windows; a WSL bash shim is not accepted")
    return bash


def _run_gate(repo: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="wir-train-pycache-") as cache:
        _command(repo, _bash_executable(), "./check.sh", extra_env={
            "PYTHON": Path(sys.executable).as_posix(),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": cache,
        })


def _delete_unchanged_branches(repo: Path, branches: list[tuple[str, str]],
                               remote_url: str) -> list[str]:
    deletions: list[tuple[str, str]] = []
    for branch, expected in branches:
        remote_ref = f"refs/heads/{branch}"
        fields = _git(repo, "ls-remote", "--heads", remote_url, remote_ref).split()
        if not fields:
            continue
        if len(fields) != 2 or fields != [expected, remote_ref]:
            raise LandingError(f"remote branch {branch} moved before disposal")
        deletions.append((remote_ref, expected))
    if deletions:
        args = ["push", "--atomic"]
        for remote_ref, expected in deletions:
            args.append(f"--force-with-lease={remote_ref}:{expected}")
        args.extend([remote_url, *[f":{remote_ref}" for remote_ref, _ in deletions]])
        _git(repo, *args)
    for remote_ref, _ in deletions:
        if _git(repo, "ls-remote", "--heads", remote_url, remote_ref).strip():
            raise LandingError(f"remote branch {remote_ref} survived exact-lease disposal")
    return [remote_ref.removeprefix("refs/heads/") for remote_ref, _ in deletions]


def _post_land(
    repo: Path, inventory: dict[str, Any], train_pr: int, train_ref: str,
    synthetic: str, remote_url: str,
    open_prs: Callable[[Path], list[dict[str, Any]]],
    close_pr: Callable[[Path, int], None],
) -> dict[str, Any]:
    _git(repo, "fetch", "--no-tags", remote_url, f"+{MAIN_REF}:{train.BASE_REF}")
    if train._resolve(repo, train.BASE_REF, "landed main") != synthetic:
        raise LandingError("main did not advance to the exact synthetic merge")
    for row in inventory["included"]:
        contained = train._run(repo, "merge-base", "--is-ancestor", row["head"], synthetic)
        if contained.returncode:
            raise LandingError(f"landed main does not contain included PR #{row['pr']}")

    rows = open_prs(repo)
    open_numbers = {_pr_identity(row)[0] for row in rows}
    close_numbers = [row["pr"] for row in inventory["included"]] + [train_pr]
    closed: list[int] = []
    for number in close_numbers:
        if number in open_numbers:
            close_pr(repo, number)
            closed.append(number)
    remaining = {_pr_identity(row) for row in open_prs(repo)}
    expected = {
        (row["pr"], row["head_repo"].lower(), row["head_ref"], row["head"])
        for row in inventory["excluded"]
    }
    if remaining != expected:
        raise LandingError("post-land open PRs do not equal the declared exclusions")
    deleted = _delete_unchanged_branches(
        repo,
        [(row["head_ref"], row["head"]) for row in inventory["included"]]
        + [(train_ref, inventory["head"])],
        remote_url,
    )
    return {"closed_prs": closed, "deleted_branches": deleted}


def land(
    repo: Path, inventory_path: Path, train_pr: int, train_ref: str,
    push: bool = False, *, remote_url: str = REMOTE_URL,
    open_prs: Callable[[Path], list[dict[str, Any]]] = _open_prs,
    close_pr: Callable[[Path, int], None] = _close_pr,
) -> dict[str, Any]:
    repo = repo.resolve()
    inventory = train.load_inventory(inventory_path.resolve())
    train.validate_inventory_shape(repo, inventory)
    train_ref = _canonical_train_ref(repo, train_ref)
    _fetch_live(repo, inventory, train_pr, train_ref, remote_url)
    train_row = _prove_open_inventory(inventory, open_prs(repo), train_pr, train_ref)
    _prove_approvals(inventory, train_row)
    receipt = train.verify_train(repo, inventory)
    if train._resolve(repo, f"refs/remotes/origin/{train_ref}", "remote train") != inventory["head"]:
        raise LandingError("remote train branch moved")
    _run_gate(repo)

    # Refresh every mutable remote fact after the potentially long local gate.
    _fetch_live(repo, inventory, train_pr, train_ref, remote_url)
    train_row = _prove_open_inventory(inventory, open_prs(repo), train_pr, train_ref)
    _prove_approvals(inventory, train_row)
    receipt = train.verify_train(repo, inventory)
    synthetic = _prove_synthetic(repo, inventory, train_pr)
    receipt["synthetic_merge"] = synthetic
    if push:
        _git(
            repo, "push", f"--force-with-lease={MAIN_REF}:{inventory['base']}",
            remote_url, f"{synthetic}:{MAIN_REF}",
        )
        receipt["landed"] = True
        receipt.update(_post_land(
            repo, inventory, train_pr, train_ref, synthetic, remote_url, open_prs, close_pr,
        ))
    else:
        receipt["landed"] = False
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--train-pr", type=int, required=True)
    parser.add_argument("--train-ref", required=True)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = land(args.repo, args.inventory, args.train_pr, args.train_ref, args.push)
    except (LandingError, train.TrainError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"merge-train landing: REFUSED: {exc}")
        return 1
    print("merge-train landing: " + json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
