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

CONDITION HANDLING
-------------------
<haxelib> tags can be gated by if="..."/unless="..." attributes, and can
also sit inside <section if="...">...</section> blocks (which nest). We
evaluate these against a small, fixed set of defines representing an
HTML5 *mod* build (as opposed to an official/native build), because:

  - Some declared libs (e.g. Psych Engine's funkin.vis, grig.audio, gated
    behind BASE_GAME_FILES/officialBuild) are git-only, NOT published on
    the public haxelib registry at all, and only exist to let the engine's
    *official* maintainer reproduce base-game assets. A generic
    `haxelib install <name>` for these fails outright with
    "No such Project", not a version problem — installing them is never
    correct for a third-party mod build regardless of version pinning.
  - Evaluating conditions lets us skip libraries that were never meant to
    be part of this build, avoiding failures on dependencies that aren't
    actually needed.

We deliberately keep this evaluation conservative and narrow: only
conditions we can resolve with confidence for "HTML5 mod build" are
evaluated (officialBuild, BASE_GAME_FILES, desktop, mobile, switch, debug,
html5, web). Any condition token we don't recognize is treated as
UNKNOWN, and unknown conditions default to "include the tag" — matching
the same reasoning as before: a missed library that's actually needed is a
worse failure than one extra install attempt.

Regardless of the above, a failed install for a given library is no longer
a hard failure of this script. Some declared libraries are legitimately
git-only or otherwise not installable via plain `haxelib install`, and the
separate verify_haxelibs.py step is what actually determines whether a
still-missing, version-pinned dependency will break the compile. This
script's job is to get as much installed as possible and report clearly
what it couldn't.

GIT-PINNED OVERRIDES
--------------------
A small number of libraries are not published on the public haxelib
registry at all, or the registry's "latest" release is incompatible with
the Haxe version this toolchain uses (observed: flxanimate's registry
release 4.0.0 uses syntax the pinned Haxe 4.3.2 toolchain can't parse,
while a specific older git commit is confirmed working). For libraries we
have a documented, maintainer-confirmed git pin for, we install from that
git URL/commit via `haxelib git` instead of the registry, regardless of
what Project.xml says. This table currently covers Psych Engine's own
documented "Libraries versions" wiki page; it's intentionally small and
explicit rather than a general git-resolution mechanism, since guessing at
a git source for a library we don't have confirmed info for would be worse
than just attempting a normal registry install and letting verification
catch it if that's wrong.
"""

import re
import sys
import subprocess


# name -> (git_url, ref). Installed via `haxelib git <name> <url> <ref>`
# INSTEAD OF the registry, regardless of any version Project.xml declares
# for that name (Project.xml's version attribute doesn't apply to git
# installs — the ref IS the version).
GIT_PINNED_LIBS = {
    "flxanimate": (
        "https://github.com/Dot-Stuff/flxanimate",
        "768740a56b26aa0c072720e0d1236b94afe68e3e",
    ),
    "linc_luajit": (
        "https://github.com/superpowers04/linc_luajit.git",
        None,
    ),
    "funkin.vis": (
        "https://github.com/FunkinCrew/funkVis",
        "22b1ce089dd924f15cdc4632397ef3504d464e90",
    ),
    "grig.audio": (
        "https://gitlab.com/haxe-grig/grig.audio.git",
        "cbf91e2180fd2e374924fe74844086aab7891666",
    ),
}


# Defines representing "building an HTML5 web port of a Psych Engine mod".
# True = condition is active. Anything not listed here is UNKNOWN (see
# _condition_holds below) and defaults to not excluding the tag.
KNOWN_DEFINES = {
    "html5": True,
    "web": True,
    "desktop": False,
    "mobile": False,
    "switch": False,
    "windows": False,
    "mac": False,
    "linux": False,
    "android": False,
    "ios": False,
    "debug": False,
    "release": True,
    "officialBuild": False,
    "BASE_GAME_FILES": False,
    "32bits": False,
    "MODS_ALLOWED": True,
}


def _condition_holds(expr, default_if_unknown=True):
    """
    Evaluates a Lime-style condition expression: a whitespace-separated
    list of tokens is treated as AND'd together (Lime's own semantics for
    a single if="a b" attribute). Any token not present in KNOWN_DEFINES
    is treated as satisfying `default_if_unknown`, so a single unknown
    token doesn't necessarily flip a whole multi-token expression to
    excluded — it only fails to positively confirm it.
    """
    if expr is None:
        return True
    tokens = expr.split()
    if not tokens:
        return True
    for tok in tokens:
        if tok in KNOWN_DEFINES:
            if not KNOWN_DEFINES[tok]:
                return False
        else:
            if not default_if_unknown:
                return False
    return True


def _tag_attr(tag, attr):
    m = re.search(rf'{attr}\s*=\s*"([^"]*)"', tag)
    return m.group(1) if m else None


def _tag_included(tag):
    """Evaluate a tag's own if=/unless= attributes."""
    if_expr = _tag_attr(tag, "if")
    unless_expr = _tag_attr(tag, "unless")
    if if_expr is not None and not _condition_holds(if_expr):
        return False
    if unless_expr is not None and _condition_holds(unless_expr):
        return False
    return True


def find_haxelib_tags(xml_text):
    """
    Returns a list of (name, version_or_None, included) tuples for every
    <haxelib .../> tag in the file, in document order. `included` reflects
    both the tag's own if=/unless= AND any enclosing <section if="...">
    blocks it's nested inside (sections don't nest more than a couple
    levels deep in practice, but we track a stack to be safe).
    """
    results = []
    section_stack = []  # each entry: bool, whether that section is active

    # Walk the document as a flat token stream of section-open, section-
    # close, and haxelib tags, in order, to know current nesting state.
    token_re = re.compile(
        r"(?P<haxelib><haxelib\b[^>]*/>)"
        r"|(?P<sec_open><section\b[^>]*>)"
        r"|(?P<sec_close></section>)"
    )

    for m in token_re.finditer(xml_text):
        if m.group("sec_open"):
            tag = m.group("sec_open")
            active = _tag_included(tag)
            # A nested section is only active if all enclosing sections
            # are also active.
            parent_active = all(section_stack) if section_stack else True
            section_stack.append(active and parent_active)
        elif m.group("sec_close"):
            if section_stack:
                section_stack.pop()
        elif m.group("haxelib"):
            tag = m.group("haxelib")
            name_match = re.search(r'name\s*=\s*"([^"]+)"', tag)
            if not name_match:
                continue
            name = name_match.group(1)
            version_match = re.search(r'version\s*=\s*"([^"]+)"', tag)
            version = version_match.group(1) if version_match else None
            in_active_section = all(section_stack) if section_stack else True
            included = in_active_section and _tag_included(tag)
            results.append((name, version, included))

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

    # De-duplicate while preserving first-seen version pin. If a lib
    # appears more than once with conflicting included-ness, treat it as
    # included if ANY occurrence is included.
    seen = {}
    for name, version, included in libs:
        if name not in seen:
            seen[name] = {"version": version, "included": included}
        else:
            if included:
                seen[name]["included"] = True
            if seen[name]["version"] is None and version:
                seen[name]["version"] = version

    to_install = {n: v for n, v in seen.items() if v["included"]}
    skipped = {n: v for n, v in seen.items() if not v["included"]}

    print(f"Found {len(seen)} haxelib dependencies in Project.xml "
          f"({len(to_install)} apply to this build, {len(skipped)} skipped by condition):")
    for name, info in seen.items():
        tag = "install" if info["included"] else "SKIP (condition not met for HTML5 mod build)"
        version_str = f" ({info['version']})" if info["version"] else " (no version pinned)"
        print(f"  - {name}{version_str} -> {tag}")

    failures = []
    for name, info in to_install.items():
        version = info["version"]

        if name in GIT_PINNED_LIBS:
            git_url, ref = GIT_PINNED_LIBS[name]
            cmd = ["haxelib", "git", name, git_url]
            if ref:
                cmd.append(ref)
            print(f"\n{name} has a known git pin — installing from source instead of the registry.")
            print(f"Running: {' '.join(cmd)}")
        else:
            cmd = ["haxelib", "install", name]
            if version:
                cmd.append(version)
            cmd.append("--quiet")
            print(f"\nRunning: {' '.join(cmd)}")

        result = subprocess.run(cmd, input="y\n", text=True)
        if result.returncode != 0:
            failures.append((name, version))

    if failures:
        print("\n::warning::The following haxelib dependencies could not be installed via `haxelib install`:")
        for name, version in failures:
            print(f"  - {name} {version or '(latest)'}")
        print(
            "This is not necessarily fatal — some libraries are git-only or otherwise "
            "not published on the public haxelib registry. The next step "
            "(verify_haxelibs.py) will determine whether any version-pinned dependency "
            "the compiler actually needs is still missing."
        )

    print("\nDone installing Project.xml-declared haxelibs that apply to this build.")


if __name__ == "__main__":
    main()
