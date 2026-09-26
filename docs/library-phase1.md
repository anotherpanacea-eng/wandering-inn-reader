# Phase 1: local text library

Contract for issue #29, based on main 85408b2. Tier: COMPLEX; builder: Codex (runtime family GPT-6; no more specific identity supplied). Independent spec review covered migration, transaction failure, races, security, compatibility and acceptance before implementation.

## Storage and ownership

Keep index.html self-contained, dependency-free and local-first. Upgrade inn-reader to version 2 additively: preserve session; add books keyed by existing content identity. Records version 1 contain projected doc, identity, format, reimportStable, importedAt, lastReadAt, position {seg}, and optional admitted cover Blob. Preserve the public document schema. EPUB/TXT/Markdown only; audiobook libraries, annotations, TTS and cloud library are excluded.

Same bytes/format upsert preserves importedAt and position. Different content with the same title stays separate. Ephemeral identities are locally durable but cannot recognize reimports. IndexedDB becomes position authority once admitted. New admissions may seed only their exact legacy identity position. Positions mean segment anchors, not exact words. Writes finish on transaction completion. Position updates only update existing records; never resurrect deleted records. Concurrent tabs use last-completed position writes.

## Migration and Resume

In one books+session transaction, inspect session/last once, validate a generic document and admit it using its identity (or a generated legacy identity). Existing records win. Store a migration disposition atomically even for absent/invalid/audio legacy state. Retain the original last record for rollback, never re-admit from it after migration; deletion preserves the marker.

A lightweight session/resume selector chooses text identity or audio. Successful text admission/open updates it atomically with the book; successful audio session save sets audio in the same transaction as session/last. Position saves do not change this last-explicit-open selector. Migration initializes it only if absent. Text Resume re-reads the current selector and book on click. Missing/deleted text never falls back to legacy text. Deletion clears only a matching text selector. Failed admission preserves the previous selector. Test both audio→text→reload and text→audio→reload.

## Failure and concurrency

Parse/identity/validation failure leaves active and durable state unchanged. Successful parse followed by quota/abort/denial opens in memory with persistent visible unsaved status; prior records and selector survive. Superseded imports cannot replace newer choices. Serialize writes, capture identities/positions before scheduling, and check loadEpoch before admission and render. Flush the old position at switching boundaries. No teardown-only durability promise.

Blocked upgrades and version changes close connections and visibly report failure; no database reset. Malformed records remain removable without blocking healthy records. Delete failures leave cards intact. Deleting an open book allows in-memory reading with unsaved status; autosave cannot re-add it. Reimport is explicit re-admission. Global comfort/audio/Dropbox state is preserved. No new uploads.

## Shelf and covers

Shelf is available from load screen and reader; accessible open/delete actions, format, last-read and approximate progress; phone and desktop layout. Last-read updates on explicit open and committed position save. Sort descending timestamp, identity tie-break. Validate finite timestamps and stored documents.

Progress = floor(100 * words strictly before saved segment / total whitespace-delimited words), capped at 99; zero-word docs show 0. Explain approximate saved-place semantics, never imply completion or time left.

Only EPUB cover-image manifest entries admitted by existing package/archive checks are candidates. Choose first valid PNG/JPEG/WebP, at most 5,242,880 encoded bytes and 16,000,000 pixels. Validate signature, type and header dimensions BEFORE browser decode; reject unsupported/animated/malformed images and use a title/format fallback. Decode validation follows header bounds. Existing rejection of unsafe/missing manifest resources or invalid ZIPs is unchanged: fallback applies after package/archive admission. No remote fetch or SVG. Revoke object URLs on card removal/refresh.

## Acceptance

Synthetic EPUBs only. Actual browser: three books, independent positions across reload and same-byte reimport; migration/retry/delete/no-resurrection; live Resume revalidation; failed quota and request-success/abort preserve prior data; stale import and multitab deletion; valid/fallback covers; progress; phone/desktop and scroll/pages; existing generic security/comfort/audio regression harness. Node policy tests are imported by the existing mandatory generic-ingest suite so check.sh runs them without touching the workflow PR's gate file. Run full check.sh and independent generic/fleet build reviews before an unarmed draft. Browser evidence and deterministic Node evidence are reported separately; no merge/release/deployment.
