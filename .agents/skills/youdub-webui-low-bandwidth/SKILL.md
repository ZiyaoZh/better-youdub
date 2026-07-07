---
name: youdub-webui-low-bandwidth
description: YouDub project WebUI design and implementation rules for low-bandwidth remote access. Use when Codex changes, reviews, or plans YouDub WebUI behavior, layout, task lists, polling, artifacts, media download/preview behavior, FastAPI Web endpoints, or files under src/youdub/web.py and src/youdub/web_static/.
---

# YouDub WebUI Low-Bandwidth Rules

## Core Context

Assume the WebUI may be used over SSH forwarding plus Tailscale DERP at roughly 1-2 Mbit/s. Treat bandwidth, request size, and accidental large media transfer as primary design constraints.

Do not spend effort on public exposure, auth, or network security unless the user explicitly asks. Focus this skill on WebUI data transfer, interaction ergonomics, layout stability, and avoiding large default payloads.

## Required Design Rules

- Keep task list APIs lightweight. The list view should fetch paginated summaries only, not full task config, artifact details, or step completion maps for every task.
- Fetch full task details only for the selected task or an explicitly opened detail view.
- Keep polling conservative. Prefer adaptive refresh intervals: faster only while tasks are queued/running, slower when idle, and no polling while the document is hidden.
- Keep pagination stable. Do not let right-side task detail content change the left task list height or derived page size.
- On desktop, keep the main WebUI workspace bounded to the viewport and let detail panels scroll internally.
- On mobile/narrow layouts, allow natural page flow if needed, but keep list height bounded enough that details remain reachable.
- Do not embed or auto-preview final videos by default. The WebUI should provide download links for `video.mp4` and other large artifacts unless the user explicitly asks for preview support.
- If preview support is requested later, use explicit low-bitrate preview artifacts, never the production `video.mp4` as the default player source.
- Use cache-busting query versions when changing static assets referenced from `index.html`.
- Avoid UI behaviors where hover states, dynamic labels, selected task detail, or artifact lists resize the task panel and change pagination.

## Implementation Checklist

When changing WebUI code:

1. Inspect the relevant files first:
   - `src/youdub/web.py`
   - `src/youdub/web_static/app.js`
   - `src/youdub/web_static/index.html`
   - `src/youdub/web_static/styles.css`
   - `tests/test_web.py`
2. Preserve the split between list summary responses and full task detail responses.
3. Verify that `/api/tasks` remains paginated and does not reintroduce heavy per-task fields such as `config`, `artifacts`, or `step_completion`.
4. Verify large artifacts remain explicit downloads. Do not add `<video>` elements or media `src` assignment to final artifacts without an explicit user request.
5. Keep desktop layout height stable:
   - `.app-shell` should stay viewport-bounded.
   - `.workspace` should use a bounded grid row such as `minmax(0, 1fr)`.
   - `.detail-panel` should scroll internally.
   - `.tasks-panel` should not grow with detail content.
6. Update tests for API shape, absence of accidental media preview, and layout invariants when changing these behaviors.
7. Run available validation:
   - `node --check src/youdub/web_static/app.js`
   - `python3 -m py_compile src/youdub/web.py tests/test_web.py`
   - `git diff --check`
   - `PYTHONPATH=src python3 -m pytest tests/test_web.py -q` when FastAPI is installed.

## Common Pitfalls

- A CSS grid/flex container with only `min-height` can grow when the detail side grows. Use fixed viewport-bounded desktop layout and internal scrolling instead.
- Recomputing page size from a mutable panel height can create feedback loops where fetching more tasks makes the panel taller, which increases the next page size.
- FileResponse download links are acceptable for large artifacts; automatic browser media playback is not.
- A small JSON field added to each task becomes expensive when multiplied by all tasks and polled repeatedly.
- Static asset cache versions must change after CSS or JS behavior changes, or remote browsers may keep the old behavior.
