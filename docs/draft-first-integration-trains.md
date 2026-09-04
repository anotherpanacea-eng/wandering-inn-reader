# Draft-first periodic integration trains

**Status:** Built
**Tracking:** [Issue #37](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/37)

## Decision

This repository uses draft-first, periodic integration trains without GitHub Pro branch
protection, rulesets, Merge Queue, or custom GitHub Actions CI.

Ordinary work lives on its own non-`main` branch and remains a draft pull request while it waits.
The train branch is **not** a long-lived development branch. For each batch, create a fresh
same-repository `train/<batch>` branch at an exact frozen `main`, merge the reviewed exact heads
with merge commits, validate the closed inventory and the whole combined tree, land it once, and
delete it. This avoids a permanently diverging integration branch and makes every batch
reconstructable.

The repository's only automatic hosted work is GitHub Pages after `main` changes. Constituents do
not need hosted CI; one landed train therefore causes one Pages deployment. `./check.sh` remains
the dependency-free local correctness gate.

## Constituent lifecycle

1. Start from current `main`; write a contract in an issue for non-trivial work.
2. Open the PR with `gh pr create --draft`. Keep it draft through review and repair.
3. Run `python3 tools/run_local_gate.py` and any change-specific browser/device checks. The wrapper
   pins the current interpreter and keeps Python caches outside the worktree. Record honest limitations.
4. Review the exact head for generic correctness, fleet posture, and CI/cost. A changed head
   invalidates its reviews.
5. Leave the approved draft unmerged until the periodic batch.

Draft is a safety state, not a quality judgment. A ready-for-review ordinary PR is an exception
that must say why; it does not acquire permission to land by itself.

## Freeze and build a train

1. Fetch `origin` and freeze the full 40-hex `origin/main` commit.
2. Re-query every open PR. Record every non-train PR in a closed inventory as either included or
   excluded, with its exact same-repository head identity and a bounded reason for exclusion.
3. Refuse forks, duplicate identities, nonexistent or noncanonical refs, moved heads, an excluded
   head reachable from the train, and a base that is not the frozen `origin/main`.
4. Create a fresh `train/<batch>` at that base. Merge each included head in inventory order using
   `git merge --no-ff --no-edit <exact-head>`. Conflict resolution is not allowed on a train:
   repair the constituent, rerun its checks, and re-review its new exact head instead.
5. Write the final train head and merge commits into the JSON inventory. There are no train-only
   commits; policy changes are normal reviewed constituents.
6. Run:

   ```sh
   python3 tools/check_merge_train.py --inventory path/to/inventory.json
   python3 tools/run_local_gate.py
   ```

The verifier refuses replacement/graft/alternate/promisor ambiguity, a moved base, hidden index or
sparse-checkout state, dirty/ignored/untracked bytes, checkout/merge-affecting local config, a wrong
canonical origin, malformed or incomplete inventories, non-canonical or unbound branches, excluded
work smuggled through an included descendant, reordered/extra commits, and any merge whose tree is
not Git's exact clean automatic result. Its receipt repeats the exact inventory and includes the
SHA-256 of canonical sorted-key compact JSON. Review evidence binds both that digest and the 40-hex
train head; changing any identity, merge, exclusion, or reason invalidates approval.

## Review and land

Open the train as a draft PR. Review its exact head and canonical inventory digest independently in
all three lanes: generic, fleet-posture, and CI/cost. Put the three head-plus-digest approvals in the
dedicated PR-body field from the template. A train-head or inventory change invalidates all three.

Immediately before landing, use the executable landing path from the clean exact train checkout:

```sh
python3 tools/land_merge_train.py \
  --inventory /external/path/train.json \
  --train-pr 123 --train-ref train/example --push
```

The tool drops ambient `GIT_*` state for every Git, GitHub, gate, proof, fetch, and push child. It
uses the fixed canonical repository URL and credential helper, re-fetches `main`, all inventoried
refs, the train, and GitHub's synthetic merge, re-queries the complete open-PR inventory, validates
the three head-plus-digest approvals, verifies the canonical inventory receipt, and runs
`./check.sh` with Python caches outside the worktree. It then repeats all mutable proofs and exact
tree/custody checks after that potentially long gate, proves the synthetic merge's exact parent
order and clean tree, and advances `main` only with a compare-and-swap lease against the frozen
base. After landing it verifies containment, closes any still-open included/train PRs, proves the
remaining open set equals the declared exclusions, and deletes only constituent/train branches
that still match their inventoried heads under exact leases. Without `--push`, it performs the
same preflight and does not mutate `main`.

Afterward, close governing issues. The tool has already proved every included head is in `main`,
closed/observed the constituent and train PRs, disposed of unchanged same-repository branches under
exact leases, and verified the remaining open set. A failed compare-and-swap is a refusal, never
permission to force through a moved `main`.

## Inventory format

`tools/check_merge_train.py` accepts schema `wandering-inn-reader-merge-train/1`:

```json
{
  "schema": "wandering-inn-reader-merge-train/1",
  "repository": "anotherpanacea-eng/wandering-inn-reader",
  "base_ref": "refs/remotes/origin/main",
  "base": "40-hex",
  "head": "40-hex",
  "included": [
    {
      "pr": 36,
      "head_repo": "anotherpanacea-eng/wandering-inn-reader",
      "head_ref": "feat/example",
      "head": "40-hex",
      "merge": "40-hex"
    }
  ],
  "excluded": [
    {
      "pr": 99,
      "head_repo": "anotherpanacea-eng/wandering-inn-reader",
      "head_ref": "feat/not-this-batch",
      "head": "40-hex",
      "reason": "bounded printable reason"
    }
  ]
}
```

The inventory is external operational evidence, not a commit added on top of the train. Store it
outside the worktree or on the train PR. This preserves the invariant that the train contains only
the constituent merge commits it claims.
