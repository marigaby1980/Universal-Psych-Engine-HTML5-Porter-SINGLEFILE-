#!/usr/bin/env python3
"""
inject_mod_for_html5.py <mod_dir> <engine_dir>

Psych Engine's normal mod convention is: drop mod content into mods/, and
the ENGINE merges it over assets/ at runtime via backend/Mods.hx and
backend/Paths.hx's getPath()/modFolders() logic. That merge mechanism is
entirely gated behind `#if MODS_ALLOWED`, and Project.xml declares:

    <define name="MODS_ALLOWED" if="desktop" />

MODS_ALLOWED is desktop-only. On an HTML5 build, every mods/-reading code
path is dead code — compiled out entirely. A mod placed in mods/ is
therefore completely inert on web: never read, never merged, never
shown. This was confirmed directly from the real Paths.hx: getPath()'s
entire #if MODS_ALLOWED block (which checks modFolders()) is absent on
web, and the function falls straight through to getSharedPath(file) ->
'assets/shared/$file', or getFolderPath(file, folder) ->
'assets/$folder/$file' for the current level/song folder.

This script instead copies the mod's content DIRECTLY into the real
assets/ subfolders the compiled web build actually reads, mirroring what
Paths.hx's helper functions resolve to:

    characters/, images/, stages/  -> assets/shared/images/
    sounds/                        -> assets/shared/sounds/
    music/                         -> assets/shared/music/
    fonts/                         -> assets/fonts/
    data/                          -> assets/shared/data/
    songs/<name>/                  -> assets/songs/<name>/
    weeks/                         -> assets/shared/weeks/
    videos/                        -> assets/videos/ (dead weight for web;
                                       VIDEOS_ALLOWED is not defined for
                                       web builds regardless, per
                                       Project.xml's `if="windows || linux
                                       || android || mac"` condition, but
                                       copied harmlessly in case a fork
                                       changes that)
    custom_events/, custom_notetypes/, scripts/
                                    -> assets/shared/ (mirrored as-is;
                                       these are typically read via
                                       relative sub-paths from data/ or
                                       similar, so preserving the
                                       subfolder name under shared/ is
                                       the closest safe approximation
                                       without engine-specific per-file
                                       path logic this script can't see)

Anything else at the mod's top level (not matching a known category) is
copied into assets/shared/ under its own name, unchanged — a reasonable,
conservative default given Psych's own getPath() fallback ultimately
resolves most unqualified asset lookups through assets/shared/ too.

AUDIO CONVERSION: Paths.hx declares
`SOUND_EXT = #if web "mp3" #else "ogg" #end` — the web build looks for
.mp3 files specifically, and Project.xml's <assets> tags explicitly
`exclude="*.ogg" if="web"`. A mod shipping only .ogg (the normal
FNF/Psych source format) would have its audio silently excluded from
the web build entirely, regardless of correct folder placement. This
script converts every .ogg file to .mp3 (via ffmpeg, preinstalled on
GitHub's ubuntu-latest runners) as part of the copy, so mod audio
actually reaches the compiled build in the format the web target
expects.
"""

import os
import re
import shutil
import subprocess
import sys


# Folders that Psych Engine's own docs confirm are LAZY-LOADED at
# runtime through specific game events (e.g. an Event Script "will only
# run if the said Event is being used on the chart" — Psych's own Lua
# Script API docs), read directly via the mods/ mechanism at the moment
# they're needed, NOT meant to be part of the upfront bulk asset
# manifest. Since mods/ itself is inert on web (MODS_ALLOWED is
# desktop-only), there is no working on-demand-load path for these on a
# web build at all. Copying them into assets/shared/ anyway (an earlier
# version of this script did) caused a real, confirmed failure: Project.
# xml's blanket `<assets path="assets/shared" .../>` tag picks up
# EVERYTHING under that folder and bakes it into the preloader's
# mandatory bulk-load list — turning files that were only ever meant to
# be fetched conditionally into hard, load-blocking dependencies. Since
# even one failed asset is fatal to the whole preloader (confirmed
# directly from Lime's compiled AssetLibrary.load_onError, which calls
# promise.error() with no per-asset isolation), and there's no working
# mechanism to actually load these on-demand on web regardless, they are
# excluded from the copy entirely — a working build without these
# systems is a better outcome than a build that can't start at all.
EXCLUDED_FOLDERS = {"custom_events", "custom_notetypes", "scripts"}

# Mod top-level folder name -> destination path template. {file} is the
# path of the item relative to that top-level folder.
FOLDER_MAP = {
    "characters": "assets/shared/images/characters",
    "images": "assets/shared/images",
    "stages": "assets/shared/images/stages",
    "sounds": "assets/shared/sounds",
    "music": "assets/shared/music",
    "fonts": "assets/fonts",
    "data": "assets/shared/data",
    "weeks": "assets/shared/weeks",
    "videos": "assets/videos",
    # songs/ is handled specially below (preserves the per-song
    # subfolder exactly, since Paths.hx resolves song assets as
    # assets/songs/<songname>/... via getFolderPath/currentLevel).
}

AUDIO_EXTENSIONS_TO_CONVERT = {".ogg"}


def convert_audio_if_needed(src_path, dest_path):
    """
    If src_path is a .ogg file, converts it to .mp3 at dest_path (with
    the extension swapped) via ffmpeg. Otherwise copies src_path to
    dest_path unchanged. Returns the actual final destination path used.
    """
    ext = os.path.splitext(src_path)[1].lower()
    if ext in AUDIO_EXTENSIONS_TO_CONVERT:
        mp3_dest = os.path.splitext(dest_path)[0] + ".mp3"
        os.makedirs(os.path.dirname(mp3_dest), exist_ok=True)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_dest],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"::warning::ffmpeg failed to convert {src_path} -> {mp3_dest}, "
                  f"copying original .ogg instead (will be excluded by Project.xml on web): {result.stderr[-500:]}")
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)
            return dest_path
        return mp3_dest
    else:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return dest_path


# Common placeholder/scaffolding files that mod folder templates ship
# with empty folders (so they survive being zipped/committed, since git
# and most zip tools don't preserve genuinely empty directories) — e.g.
# Psych Engine's own mod template. These aren't real assets and were
# confirmed, via a real build's error log, to break the preloader when
# copied in as if they were: Project.xml's blanket <assets> tags pick up
# every file under a folder indiscriminately, including these, and the
# compiled build then tries to load them as real assets and fails.
PLACEHOLDER_PATTERNS = [
    re.compile(r".*-go-here\.txt$", re.IGNORECASE),
    re.compile(r"^\.gitkeep$", re.IGNORECASE),
    re.compile(r"^\.gitignore$", re.IGNORECASE),
    re.compile(r"^placeholder\.", re.IGNORECASE),
    re.compile(r"^readme\.(txt|md)$", re.IGNORECASE),
]


def is_placeholder_file(fname):
    return any(pattern.match(fname) for pattern in PLACEHOLDER_PATTERNS)


def copy_tree_with_conversion(src_dir, dest_dir):
    converted = 0
    copied = 0
    skipped_placeholders = 0
    for root, _dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        for fname in files:
            if is_placeholder_file(fname):
                skipped_placeholders += 1
                continue
            src_path = os.path.join(root, fname)
            dest_path = os.path.join(dest_dir, rel_root, fname) if rel_root != "." else os.path.join(dest_dir, fname)
            final_path = convert_audio_if_needed(src_path, dest_path)
            if final_path.endswith(".mp3") and src_path.endswith(".ogg"):
                converted += 1
            else:
                copied += 1
    if skipped_placeholders:
        print(f"    (skipped {skipped_placeholders} placeholder/scaffolding file(s))")
    return copied, converted


def main():
    if len(sys.argv) != 3:
        print("Usage: inject_mod_for_html5.py <mod_dir> <engine_dir>", file=sys.stderr)
        sys.exit(2)

    mod_dir, engine_dir = sys.argv[1], sys.argv[2]

    if not os.path.isdir(mod_dir):
        print(f"::error::Mod directory not found: {mod_dir}")
        sys.exit(1)

    total_copied = 0
    total_converted = 0

    top_level_entries = sorted(os.listdir(mod_dir))
    print(f"Mod top-level entries: {', '.join(top_level_entries)}")

    for entry in top_level_entries:
        entry_path = os.path.join(mod_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        key = entry.lower()

        if key in EXCLUDED_FOLDERS:
            print(f"  {entry}/ -> SKIPPED (lazy-loaded via mods/ at runtime on desktop; "
                  f"no working on-demand-load path on web, and including it would poison "
                  f"the preloader's mandatory bulk-load list — see EXCLUDED_FOLDERS comment)")
            continue

        if key == "songs":
            # Preserve per-song subfolder structure exactly:
            # songs/<name>/... -> assets/songs/<name>/...
            dest_base = os.path.join(engine_dir, "assets", "songs")
            copied, converted = copy_tree_with_conversion(entry_path, dest_base)
            print(f"  songs/ -> assets/songs/  ({copied} file(s) copied, {converted} .ogg->.mp3 converted)")
            total_copied += copied
            total_converted += converted
            continue

        if key in FOLDER_MAP:
            dest_base = os.path.join(engine_dir, FOLDER_MAP[key])
            copied, converted = copy_tree_with_conversion(entry_path, dest_base)
            print(f"  {entry}/ -> {FOLDER_MAP[key]}/  ({copied} file(s) copied, {converted} .ogg->.mp3 converted)")
            total_copied += copied
            total_converted += converted
            continue

        # Unknown top-level folder: conservative fallback into
        # assets/shared/<name>/, matching Paths.hx's own eventual
        # fallback through getSharedPath() for anything not otherwise
        # specially resolved.
        dest_base = os.path.join(engine_dir, "assets", "shared", entry)
        copied, converted = copy_tree_with_conversion(entry_path, dest_base)
        print(f"  {entry}/ -> assets/shared/{entry}/  (unrecognized folder, conservative fallback; "
              f"{copied} file(s) copied, {converted} .ogg->.mp3 converted)")
        total_copied += copied
        total_converted += converted

    # Any loose files directly at the mod's top level (not inside a
    # recognized folder) also fall back to assets/shared/.
    for entry in top_level_entries:
        entry_path = os.path.join(mod_dir, entry)
        if os.path.isfile(entry_path):
            dest_path = os.path.join(engine_dir, "assets", "shared", entry)
            final_path = convert_audio_if_needed(entry_path, dest_path)
            print(f"  {entry} -> assets/shared/{os.path.basename(final_path)}")
            total_copied += 1

    print(f"\nTotal: {total_copied} file(s) copied, {total_converted} .ogg->.mp3 conversion(s).")


if __name__ == "__main__":
    main()
