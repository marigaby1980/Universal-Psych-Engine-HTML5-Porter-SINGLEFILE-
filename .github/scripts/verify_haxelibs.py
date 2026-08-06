#!/usr/bin/env python3
"""
verify_haxelibs.py <Project.xml>

After installation, confirms every version-pinned <haxelib> declared in
Project.xml is actually present locally at that exact version. Note this
deliberately checks `haxelib list`, not `haxelib path`/"current version" —
`lime build` resolves a Project.xml pin like
<haxelib name="flixel" version="5.6.1"/> directly against whatever versions
are installed on disk, the same way `haxe -lib flixel:5.6.1` works
regardless of which version is marked "current". So the only thing that
actually matters here is: is 5.6.1 installed at all, under any version
slot — not whether it's selected as default.

Exits non-zero with a clear, itemized report if any pinned lib's exact
version isn't installed, so the workflow fails at the install stage with
an actionable message rather than failing later at compile time with the
same confusing "Could not find haxelib X version Y" error this exists to
prevent.
"""

import re
import subprocess
import sys


# Libraries known to be installed via `haxelib git` rather than the
# registry (see install_project_xml_libs.py's GIT_PINNED_LIBS). A git
# install shows up in `haxelib list` as version "git", not the actual
# pinned version string, so these are treated as satisfying any
# Project.xml version pin as long as SOME version is installed at all —
# the git ref itself, not this string match, is what actually pins the
# real content for these.
GIT_INSTALLED_LIBS = {"flxanimate", "linc_luajit", "funkin.vis", "grig.audio", "flixel-addons"}


def find_versioned_haxelib_tags(xml_text):
    tags = re.findall(r"<haxelib\b[^>]*/>", xml_text)
    results = []
    for tag in tags:
        name_match = re.search(r'name\s*=\s*"([^"]+)"', tag)
        version_match = re.search(r'version\s*=\s*"([^"]+)"', tag)
        if name_match and version_match:
            results.append((name_match.group(1), version_match.group(1)))
    return results


def installed_versions(lib_name):
    """
    Returns the set of all locally installed version strings for a
    haxelib, regardless of which one is "current". `haxelib list <name>`
    output looks like: 'flixel: [5.6.1] 5.4.0' where [x] marks current.
    """
    try:
        result = subprocess.run(
            ["haxelib", "list", lib_name],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return set()

    versions = set()
    for line in (result.stdout + result.stderr).splitlines():
        if ":" not in line:
            continue
        _, _, rest = line.partition(":")
        # Versions appear space-separated, current one wrapped in [brackets]
        for token in rest.strip().split():
            cleaned = token.strip("[]")
            if cleaned:
                versions.add(cleaned)
    return versions


def main():
    if len(sys.argv) != 2:
        print("Usage: verify_haxelibs.py <Project.xml>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        xml_text = f.read()

    pinned = find_versioned_haxelib_tags(xml_text)
    seen = {}
    for name, version in pinned:
        seen.setdefault(name, version)

    if not seen:
        print("No version-pinned haxelibs to verify.")
        return

    mismatches = []
    for name, expected_version in seen.items():
        versions_on_disk = installed_versions(name)
        if name in GIT_INSTALLED_LIBS:
            if "git" in versions_on_disk or versions_on_disk:
                print(f"  \u2713 {name}: installed via git (pinned by ref, not by haxelib version string)")
            else:
                found = "(none installed)"
                print(f"  \u2717 {name}: expected a git install, found: {found}")
                mismatches.append((name, "git install", found))
            continue
        if expected_version in versions_on_disk:
            print(f"  \u2713 {name}: {expected_version} is installed")
        else:
            found = ", ".join(sorted(versions_on_disk)) if versions_on_disk else "(none installed)"
            print(f"  \u2717 {name}: need {expected_version}, found: {found}")
            mismatches.append((name, expected_version, found))

    if mismatches:
        print("\n::error::The following libraries are missing the exact version Project.xml requires:")
        for name, expected, found in mismatches:
            print(f"  - {name}: need {expected}, installed versions: {found}")
        print(
            "This means `haxelib install <lib> <version>` did not succeed for that "
            "exact version — check the install step's output above for that library "
            "for the underlying error (network failure, version removed from the "
            "registry, etc.)."
        )
        sys.exit(1)

    print("\nAll pinned library versions are installed and available to lime/haxe.")


if __name__ == "__main__":
    main()

