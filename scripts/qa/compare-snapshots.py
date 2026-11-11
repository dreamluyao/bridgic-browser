#!/usr/bin/env python3
"""
scripts/qa/compare-snapshots.py - gate a Playwright upgrade on snapshot/ref drift.

bridgic refs hash (role, name, frame, nth) taken from Playwright's AI-mode aria
snapshot, whose format changes between Playwright minors. Before moving the
Playwright pin, capture snapshots of the same pages under the current and the
candidate Playwright, then diff them.

  capture  Run bridgic's own get_snapshot() on each page with whatever Playwright
           is installed in the running interpreter, and write a JSON file.
  diff     Compare two capture files and report which refs and names changed.

Typical flow (candidate Playwright in a throwaway venv, bridgic from this repo):

  uv run python scripts/qa/compare-snapshots.py capture before.json
  uv venv /tmp/pw-next && VIRTUAL_ENV=/tmp/pw-next uv pip install playwright==X.Y.Z \\
      && VIRTUAL_ENV=/tmp/pw-next uv pip install --no-deps -e . \\
      && VIRTUAL_ENV=/tmp/pw-next uv pip install bridgic-core bridgic-llms-openai pydantic click \\
      && /tmp/pw-next/bin/python -m playwright install chromium
  /tmp/pw-next/bin/python scripts/qa/compare-snapshots.py capture after.json
  uv run python scripts/qa/compare-snapshots.py diff before.json after.json

Pages default to tests/fixtures/*.html. Pass --pages with extra .html files or
directories (e.g. saved copies of the real pages your automations drive) - the
closer the pages are to production, the more the diff tells you. Saved copies of
dynamic pages may not render identically offline; treat them as approximations.

Exit status of `diff`: 0 when no control ref changed, 1 when a ref on a control
role (button, link, textbox, ...) disappeared - those are the refs automations
hard-code, so that is the case that needs a human look before upgrading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from typing import Dict, List

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_PAGES = REPO_ROOT / "tests" / "fixtures"

# Roles automations act on. A ref of one of these disappearing is what breaks
# hard-coded refs; container/text refs (row, generic, text, ...) are expected
# to drift with Playwright's snapshot clean-ups.
CONTROL_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox",
    "listbox", "option", "switch", "slider", "spinbutton", "tab", "menuitem",
    "menuitemcheckbox", "menuitemradio", "treeitem",
})


def _collect_pages(paths: List[str]) -> List[pathlib.Path]:
    pages: List[pathlib.Path] = []
    for raw in paths:
        path = pathlib.Path(raw).resolve()
        if path.is_dir():
            pages.extend(sorted(path.glob("*.html")))
        elif path.is_file():
            pages.append(path)
        else:
            sys.exit(f"Not found: {raw}")
    return pages


async def _capture(pages: List[pathlib.Path], settle_s: float) -> Dict:
    import importlib.metadata as md

    from bridgic.browser import Browser

    result: Dict = {"playwright": md.version("playwright"), "pages": {}}
    async with Browser(headless=True, clear_user_data=True) as browser:
        for page in pages:
            await browser.navigate_to(page.as_uri())
            await asyncio.sleep(settle_s)
            snapshot = await browser.get_snapshot(interactive=False, full_page=True)
            result["pages"][page.name] = {
                "refs": {ref: [d.role, d.name or ""] for ref, d in snapshot.refs.items()},
                "chars": len(snapshot.tree),
            }
            print(f"  {page.name}: {len(snapshot.refs)} refs", file=sys.stderr)
    return result


def _diff(before: Dict, after: Dict, ignore: re.Pattern | None) -> int:
    def keep(entry) -> bool:
        return not (ignore and ignore.search(entry[1]))

    print(f"Playwright {before['playwright']} -> {after['playwright']}\n")
    header = f"{'page':32s} {'refs':>6s} {'kept':>6s} {'gone':>6s} {'new':>6s} {'controls gone':>14s} {'size':>8s}"
    print(header)
    print("-" * len(header))
    controls_gone: List[str] = []
    gone_by_role: Dict[str, int] = {}
    for name, b in before["pages"].items():
        a = after["pages"].get(name)
        if a is None:
            print(f"{name:32s} missing from the second capture")
            continue
        refs_b = {k: v for k, v in b["refs"].items() if keep(v)}
        refs_a = {k: v for k, v in a["refs"].items() if keep(v)}
        gone = set(refs_b) - set(refs_a)
        new = set(refs_a) - set(refs_b)
        page_controls = [k for k in gone if refs_b[k][0] in CONTROL_ROLES]
        for k in gone:
            gone_by_role[refs_b[k][0]] = gone_by_role.get(refs_b[k][0], 0) + 1
        controls_gone.extend(f"{name}: {refs_b[k][0]} \"{refs_b[k][1]}\" [ref={k}]" for k in page_controls)
        size = (a["chars"] - b["chars"]) / b["chars"] if b["chars"] else 0.0
        print(
            f"{name:32s} {len(refs_b):6d} {len(refs_b) - len(gone):6d} {len(gone):6d} "
            f"{len(new):6d} {len(page_controls):14d} {size:+8.0%}"
        )
    print()
    if gone_by_role:
        print("Refs gone, by role:", ", ".join(f"{r} {n}" for r, n in sorted(gone_by_role.items(), key=lambda x: -x[1])))
    if controls_gone:
        print("\nControl refs that disappeared (automations may hard-code these):")
        for line in controls_gone:
            print("  " + line)
        return 1
    print("No control refs disappeared.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="snapshot pages with the installed Playwright")
    cap.add_argument("out", help="output JSON path")
    cap.add_argument("--pages", nargs="+", default=[str(DEFAULT_PAGES)],
                     help="HTML files or directories (default: tests/fixtures)")
    cap.add_argument("--settle", type=float, default=0.5, help="seconds to wait after load")

    dif = sub.add_parser("diff", help="compare two capture files")
    dif.add_argument("before")
    dif.add_argument("after")
    dif.add_argument("--ignore-name", default=None,
                     help="regex; refs whose name matches are ignored (e.g. timestamps)")

    args = parser.parse_args()
    if args.command == "capture":
        pages = _collect_pages(args.pages)
        result = asyncio.run(_capture(pages, args.settle))
        pathlib.Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Wrote {args.out} ({len(pages)} pages, Playwright {result['playwright']})", file=sys.stderr)
        return 0

    before = json.loads(pathlib.Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(pathlib.Path(args.after).read_text(encoding="utf-8"))
    ignore = re.compile(args.ignore_name) if args.ignore_name else None
    return _diff(before, after, ignore)


if __name__ == "__main__":
    sys.exit(main())
