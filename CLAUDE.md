See [`AGENTS.md`](AGENTS.md) for the workflow, repo layout, the player/aligner data
contract, and this repo's gotchas. (This file is a thin pointer; `AGENTS.md` is
canonical and is read by both Claude and Codex sessions.)

In particular, follow `AGENTS.md` § **Test value convention**: tests must
protect behavior, contracts, reproduced bugs, or stable safety boundaries; do
not preserve implementation-mirroring tests or production seams built only for
tests.
