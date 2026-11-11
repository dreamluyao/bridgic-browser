#!/usr/bin/env python3
"""
scripts/qa/scan-snapshot-format-risks.py PROJECT_DIR [--snapshots PATH ...]

Flags code in an automation project that depends on parts of the bridgic
snapshot that Playwright 1.62+ changed. Standard library only - run it with any
Python 3.10+, no bridgic install needed.

What changed in Playwright's AI-mode snapshot (bridgic on Playwright 1.64.x):

1. A name made only of visible child content is dropped from elements whose role
   is not "identifying" - when the children that supplied it have refs and are
   not plain text. Rows lose their names (their cells carry the text); a cell
   holding a link or a textbox loses its name. A cell with only text keeps it.
2. A generic child that only repeats its parent's name is removed
   (``button "Save"`` no longer has ``generic: Save`` under it; a <legend> no
   longer appears under its ``group``).
3. Unnamed, non-clickable images are removed; adjacent text lines are merged.

bridgic refs hash (role, name, frame, nth), so (1) also changes those refs.

The scanner reads the project's Python files and, when present, its saved
bridgic snapshots (any .txt containing ``[ref=``, e.g. ``.bridgic/explore/``) to
predict which of the names and refs the code uses are affected:

  HIGH    a hard-coded ref or a name the code matches that is predicted to change
  MEDIUM  a pattern on a container/generic/text role that could not be checked
          against a saved snapshot, or that rule (2)/(3) may affect
  INFO    hard-coded refs on control roles - unchanged in our comparisons

Predictions use the saved snapshots' structure, so they are approximations of
Playwright's rules. Confirm with one verification run of each automation on the
new version.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# Playwright keeps these names even when they come from content
# (rolesWithIdentifyingNames in its injected aria snapshot code).
IDENTIFYING_ROLES = frozenset({
    "alertdialog", "dialog", "banner", "complementary", "contentinfo", "form", "main",
    "navigation", "region", "search", "application", "document", "figure", "group", "grid",
    "listbox", "menu", "menubar", "radiogroup", "tablist", "tabpanel", "toolbar", "tree",
    "treegrid", "table", "columnheader", "rowheader",
})
CONTROL_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "option",
    "switch", "slider", "spinbutton", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "treeitem",
})
# ARIA never computes these roles' names from their content (label / aria-label
# only), so rule (1) cannot apply to them.
NO_NAME_FROM_CONTENT = frozenset({
    "combobox", "textbox", "searchbox", "slider", "spinbutton", "list", "img", "progressbar",
})
# Roles whose names typically come from content and may be dropped by rule (1).
CONTAINER_ROLES = frozenset({"row", "cell", "gridcell", "listitem", "heading", "paragraph", "tooltip"})

SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__", "site-packages"}

SNAP_LINE = re.compile(r'^(?P<indent>\s*)- (?P<role>[a-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<rest>.*)$')
REF_IN_LINE = re.compile(r"\[ref=(?P<ref>[0-9a-f]{8})\]")
HEX_REF_LITERAL = re.compile(r"""["'](?P<ref>[0-9a-f]{8})["']""")
# `- row "Name"` / `- cell "Page ` / `- gridcell(?: "` inside a string or regex.
CODE_ROLE_PATTERN = re.compile(r"""-\s(?P<role>row|cell|gridcell|listitem|heading|paragraph|generic|img|text)\b(?P<after>[^\n]{0,60})""")
# role="row" / role='cell' keyword arguments, and ROLE = "row" constants.
CODE_ROLE_KW = re.compile(r"""role\w*\s*[=:,]\s*["'](?P<role>row|cell|gridcell|listitem|generic)["']""", re.IGNORECASE)


@dataclass
class SnapNode:
    role: str
    name: str
    value: str  # inline text after `:` (e.g. `- generic: Billing`, `- textbox "x": 01/02`)
    ref: Optional[str]
    indent: int
    children: List["SnapNode"]
    source: str
    line_no: int


@dataclass
class Finding:
    severity: str
    where: str
    message: str


def _iter_files(root: pathlib.Path, suffixes: Tuple[str, ...]) -> Iterable[pathlib.Path]:
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in suffixes:
            yield path


def _parse_snapshot(path: pathlib.Path) -> List[SnapNode]:
    nodes: List[SnapNode] = []
    stack: List[SnapNode] = []
    for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = SNAP_LINE.match(line)
        if not m:
            continue
        ref = REF_IN_LINE.search(line)
        rest = re.sub(r"\[[^\]]*\]", "", m.group("rest")).strip()
        value = rest[1:].strip().strip('"') if rest.startswith(":") else ""
        node = SnapNode(
            role=m.group("role"), name=(m.group("name") or "").replace('\\"', '"'), value=value,
            ref=ref.group("ref") if ref else None, indent=len(m.group("indent")),
            children=[], source=path.name, line_no=no,
        )
        while stack and stack[-1].indent >= node.indent:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        stack.append(node)
        nodes.append(node)
    return nodes


def _is_plain_text_child(node: SnapNode) -> bool:
    # `text` lines and generics that only hold text do not trigger rule (1).
    return node.role == "text" or (node.role == "generic" and not node.children)


def _descendant_text(node: SnapNode) -> str:
    parts: List[str] = []
    for child in node.children:
        if child.name:
            parts.append(child.name)
        if child.value:
            parts.append(child.value)
        parts.append(_descendant_text(child))
    return " ".join(p for p in parts if p)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _name_predicted_dropped(node: SnapNode) -> bool:
    if not node.name or node.role in IDENTIFYING_ROLES or node.role in NO_NAME_FROM_CONTENT:
        return False
    contributors = [c for c in node.children if c.ref and not _is_plain_text_child(c)]
    if not contributors:
        return False
    # Only names computed from the children's own content are dropped; a name
    # from a label / aria-label (e.g. a combobox "Status:") is not in the
    # children's text and is kept.
    return _norm(node.name) in _norm(_descendant_text(node))


def _load_snapshots(paths: List[pathlib.Path]) -> List[SnapNode]:
    nodes: List[SnapNode] = []
    for path in paths:
        try:
            if "[ref=" in path.read_text(encoding="utf-8", errors="replace"):
                nodes.extend(_parse_snapshot(path))
        except OSError:
            continue
    return nodes


def scan(project: pathlib.Path, snapshot_paths: List[pathlib.Path]) -> List[Finding]:
    snaps = _load_snapshots(snapshot_paths)
    by_ref: Dict[str, SnapNode] = {n.ref: n for n in snaps if n.ref}
    findings: List[Finding] = []

    for py in sorted(_iter_files(project, (".py",))):
        rel = py.relative_to(project)
        text = py.read_text(encoding="utf-8", errors="replace")
        for no, line in enumerate(text.splitlines(), 1):
            where = f"{rel}:{no}"
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # Hard-coded refs: any 8-hex literal that is a ref in a saved
            # snapshot, plus unknown ones on lines that mention "ref".
            mentions_ref = "ref" in line.lower()
            for m in HEX_REF_LITERAL.finditer(line):
                ref = m.group("ref")
                node = by_ref.get(ref)
                if node is None and not mentions_ref:
                    continue
                if node is None:
                    findings.append(Finding("MEDIUM", where, f"hard-coded ref {ref}: not in any saved snapshot, role unknown - verify it still resolves"))
                elif node.role in CONTROL_ROLES:
                    findings.append(Finding("INFO", where, f"hard-coded ref {ref} = {node.role} \"{node.name}\" (control role; unchanged in comparisons)"))
                elif _name_predicted_dropped(node):
                    findings.append(Finding("HIGH", where, f"hard-coded ref {ref} = {node.role} \"{node.name}\": name predicted dropped, so this ref changes ({node.source}:{node.line_no})"))
                else:
                    findings.append(Finding("MEDIUM", where, f"hard-coded ref {ref} = {node.role} \"{node.name}\" (non-control role; check it after upgrading)"))

            # Snapshot-text patterns on affected roles.
            for m in CODE_ROLE_PATTERN.finditer(line):
                role, after = m.group("role"), m.group("after")
                literal = re.match(r'\s*"([^"(\\\[]+)', after)
                if role == "img":
                    findings.append(Finding("MEDIUM", where, "matches `- img` lines: unnamed, non-clickable images are no longer emitted"))
                    continue
                if role == "text":
                    findings.append(Finding("MEDIUM", where, "matches `- text` lines: adjacent text is now merged into one line"))
                    continue
                if role == "generic":
                    findings.append(Finding("MEDIUM", where, "matches `generic` lines: a generic repeating its parent's name is now removed"))
                    continue
                if not literal:
                    if '(?: "' in after or '(?:"' in after:
                        findings.append(Finding("INFO", where, f"matches `- {role}` with an optional name - already tolerant of the name being dropped"))
                    else:
                        findings.append(Finding("MEDIUM", where, f"matches a `- {role} \"...\"` name: {role} names can be dropped when the content is in child elements"))
                    continue
                prefix = literal.group(1)
                matches = [n for n in snaps if n.role == role and n.name.startswith(prefix)]
                if not matches:
                    findings.append(Finding("MEDIUM", where, f"matches `- {role} \"{prefix}...\"`: no saved snapshot line to check against"))
                elif any(_name_predicted_dropped(n) for n in matches):
                    sample = next(n for n in matches if _name_predicted_dropped(n))
                    findings.append(Finding("HIGH", where, f"matches `- {role} \"{prefix}...\"`: that name is predicted dropped - its children carry refs ({sample.source}:{sample.line_no}). Read the child elements instead"))
                else:
                    findings.append(Finding("INFO", where, f"matches `- {role} \"{prefix}...\"`: name predicted kept (text-only content)"))

            for m in CODE_ROLE_KW.finditer(line):
                findings.append(Finding("MEDIUM", where, f"targets role \"{m.group('role').lower()}\" by name: that name may be dropped (rows always are)"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", help="automation project directory")
    parser.add_argument("--snapshots", nargs="*", default=None,
                        help="saved snapshot files/dirs (default: every .txt under the project containing [ref=)")
    parser.add_argument("--min-severity", choices=["INFO", "MEDIUM", "HIGH"], default="INFO")
    args = parser.parse_args()

    project = pathlib.Path(args.project).resolve()
    if args.snapshots is None:
        snapshot_paths = list(_iter_files(project, (".txt",)))
    else:
        snapshot_paths = []
        for raw in args.snapshots:
            p = pathlib.Path(raw)
            snapshot_paths.extend(p.rglob("*.txt") if p.is_dir() else [p])

    findings = scan(project, snapshot_paths)
    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    threshold = order[args.min_severity]
    shown = sorted((f for f in findings if order[f.severity] <= threshold), key=lambda f: (order[f.severity], f.where))

    counts = {s: sum(1 for f in findings if f.severity == s) for s in order}
    print(f"Project: {project}")
    print(f"Saved snapshots used: {len(snapshot_paths)}")
    print(f"Findings: HIGH {counts['HIGH']}, MEDIUM {counts['MEDIUM']}, INFO {counts['INFO']}\n")
    for f in shown:
        print(f"[{f.severity:6s}] {f.where}: {f.message}")
    return 1 if counts["HIGH"] else 0


if __name__ == "__main__":
    sys.exit(main())
