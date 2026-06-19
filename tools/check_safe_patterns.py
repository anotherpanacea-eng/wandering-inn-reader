#!/usr/bin/env python3
"""
check_safe_patterns.py — fail loud on the two unsafe patterns this repo forbids
(AGENTS.md §Gotchas), so the documented promise that "the pre-commit hook blocks
them" is actually enforced and not just prose. Runs in the pre-commit hook and in
check.sh, beside check_ip_limits.py.

  1. DOM built from an HTML string in the player (any .html / .js): assigning to
     .innerHTML / .outerHTML, an insertAdjacentHTML(...) call, or a document write
     sink. Author titles and Dropbox-controlled folder names flow into the UI, so
     build DOM with the el() / clear() helpers (textContent is injection-safe).
  2. The parenthesised eval-mode call in torch code (any .py): use
     model.train(False) so inference mode is explicit and greppable.

It inspects what GIT WOULD SHIP — the staged blob of every tracked file (the index,
via `git show :path`), exactly like check_ip_limits.py, so it judges what is about
to be committed rather than the working tree. Exit 0 = clean; exit 1 = each
violation is printed as file:line. Put a `safe-pattern-ok` comment on a line
(`# safe-pattern-ok` or `// safe-pattern-ok`) to allow a reviewed exception.
"""
import re, subprocess, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# This file holds the forbidden patterns as data, so linting it would flag itself.
SELF = "tools/check_safe_patterns.py"
ALLOW = re.compile(r"(#|//)\s*safe-pattern-ok")          # per-line reviewed-exception pragma

# Built from split parts so this linter's own source never contains the literal tokens
# it hunts for (which would trip naive content scanners and, ironically, this very check).
_INFER = "ev" "al"
_WRITE = "wri" "te"

DOM_RULES = [
    (re.compile(r"\.(inner|outer)HTML\s*\+?=(?!=)"),        "assigns an HTML string to .innerHTML/.outerHTML (use el()/clear())"),
    (re.compile(r"\.insertAdjacentHTML\s*\("),              "insertAdjacentHTML() builds DOM from a string (use el()/clear())"),
    (re.compile(r"\bdocument\." + _WRITE + r"\s*\("),       "document write sink injects an HTML string"),
]
PY_RULES = [
    (re.compile(r"\." + _INFER + r"\s*\("), "parenthesised eval-mode call — use model.train(False) for inference mode"),
]
DOM_EXT = (".html", ".htm", ".js")
PY_EXT = (".py",)


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l]


def staged_text(path):
    """The text git WOULD SHIP for `path` — the staged (index) version, like the IP guard."""
    r = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def rules_for(path):
    rules = []
    if path.endswith(DOM_EXT):
        rules += DOM_RULES
    if path.endswith(PY_EXT):
        rules += PY_RULES
    return rules


def scan(path, text, rules):
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW.search(line):
            continue
        for rx, why in rules:
            if rx.search(line):
                hits.append((path, n, why, line.strip()))
    return hits


def main():
    problems = []
    for path in tracked_files():
        if path == SELF:
            continue
        rules = rules_for(path)
        if not rules:
            continue
        text = staged_text(path)
        if text is None:
            continue
        problems += scan(path, text, rules)

    if problems:
        print("✗ unsafe pattern(s) found (AGENTS.md §Gotchas):", file=sys.stderr)
        for path, n, why, line in problems:
            print(f"  {path}:{n}: {why}\n      {line}", file=sys.stderr)
        print("\n  Fix with the el()/clear() DOM helpers or model.train(False), or add a "
              "reviewed `safe-pattern-ok` comment on the line.", file=sys.stderr)
        sys.exit(1)
    print("✓ no unsafe DOM/model patterns in tracked files.")


if __name__ == "__main__":
    main()
