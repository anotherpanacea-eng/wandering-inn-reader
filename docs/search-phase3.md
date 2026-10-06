# Local in-book search (Phase3A)

Search finds a literal phrase in the currently open text or audio/sync document. Open Search, enter a phrase and submit; typing alone does not scan. Choose a result to reveal its source character in scroll or paged mode. Search never starts audio. An audio hit pauses playback, retains source-faithful text while paused and seeks only to an admitted segment time within a playable source. Press Play explicitly to restore timed words and listen.

Matching uses per-code-point JavaScript lowercase and collapsed ECMAScript whitespace. Punctuation and accents remain significant; it is not linguistic casefolding or fuzzy search. Queries are limited to256 Unicode code points. Excerpts, including ellipses, have at most120 code points. Searches do not cross segments.

Results arrive in batches of50, with one pending hit and a resumable cursor. Counts remain lower bounds until the full scan ends. At500 displayed hits with additional matches, refine the query. Close cancels unpublished work; published results and their cursor remain available for the same admitted document. Loading or reopening a book clears search. Failed imports preserve the old document's results.

The sheet supports Enter, Escape and contained Tab navigation. Query/results are transient; they never enter saved positions, storage, synchronization, URLs or exports. Successful navigation uses the existing segment-level position path, so reload resumes a segment rather than an exact searched character. Bookmarks, notes and time-left remain separate Phase3 work.

The controlling reviewed contract is Reader draft56 atfcefe4132e572406f1715e8d099655443074219a. Development checks use invented text and generated silent audio only. Run the normal native local gate and `python tools/run_node_tests.py tests/test_search.mjs`. The manual harness `tests/search_harness.html` uses the actual player in a same-origin iframe; serve this checkout locally with Python's standard-library HTTP server to exercise it. It installs nothing and sends no data to another service.

Draft implementation remains subject to independent exact-head reviews, Claude review and normal integration-train validation. Browser automation is development evidence, not phone or real-book qualification.