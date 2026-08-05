"""
list_hmm_libs.py <path-to-hmm.json>

Prints the name of every haxelib-type dependency declared in an hmm.json
file, one per line, to stdout. Used by the workflow to verify each pinned
library actually resolved after `hmm install`, since hmm can report success
while leaving a specific version's local .haxelib entry incomplete.
"""

import json
import sys


def main():
    if len(sys.argv) != 2:
        print("Usage: list_hmm_libs.py <hmm.json>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    deps = data.get("dependencies", [])
    libs = [
        d["name"]
        for d in deps
        if d.get("type", "haxelib") == "haxelib" and "name" in d
    ]

    for lib in libs:
        print(lib)

    print(f"Pinned haxelib dependencies: {', '.join(libs) if libs else '(none)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
