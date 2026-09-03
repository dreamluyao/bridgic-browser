---
name: bridgic-browser
description: |
  Use for any task requiring a real browser: viewing web pages, accessing login-gated sites, operating web UIs, scraping social media (Xiaohongshu/Weibo/Twitter/X, etc.), reading JS-rendered or dynamic pages, bypassing bot detection, form filling, e2e checks, and general web automation. Prefer this over WebFetch whenever the page needs JS execution, authenticated session, interaction, or stealth. Invoke via terminal CLI (`bridgic-browser ...`) or Python SDK (`from bridgic.browser.session import Browser`, `from bridgic.browser.tools import BrowserToolSetBuilder`). Also covers accessibility snapshot refs, CLI-SDK mapping/migration, and generating SDK code from CLI action steps.
---

## Dependencies

A bridgic-browser project requires the following packages:

| Package | Description |
|---------|-------------|
| `bridgic-browser` | Browser automation CLI + Python SDK (installing one installs both) |

Additionally, browser binaries must be installed once: `uv run playwright install chromium`.

**Installation**: Run the install script to set up all dependencies:

```bash
bash "skills/bridgic-browser/scripts/install-deps.sh" "$PWD"
```

The script checks uv availability, initializes a uv project if needed, installs missing packages, and ensures Playwright chromium is available.

`bridgic-browser` is served by the private pypiserver at `http://us-papy-oc:4000/simple/`, not by public PyPI; the script writes that index into `pyproject.toml` before installing. Set `BRIDGIC_DEV_INDEX` to point at a different server. An install that fails to fetch is almost always the index being unreachable, not a missing package.

## Strategies & Guidelines (Important!!)

Notes:
- Whenever invoking the `bridgic-browser` CLI, you must call it using `uv run`.
- If the user clearly specifies exact steps that must be followed, try to perform the exploration according to those steps. If loops or branches appear during exploration, decide the best exploration path autonomously.
- If you think you may need to return to the original page after clicking into a new page, try opening the new page in a new browser tab instead of using a “click then go back” approach. This is especially important when the original page already has interaction state (such as filled forms or applied filters); otherwise, that state may be lost after navigating back. Be sure to close the new tab promptly after finishing the related actions.
- If exploration involves repeatedly clicking items in a list, you do not need to traverse every item (especially when the list is large).
- If login, verification, or authorization is required during exploration, pause and ask the user to complete it manually, unless the user explicitly provides instructions in the task.
- To avoid operating on websites too frequently, maintain human-like access intervals during both exploration and coding. You may simulate random wait times to reduce the risk of being blocked. Note: the `bridgic-browser wait` command parameter is in **seconds**, not milliseconds; for example, `bridgic-browser wait 2` or `bridgic-browser wait 3.2`.
- After finishing exploration and code writing, automatically run testing/validation.
- **PDF links download automatically — on the persistent profile only**: the built-in PDF viewer is disabled by writing `plugins.always_open_pdf_externally` into the profile before launch, which happens on the persistent-context path and nowhere else. With `clear_user_data=True` (ephemeral) or in CDP mode the preference is never written, the viewer stays on, and a PDF link opens a viewer tab instead of downloading — so anything waiting on the download hangs until it times out. On the persistent path, clicking a PDF link saves the file to `downloads_path` (or `~/Downloads` in CLI mode). Access downloaded files via `browser.downloaded_files` in the SDK.
- **window.print() is intercepted**: `window.print()` never shows a dialog. It silently saves a `print-<timestamp>.pdf` to `downloads_path` and appends it to `browser.downloaded_files`. In headless mode this is the only way to get print output — the native call is a no-op without the intercept.
- **Credentials**: when filling a password, token, or OTP, pass `--secret` (CLI: `fill`, `type`, `fill-form`) or `is_secret=True` (SDK), or `"is_secret": true` on an individual `fill-form` field. It keeps the value out of the command response and bridgic's logs. It does **not** scrub an agent framework's own record of the tool arguments, your shell history, or a snapshot in which the page echoes the value back - see [cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md#secret-values-is_secret).
- **CDP mode tab visibility**: when attached via `--cdp` to a user's running Chrome, `tabs` / `switch-tab` / `close-tab` only see pages bridgic itself opened (the initial blank tab plus anything spawned from it via `new-tab` or a click on a `target="_blank"` link). The user's other tabs are deliberately invisible to bridgic — never assume you can `switch-tab` into them. To work with such a tab, ask the user to navigate to it through bridgic, or use `new-tab <url>`.

## Reference Files

Reference files cover all use cases. Load only the one(s) relevant to the task:

| Scenario | Interface | Load |
|---|---|---|
| Directly control browser from terminal | CLI | [cli-guide.md](references/cli-guide.md) |
| Write Python code about browser automation | Python | [sdk-guide.md](references/sdk-guide.md) |
| Write shell script about browser automation | CLI | [cli-guide.md](references/cli-guide.md) |
| Explore via CLI, then generate Python code | CLI → Python | [cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md) + [sdk-guide.md](references/sdk-guide.md) |
| Migrate / compare / explain CLI ↔ SDK | Both | [cli-sdk-api-mapping.md](references/cli-sdk-api-mapping.md) |
| Configure env vars or login state persistence | Either | [env-vars.md](references/env-vars.md) |
| Connect to an existing Chrome (chrome://inspect, `--remote-debugging-port`, cloud browser, Electron) | CLI / SDK | [cdp-mode.md](references/cdp-mode.md) |

## Interface Decision Rules

1. Output requested as shell commands or scripts → use CLI guide first (`references/cli-guide.md`).
2. Output requested as runnable Python code (`async`, `Browser`, tool builder) → use SDK guide first (`references/sdk-guide.md`).
3. Input is CLI outputs or actions but output needs to be Python code → use mapping guide first (`references/cli-sdk-api-mapping.md`), then SDK guide for final code generation (``references/sdk-guide.md``).
4. If intent is ambiguous, infer from requested artifacts (`.sh` / terminal session vs `.py` script).

## Common Usage (CLI + SDK)

- Ref-based actions depend on the latest snapshot.
- After navigation or major DOM updates, refs can become stale; refresh snapshot before ref actions.
- CLI keeps state in a daemon session across invocations. Set `BRIDGIC_HOME` env var to run multiple independent daemon instances (each with its own socket, logs, and user data).
- SDK keeps state in the Python process/context. By default, browser profile (cookies, session) is persisted to `$BRIDGIC_HOME/bridgic-browser/user_data/` (default `~/.bridgic/...`); pass `clear_user_data=True` to `Browser()` for an ephemeral session — but **to isolate a run, give it a fresh `user_data_dir` instead**: ephemeral sessions lose the PDF-download preference (see PDF links above).
- Use exact command/method names from references; do not invent aliases.

## Bridge Workflow: CLI Actions -> Python Code

1. Parse CLI steps in order.
2. Map each step using `references/cli-sdk-api-mapping.md`.
3. Preserve behavior details: refs, options, arguments, configuration, etc.
4. Emit runnable async Python code with explicit browser lifecycle (`async with Browser(...)` preferred).
5. Call out any behavior differences that cannot be represented 1:1.

## Minimal Quality Checklist

- CLI request: return valid CLI commands/options only.
- SDK request: return executable async Python with correct imports.
- Bridge request: include mapping rationale plus final SDK code.
