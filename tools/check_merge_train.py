#!/usr/bin/env python3
"""Fail closed unless HEAD is the exact clean merge train in its closed inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence


SCHEMA = "wandering-inn-reader-merge-train/1"
REPOSITORY = "anotherpanacea-eng/wandering-inn-reader"
REMOTE_URL = "https://github.com/anotherpanacea-eng/wandering-inn-reader.git"
BASE_REF = "refs/remotes/origin/main"
OID_RE = re.compile(r"[0-9a-f]{40}\Z")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
TRAIN_RE = re.compile(r"train/[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")


class TrainError(ValueError):
    """The proposed train does not satisfy its closed inventory."""


def _environment() -> dict[str, str]:
    blocked = {
        "ALL_PROXY", "BASHOPTS", "BASH_ENV", "CDPATH", "CURL_CA_BUNDLE", "ENV",
        "GH_CONFIG_DIR", "GH_HOST", "GH_REPO", "HTTP_PROXY", "HTTPS_PROXY", "NODE_OPTIONS",
        "NODE_PATH", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SHELLOPTS", "SSL_CERT_DIR",
        "SSL_CERT_FILE",
    }
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("GIT_", "PYTHON")) and key.upper() not in blocked
    }
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_PROMPT_DISABLED": "1",
    })
    return env


def _run(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo.resolve()), *args],
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="strict" if text else None,
        check=False,
        env=_environment(),
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(repo, *args)
    if result.returncode:
        raise TrainError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TrainError(f"duplicate JSON member {key!r}")
        value[key] = item
    return value


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrainError(f"cannot read strict inventory JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TrainError("inventory root must be an object")
    return value


def inventory_digest(inventory: dict[str, Any]) -> str:
    canonical = json.dumps(
        inventory, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _exact_keys(value: object, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise TrainError(f"{where} keys must be exactly {sorted(keys)}; got {actual}")
    return value


def _oid(value: object, where: str) -> str:
    if not isinstance(value, str) or not OID_RE.fullmatch(value) or set(value) == {"0"}:
        raise TrainError(f"{where} must be canonical nonzero lowercase 40-hex")
    return value


def _positive(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise TrainError(f"{where} must be a bounded positive integer")
    return value


def _printable(value: object, where: str, maximum: int = 200) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or any(ord(char) < 0x20 or ord(char) > 0x7e for char in value)):
        raise TrainError(f"{where} must be 1-{maximum} printable ASCII characters")
    return value


def _resolve(repo: Path, value: str, where: str) -> str:
    if value.startswith("-") or any(char in value for char in "\0\r\n"):
        raise TrainError(f"unsafe {where}")
    return _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").strip()


def _parents(repo: Path, commit: str) -> list[str]:
    fields = _git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    if not fields or fields[0] != commit:
        raise TrainError(f"cannot inspect exact commit {commit}")
    return fields[1:]


def _tree(repo: Path, commit: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{commit}^{{tree}}").strip()


def _automatic_tree(repo: Path, left: str, right: str) -> str:
    result = _run(repo, "merge-tree", "--write-tree", "--name-only", "-z", "--messages",
                  left, right, text=False)
    if result.returncode != 0:
        raise TrainError("constituent does not merge cleanly; repair it outside the train")
    fields = result.stdout.split(b"\0")
    try:
        tree = fields[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as exc:
        raise TrainError("git merge-tree emitted a malformed tree") from exc
    if not OID_RE.fullmatch(tree):
        raise TrainError("git merge-tree omitted its exact tree")
    if len(fields) < 2 or fields[1] != b"":
        raise TrainError("clean git merge-tree unexpectedly reported conflict paths")
    return tree


def _refuse_ambiguous_objects(repo: Path) -> None:
    if _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace").strip():
        raise TrainError("replacement refs are forbidden")
    for relative in ("info/grafts", "objects/info/alternates"):
        target = Path(_git(repo, "rev-parse", "--git-path", relative).strip())
        if not target.is_absolute():
            target = repo / target
        if target.exists():
            raise TrainError(f"{relative} is forbidden")
    config = _run(repo, "config", "--local", "--get-regexp",
                  r"^(extensions\.partialClone|remote\..*\.promisor|core\.alternateRefsCommand)$")
    if config.returncode == 0 and config.stdout.strip():
        raise TrainError("promisor or alternate-object configuration is forbidden")
    if config.returncode not in (0, 1):
        raise TrainError("could not inspect object configuration")
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise TrainError("a full non-shallow repository is required")


def _refuse_hidden_checkout_state(repo: Path) -> None:
    origin_urls = _git(repo, "config", "--local", "--get-all", "remote.origin.url").splitlines()
    if origin_urls != [REMOTE_URL]:
        raise TrainError("origin must be the one canonical repository URL")
    unsafe_config = _run(
        repo, "config", "--local", "--get-regexp",
        r"^(include\.|includeif\.|core\.(attributesfile|excludesfile|sparsecheckout|sparsecheckoutcone|worktree)$|extensions\.worktreeconfig$|merge\..*\.driver$|merge\.(default|renormalize)$|url\..*\.(insteadof|pushinsteadof)$|https?\.|remote\..*\.proxy$)",
    )
    if unsafe_config.returncode == 0 and unsafe_config.stdout.strip():
        raise TrainError("local Git config can alter checkout, attributes, or merge behavior")
    if unsafe_config.returncode not in (0, 1):
        raise TrainError("could not inspect checkout-affecting local Git config")
    attributes = Path(_git(repo, "rev-parse", "--git-path", "info/attributes").strip())
    if not attributes.is_absolute():
        attributes = repo / attributes
    if attributes.exists() and attributes.stat().st_size:
        raise TrainError("untracked info/attributes is forbidden")

    flags = _git(repo, "ls-files", "-v", "-z").split("\0")
    if any(row and not row.startswith("H ") for row in flags):
        raise TrainError("assume-unchanged, skip-worktree, or nonstandard index flags are forbidden")
    stages = _git(repo, "ls-files", "--stage", "-z").split("\0")
    if any(row and (" 0\t" not in row or row.startswith("160000 ")) for row in stages):
        raise TrainError("unmerged entries and submodules are forbidden")
    if _run(repo, "diff-files", "--no-ext-diff", "--quiet", "--").returncode:
        raise TrainError("tracked worktree bytes differ from the index")
    if _run(repo, "diff-index", "--cached", "--no-ext-diff", "--quiet", "HEAD", "--").returncode:
        raise TrainError("index tree differs from HEAD")
    if _git(repo, "write-tree").strip() != _tree(repo, _resolve(repo, "HEAD", "HEAD")):
        raise TrainError("index tree is not the exact HEAD tree")
    ignored = _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if ignored or untracked:
        raise TrainError("all untracked and ignored worktree files are forbidden")


def _identity(repo: Path, row: dict[str, Any], where: str,
              excluded: bool) -> tuple[int, str, str, str]:
    identity = _shape_identity(repo, row, where, excluded)
    head_ref = row["head_ref"]
    head = identity[3]
    if _resolve(repo, head, f"{where}.head") != head:
        raise TrainError(f"{where}.head does not resolve exactly")
    remote_ref = f"refs/remotes/origin/{head_ref}"
    if _resolve(repo, remote_ref, f"{where}.head_ref") != head:
        raise TrainError(f"{where}.head_ref moved or does not name its exact head")
    return identity


def _shape_identity(repo: Path, row: dict[str, Any], where: str,
                    excluded: bool) -> tuple[int, str, str, str]:
    keys = {"pr", "head_repo", "head_ref", "head", "reason" if excluded else "merge"}
    _exact_keys(row, keys, where)
    pr = _positive(row["pr"], f"{where}.pr")
    head_repo = _printable(row["head_repo"], f"{where}.head_repo")
    if not REPO_RE.fullmatch(head_repo) or head_repo.lower() != REPOSITORY.lower():
        raise TrainError(f"{where}.head_repo must be this repository")
    head_ref = _printable(row["head_ref"], f"{where}.head_ref")
    branch_check = _run(repo, "check-ref-format", "--branch", head_ref)
    if (branch_check.returncode or branch_check.stdout.strip() != head_ref
            or head_ref.startswith(("train/", "refs/"))):
        raise TrainError(f"{where}.head_ref is not a bounded constituent branch")
    head = _oid(row["head"], f"{where}.head")
    if excluded:
        _printable(row["reason"], f"{where}.reason")
    else:
        _oid(row["merge"], f"{where}.merge")
    return pr, head_repo.lower(), head_ref.lower(), head


def validate_inventory_shape(repo: Path, inventory: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    _exact_keys(inventory, {"schema", "repository", "base_ref", "base", "head",
                            "included", "excluded"}, "inventory")
    if inventory["schema"] != SCHEMA or inventory["repository"] != REPOSITORY:
        raise TrainError("inventory schema or repository is wrong")
    if inventory["base_ref"] != BASE_REF:
        raise TrainError(f"base_ref must be exactly {BASE_REF}")
    _oid(inventory["base"], "base")
    _oid(inventory["head"], "head")
    included = inventory["included"]
    excluded = inventory["excluded"]
    if not isinstance(included, list) or not included or not isinstance(excluded, list):
        raise TrainError("included must be nonempty and excluded must be an array")
    identities: set[tuple[int, str, str, str]] = set()
    numbers: set[int] = set()
    heads: set[str] = set()
    for label, rows, is_excluded in (("included", included, False), ("excluded", excluded, True)):
        for index, raw in enumerate(rows):
            row = _exact_keys(raw, {"pr", "head_repo", "head_ref", "head",
                                    "reason" if is_excluded else "merge"}, f"{label}[{index}]")
            identity = _shape_identity(repo, row, f"{label}[{index}]", is_excluded)
            if identity in identities or identity[0] in numbers or identity[3] in heads:
                raise TrainError("PR numbers, branch identities, and heads must be globally unique")
            identities.add(identity); numbers.add(identity[0]); heads.add(identity[3])
    return included, excluded


def verify_train(repo: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    included, excluded = validate_inventory_shape(repo, inventory)
    _refuse_ambiguous_objects(repo)
    _refuse_hidden_checkout_state(repo)
    base = inventory["base"]
    head = inventory["head"]
    if _resolve(repo, base, "base") != base or _resolve(repo, head, "head") != head:
        raise TrainError("base/head do not resolve exactly")
    if _git(repo, "show-ref", "--verify", "--hash", BASE_REF).strip() != base:
        raise TrainError("origin/main moved after inventory freeze")
    if _resolve(repo, "HEAD", "HEAD") != head:
        raise TrainError("worktree HEAD is not the inventoried train head")
    branch = _git(repo, "branch", "--show-current").strip()
    if not TRAIN_RE.fullmatch(branch):
        raise TrainError("current branch is not a bounded train/* branch")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all").strip()
    if dirty:
        raise TrainError(f"train worktree must be completely clean: {dirty}")
    identities: set[tuple[int, str, str, str]] = set()
    numbers: set[int] = set()
    heads: set[str] = set()
    excluded_heads: list[str] = []
    for label, rows, is_excluded in (("included", included, False), ("excluded", excluded, True)):
        for index, raw in enumerate(rows):
            row = _exact_keys(raw, {"pr", "head_repo", "head_ref", "head",
                                    "reason" if is_excluded else "merge"}, f"{label}[{index}]")
            identity = _identity(repo, row, f"{label}[{index}]", is_excluded)
            if identity in identities or identity[0] in numbers or identity[3] in heads:
                raise TrainError("PR numbers, branch identities, and heads must be globally unique")
            identities.add(identity); numbers.add(identity[0]); heads.add(identity[3])
            if is_excluded:
                excluded_heads.append(identity[3])

    for excluded_head in excluded_heads:
        reachable = _run(repo, "merge-base", "--is-ancestor", excluded_head, head)
        if reachable.returncode == 0:
            raise TrainError("an excluded PR head is reachable from the train head")
        if reachable.returncode != 1:
            raise TrainError("could not prove an excluded PR is absent from the train")

    current = head
    for index in range(len(included) - 1, -1, -1):
        row = included[index]
        merge = _oid(row["merge"], f"included[{index}].merge")
        candidate = row["head"]
        if current != merge or _resolve(repo, candidate, "constituent") != candidate:
            raise TrainError(f"included[{index}] is not at its exact first-parent position")
        parents = _parents(repo, merge)
        if len(parents) != 2 or parents[1] != candidate:
            raise TrainError(f"included[{index}] has wrong merge parents")
        if _tree(repo, merge) != _automatic_tree(repo, parents[0], parents[1]):
            raise TrainError(f"included[{index}] tree differs from Git's exact clean merge")
        current = parents[0]
    if current != base:
        raise TrainError("first-parent chain has missing, reordered, or extra commits")
    return {
        "schema": "wandering-inn-reader-merge-train-receipt/1",
        "base": base,
        "head": head,
        "inventory_sha256": inventory_digest(inventory),
        "included": included,
        "excluded": excluded,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify_train(args.repo, load_inventory(args.inventory))
    except TrainError as exc:
        print(f"merge-train: REFUSED: {exc}")
        return 1
    print("merge-train: " + json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
