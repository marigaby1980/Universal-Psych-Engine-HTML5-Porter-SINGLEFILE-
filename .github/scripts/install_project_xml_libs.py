#!/usr/bin/env python3
"""
install_project_xml_libs.py <Project.xml>

Parses a Lime/OpenFL Project.xml for <haxelib name="..." version="..."/>
tags and installs each one via `haxelib install <name> <version>` at the
EXACT pinned version. This matters because `lime build` resolves library
versions directly against Project.xml, independent of whatever a package
manager like hmm may or may not have installed — a generic
`haxelib install flixel` grabs the newest published version, which silently
does NOT satisfy a `<haxelib name="flixel" version="5.6.1"/>` pin, and lime
fails immediately with "Could not find haxelib X version Y" the moment it
tries to resolve the classpath, before any real compilation starts.

Conditional tags (if="LUA_ALLOWED", if="VIDEOS_ALLOWED", if="debug", etc.)
are intentionally NOT evaluated — we install every declared haxelib
regardless of its condition. This is deliberately conservative: skipping a
lib because we guessed its condition was false is a much worse failure mode
(silent missing dependency at compile time) than installing one extra lib
that ends up unused for this build target.
"""

import re
import sys
import subprocess


def find_haxelib_tags(xml_text):
    """
    Returns a list of (name, version_or_None) tuples for every
    <haxelib .../> tag in the file, in document order.
    """
    tags = re.findall(r"<haxelib\b[^>]*/>", xml_text)
    results = []
    for tag in tags:
        name_match = re.search(r'name\s*=\s*"([^"]+)"', tag)
        version_match = re.search(r'version\s*=\s*"([^"]+)"', tag)
        if not name_match:
            continue
        name = name_match.group(1)
        version = version_match.group(1) if version_match else None
        results.append((name, version))
    return results


def main():
    if len(sys.argv) != 2:
        print("Usage: install_project_xml_libs.py <Project.xml>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        xml_text = f.read()

    libs = find_haxelib_tags(xml_text)
    if not libs:
        print("No <haxelib> tags found in Project.xml — nothing to install.")
        return

    # De-duplicate while preserving first-seen version pin, in case a lib
    # is referenced more than once (e.g. inside different <section> blocks).
    seen = {}
    for name, version in libs:
        if name not in seen:
            seen[name] = version

    print(f"Found {len(seen)} haxelib dependencies in Project.xml:")
    for name, version in seen.items():
        print(f"  - {name}" + (f" ({version})" if version else " (no version pinned)"))

    failures = []
    for name, version in seen.items():
        cmd = ["haxelib", "install", name]
        if version:
            cmd.append(version)
        cmd.append("--quiet")
        print(f"\nRunning: {' '.join(cmd)}")
        result = subprocess.run(cmd, input="y\n", text=True)
        if result.returncode != 0:
            failures.append((name, version))

    if failures:
        print("\n::error::Failed to install the following haxelib dependencies:")
        for name, version in failures:
            print(f"  - {name} {version or '(latest)'}")
        sys.exit(1)

    print("\nAll Project.xml-declared haxelibs installed successfully.")


if __name__ == "__main__":
    main()
