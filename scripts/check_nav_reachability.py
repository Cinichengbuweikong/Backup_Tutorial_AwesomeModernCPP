#!/usr/bin/env python3
"""
Navigation Reachability Checker for MkDocs + awesome-pages

Performs three checks:
1. Section index.md links to all child articles
2. .pages format convention (directory refs MUST end with /)
3. Navigation tree reachability (every content dir reachable from root .pages)

Usage:
    python scripts/check_nav_reachability.py [--json FILE] [--quiet] [--fail-on-incomplete]
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SectionCheck:
    source_index: str
    expected_children: List[str]
    found_children: List[str]
    missing_children: List[str]
    has_all_children: bool


@dataclass
class CheckResult:
    sections: List[SectionCheck] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS = {"images", "hooks", "stylesheets", "javascripts"}
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


# ---------------------------------------------------------------------------
# .pages helpers
# ---------------------------------------------------------------------------


def _parse_pages(pages_path: Path) -> Optional[dict]:
    """Parse a .pages file. Returns the full dict or None."""
    if not pages_path.exists():
        return None
    try:
        with open(pages_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _extract_nav_string_refs(nav_entries: list) -> List[Tuple[str, bool]]:
    """Extract all string references from a nav list (recursive).

    Returns list of (raw_ref, is_dict_value) tuples.
    """
    refs: List[Tuple[str, bool]] = []
    if not nav_entries:
        return refs
    for entry in nav_entries:
        if isinstance(entry, str):
            refs.append((entry, False))
        elif isinstance(entry, dict):
            for _label, target in entry.items():
                if isinstance(target, str):
                    refs.append((target, True))
                elif isinstance(target, list):
                    refs.extend(_extract_nav_string_refs(target))
    return refs


def _classify_ref(ref: str, parent_dir: Path) -> str:
    """Classify a nav ref as 'file', 'dir', or 'unknown'."""
    stripped = ref.rstrip("/")
    if ref.endswith(".md"):
        return "file"
    target = parent_dir / stripped
    if target.is_dir():
        return "dir"
    if (parent_dir / (stripped + ".md")).is_file():
        return "file"
    return "unknown"


# ---------------------------------------------------------------------------
# Check 2: .pages format convention
# ---------------------------------------------------------------------------


def check_pages_format(documents_dir: Path) -> List[Tuple[str, str, str]]:
    """Check all .pages files for format convention violations.

    Convention: directory references MUST end with '/'.

    Returns list of (pages_file_rel, raw_ref, violation_description).
    """
    violations: List[Tuple[str, str, str]] = []

    for pages_file in sorted(documents_dir.rglob(".pages")):
        rel = str(pages_file.relative_to(documents_dir))
        data = _parse_pages(pages_file)
        if data is None or "nav" not in data:
            continue

        nav = data["nav"]
        refs = _extract_nav_string_refs(nav)
        parent_dir = pages_file.parent

        for raw_ref, _is_dict in refs:
            kind = _classify_ref(raw_ref, parent_dir)
            if kind == "dir" and not raw_ref.endswith("/"):
                violations.append(
                    (rel, raw_ref, "directory ref missing trailing '/'")
                )

    return violations


# ---------------------------------------------------------------------------
# Check 3: Navigation tree reachability
# ---------------------------------------------------------------------------


def _collect_reachable_dirs_from_nav(nav: list, parent_dir: Path) -> Set[str]:
    """Get directory names referenced in a nav list (resolves both formats)."""
    refs = _extract_nav_string_refs(nav)
    dirs: Set[str] = set()
    for raw_ref, _ in refs:
        stripped = raw_ref.rstrip("/")
        if raw_ref.endswith(".md"):
            continue
        target = parent_dir / stripped
        if target.is_dir():
            dirs.add(stripped)
    return dirs


def _discover_subtree(dir_path: Path, rel_prefix: str, reachable: Set[str]):
    """Recursively discover reachable subdirectories based on .pages config."""
    if not dir_path.is_dir():
        return

    data = _parse_pages(dir_path / ".pages")
    nav = data.get("nav") if data else None

    if nav is not None:
        listed = _collect_reachable_dirs_from_nav(nav, dir_path)
        for d in listed:
            child_rel = f"{rel_prefix}/{d}" if rel_prefix else d
            reachable.add(child_rel)
            _discover_subtree(dir_path / d, child_rel, reachable)
    else:
        for child in sorted(dir_path.iterdir()):
            if (child.is_dir()
                    and child.name not in SKIP_DIRS
                    and not child.name.startswith(".")):
                child_rel = f"{rel_prefix}/{child.name}" if rel_prefix else child.name
                reachable.add(child_rel)
                _discover_subtree(child, child_rel, reachable)


def check_nav_reachability(
    documents_dir: Path,
) -> Tuple[Set[str], List[Tuple[str, str]]]:
    """Walk the .pages tree and find unreachable content directories.

    Returns:
        reachable: set of relative paths that are reachable
        unreachable: list of (rel_path, reason) for unreachable dirs
    """
    if yaml is None:
        return set(), [("?", "PyYAML not installed")]

    reachable: Set[str] = set()
    root_data = _parse_pages(documents_dir / ".pages")
    root_nav = root_data.get("nav") if root_data else None

    if root_nav is not None:
        root_dirs = _collect_reachable_dirs_from_nav(root_nav, documents_dir)
        for d in root_dirs:
            reachable.add(d)
            _discover_subtree(documents_dir / d, d, reachable)
    else:
        reachable.add("")
        for child in sorted(documents_dir.iterdir()):
            if child.is_dir() and child.name not in SKIP_DIRS:
                rel = child.name
                reachable.add(rel)
                _discover_subtree(child, rel, reachable)

    # Find all content directories
    all_content_dirs: Set[str] = set()
    for md_file in documents_dir.rglob("*.md"):
        parts = Path(md_file.relative_to(documents_dir)).parts
        if any(p in SKIP_DIRS for p in parts):
            continue
        for i in range(len(parts)):
            dir_path = "/".join(parts[:i])
            if dir_path:
                all_content_dirs.add(dir_path)

    unreachable = []
    for d in sorted(all_content_dirs):
        if d not in reachable:
            unreachable.append((d, "not in navigation tree"))

    return reachable, unreachable


# ---------------------------------------------------------------------------
# Check 1: Section index links to children (original logic)
# ---------------------------------------------------------------------------


def _source_rel_to_url(rel_path: str) -> str:
    p = Path(rel_path)
    if p.name == "index.md":
        return ""
    return p.stem


def build_section_map(
    documents_dir: Path,
) -> Dict[str, Tuple[str, List[str]]]:
    sections: Dict[str, Tuple[str, List[str]]] = {}

    for index_file in sorted(documents_dir.rglob("index.md")):
        rel = str(index_file.relative_to(documents_dir))
        parts = Path(rel).parts
        if any(p in SKIP_DIRS for p in parts):
            continue

        parent_dir = index_file.parent
        children = []
        for child in sorted(parent_dir.glob("*.md")):
            if child.name == "index.md" or child.name.endswith(".en.md"):
                continue
            children.append(child.stem)

        if children:
            sections[rel] = children

    return sections


def extract_linked_stems(content: str, index_rel: str) -> Set[str]:
    documents_dir = Path("documents")
    source_file = documents_dir / index_rel
    source_dir = source_file.parent
    linked_stems: Set[str] = set()

    in_code_block = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        cleaned = re.sub(r"`[^`]+`", "", line)

        for match in LINK_PATTERN.finditer(cleaned):
            url = match.group(2).split("#")[0]
            if not url:
                continue
            if url.startswith(("http:", "https:", "mailto:")):
                continue
            if Path(url).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif",
                                             ".svg", ".bmp", ".webp", ".ico"}:
                continue

            resolved = _resolve_link_stem(url, source_dir, documents_dir)
            if resolved:
                linked_stems.add(resolved)

    return linked_stems


def _resolve_link_stem(
    link_url: str, source_dir: Path, documents_dir: Path
) -> Optional[str]:
    try:
        target = (source_dir / link_url).resolve()
    except Exception:
        return None

    if target.is_file() and target.suffix == ".md":
        try:
            rel = str(target.relative_to(documents_dir.resolve()))
            return Path(rel).stem
        except ValueError:
            return None

    if not target.suffix:
        with_md = Path(str(target) + ".md")
        if with_md.is_file():
            try:
                rel = str(with_md.relative_to(documents_dir.resolve()))
                if not rel.endswith(".en.md"):
                    return Path(rel).stem
            except ValueError:
                return None

    if target.is_dir():
        index = target / "index.md"
        if index.exists():
            try:
                rel = str(index.relative_to(documents_dir.resolve()))
                return Path(rel).stem
            except ValueError:
                return None

    target_md = Path(str(target) + ".md")
    if target_md.is_file():
        try:
            rel = str(target_md.relative_to(documents_dir.resolve()))
            if not rel.endswith(".en.md"):
                return Path(rel).stem
        except ValueError:
            return None

    return None


def check_sections(
    section_map: Dict[str, Tuple[str, List[str]]],
    documents_dir: Path,
) -> List[SectionCheck]:
    results = []

    for index_rel, expected_stems in sorted(section_map.items()):
        filepath = documents_dir / index_rel
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            results.append(SectionCheck(
                source_index=index_rel,
                expected_children=expected_stems,
                found_children=[],
                missing_children=expected_stems,
                has_all_children=False,
            ))
            continue

        linked = extract_linked_stems(content, index_rel)
        missing = [s for s in expected_stems if s not in linked]

        results.append(SectionCheck(
            source_index=index_rel,
            expected_children=expected_stems,
            found_children=[s for s in expected_stems if s in linked],
            missing_children=missing,
            has_all_children=len(missing) == 0,
        ))

    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def print_section_check(result: CheckResult, quiet: bool = False) -> int:
    sections = result.sections
    good = [s for s in sections if s.has_all_children]
    bad = [s for s in sections if not s.has_all_children]

    print("=" * 60)
    print("Check 1: Section Index Links")
    print("=" * 60)
    print(f"Sections: {len(sections)} | OK: {len(good)} | Incomplete: {len(bad)}")
    print()

    if bad:
        print(f"--- Incomplete ({len(bad)}) ---")
        for s in bad:
            total = len(s.expected_children)
            found = len(s.found_children)
            print(f"  {s.source_index} ({found}/{total} linked)")
            for child in s.missing_children:
                print(f"    X {child}")
        print()

    if not quiet and good:
        print(f"--- OK ({len(good)}) ---")
        for s in good:
            total = len(s.expected_children)
            print(f"  + {s.source_index} ({total}/{total})")
        print()

    if not bad:
        print("All section index pages link to their children")

    print("=" * 60)
    return len(bad)


def write_json(result: CheckResult, output_file: str):
    output = {
        "stats": result.stats,
        "incomplete": [
            {
                "source_index": s.source_index,
                "found": len(s.found_children),
                "expected": len(s.expected_children),
                "missing_children": s.missing_children,
            }
            for s in result.sections
            if not s.has_all_children
        ],
        "ok": [
            {
                "source_index": s.source_index,
                "children": len(s.expected_children),
            }
            for s in result.sections
            if s.has_all_children
        ],
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nJSON report written to: {output_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Navigation reachability checker for MkDocs + awesome-pages"
    )
    parser.add_argument(
        "--json", metavar="FILE", help="Also write JSON report to FILE"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only show problems, no OK sections"
    )
    parser.add_argument(
        "--fail-on-incomplete", action="store_true",
        help="Exit non-zero when any check fails"
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    documents_dir = project_root / "documents"

    if not documents_dir.exists():
        print(f"Error: documents directory not found: {documents_dir}")
        sys.exit(1)

    total_failures = 0

    # --- Check 1: index.md links to children ---
    print("Scanning sections...")
    section_map = build_section_map(documents_dir)
    print(f"Found {len(section_map)} sections with child articles")

    print("Checking index pages for child links...")
    results = check_sections(section_map, documents_dir)

    good = sum(1 for s in results if s.has_all_children)
    bad = sum(1 for s in results if not s.has_all_children)
    missing_total = sum(
        len(s.missing_children) for s in results if not s.has_all_children
    )

    result = CheckResult(
        sections=results,
        stats={
            "total_sections": len(results),
            "ok": good,
            "incomplete": bad,
            "missing_child_links": missing_total,
        },
    )

    total_failures += print_section_check(result, quiet=args.quiet)

    # --- Check 2: .pages format convention ---
    print()
    print("=" * 60)
    print("Check 2: .pages Format Convention")
    print("=" * 60)

    if yaml is not None:
        violations = check_pages_format(documents_dir)
        if violations:
            print(f"Found {len(violations)} format violations:")
            print("  Convention: directory refs in .pages MUST end with '/'")
            print()
            for pages_rel, raw_ref, desc in violations:
                print(f"  {pages_rel}: '{raw_ref}' — {desc}")
            total_failures += len(violations)
        else:
            print("All .pages files follow format convention")
    else:
        print("Skipped (PyYAML not installed)")

    print("=" * 60)

    # --- Check 3: Navigation tree reachability ---
    print()
    print("=" * 60)
    print("Check 3: Navigation Tree Reachability")
    print("=" * 60)

    if yaml is not None:
        reachable, unreachable = check_nav_reachability(documents_dir)

        if unreachable:
            print(f"Found {len(unreachable)} unreachable content directories:")
            for path, reason in unreachable:
                print(f"  X {path}/ ({reason})")
            total_failures += len(unreachable)
        else:
            print(f"All {len(reachable)} content directories reachable from root nav")
    else:
        print("Skipped (PyYAML not installed)")

    print("=" * 60)

    if args.json:
        write_json(result, args.json)

    if args.fail_on_incomplete and total_failures > 0:
        print(f"\nFailed with {total_failures} total issue(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
