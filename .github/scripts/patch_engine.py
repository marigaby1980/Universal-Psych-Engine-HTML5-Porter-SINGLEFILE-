#!/usr/bin/env python3
"""
patch_engine.py

Stubs out native-only Haxe APIs (sys.*, cpp.*, llua.*, Discord RPC, threads,
mutexes) so that Psych Engine — or a fork/mod of it with custom scripts that
touch these same APIs — compiles cleanly for the HTML5 target.

This is intentionally broad/global (rewrites the whole source tree, not just
engine core) so the porter tolerates mods that ship their own HScript/Lua
that reference native-only classes, which is the main source of HTML5
compile failures across different Psych mods.
"""

import argparse
import os
import re
import glob


STUBS = {
    "source/psychporter/compat/FileSystem.hx": """\
package psychporter.compat;
class FileSystem {
  public static function absolutePath(path:String) return path;
  public static function exists(path:String) return false;
  public static function readDirectory(path:String):Array<String> return [];
  public static function isDirectory(path:String) return false;
  public static function stat(path:String) return null;
  public static function fullPath(path:String) return path;
  public static function createDirectory(path:String) {}
  public static function deleteFile(path:String) {}
  public static function deleteDirectory(path:String) {}
  public static function rename(p1:String, p2:String) {}
}
""",
    "source/psychporter/compat/StubBitmapData.hx": """\
package psychporter.compat;
// Minimal, non-crashing stand-in for openfl.display.BitmapData, used as
// the new base class for haxelib source that previously did
// `class SomeRawGraphic extends BitmapData {}` purely to trigger
// OpenFL's @:autoBuild(AssetsMacro.embedBitmap()) — a macro that crashes
// on this toolchain (see patch_asset_macro_bitmapdata_classes). This
// exists so that subclasses/callers depending on BitmapData's
// constructor signature or width/height fields still compile, without
// ever extending the real BitmapData (and therefore never triggering the
// crashing macro). It does not attempt to actually hold or render pixel
// data — anything calling pixel-manipulation methods on one of these
// will not work correctly, but the previous state was a hard compile
// failure, so an inert graphic is strictly an improvement.
class StubBitmapData {
  public var width:Int;
  public var height:Int;
  public var rect(get, never):Dynamic;
  public var transparent:Bool;

  public function new(width:Int = 0, height:Int = 0, transparent:Bool = true, ?fillColor:Int, ?onLoad:Dynamic) {
    this.width = width;
    this.height = height;
    this.transparent = transparent;
  }

  function get_rect():Dynamic return { x: 0, y: 0, width: width, height: height };

  public function clone():StubBitmapData return new StubBitmapData(width, height, transparent);
  public function dispose():Void {}
  public function fillRect(rect:Dynamic, color:Int):Void {}
  public function getPixel(x:Int, y:Int):Int return 0;
  public function getPixel32(x:Int, y:Int):Int return 0;
  public function setPixel(x:Int, y:Int, color:Int):Void {}
  public function setPixel32(x:Int, y:Int, color:Int):Void {}
  public function copyPixels(source:Dynamic, sourceRect:Dynamic, destPoint:Dynamic, ?alphaBitmap:Dynamic, ?alphaPoint:Dynamic, mergeAlpha:Bool = false):Void {}
}
""",
    "source/psychporter/compat/StubSound.hx": """\
package psychporter.compat;
// Minimal, non-crashing stand-in for openfl.media.Sound — same rationale
// as StubBitmapData above: some haxelib source does
// `class SomeRawSound extends Sound {}` purely to trigger OpenFL's
// crashing @:autoBuild(AssetsMacro) asset-embed macro (the Sound variant
// of the same bug that affects BitmapData). This does not actually
// decode or play audio; anything relying on real playback from one of
// these will not work correctly, but the previous state was a hard
// compile failure, so silent audio is strictly an improvement.
class StubSound {
  public var length(default, null):Float = 0;
  public var bytesLoaded(default, null):Float = 0;
  public var bytesTotal(default, null):Float = -1;
  public var id3(default, null):Dynamic;

  public function new(?stream:Dynamic, ?context:Dynamic) {}

  public function load(stream:Dynamic, ?context:Dynamic):Void {}
  public function close():Void {}
  public function play(startTime:Float = 0, loops:Int = 0, ?sndTransform:Dynamic):Dynamic return null;
}
""",
    "source/psychporter/compat/File.hx": """\
package psychporter.compat;
class File {
  public static function getContent(path:String) return "";
  public static function getBytes(path:String) return null;
  public static function saveContent(path:String, c:String) return;
  public static function saveBytes(path:String, b:Dynamic) return;
  public static function copy(s:String, d:String) {}
  public static function read(path:String, binary:Bool = true) return null;
  public static function write(path:String, binary:Bool = true) return null;
}
""",
    "source/Sys.hx": """\
class Sys {
  public static function exit(code:Int) {}
  public static function sleep(t:Float) {}
  public static function command(c:String, ?a:Array<String>) return 0;
  public static function args():Array<String> return [];
  public static function getCwd() return "";
  public static function setCwd(s:String) {}
  public static function print(v:Dynamic) {}
  public static function println(v:Dynamic) {}
  public static function environment() return new haxe.ds.StringMap<String>();
  public static function getEnv(s:String) return "";
  public static function programPath() return "";
  public static function systemName() return "HTML5";
  public static function time() return Date.now().getTime() / 1000;
}
""",
    "source/Process.hx": """\
class Process {
  public var stdout:Dynamic;
  public var stderr:Dynamic;
  public function new(c:String, ?a:Dynamic) {
    stdout = { readAll: function() return { toString: function() return "" }, readLine: function() return "" };
    stderr = stdout;
  }
  public function exitCode(b:Bool=true) return 0;
  public function close() {}
  public function kill() {}
}
""",
    "source/Thread.hx": """\
class Thread {
  public static function create(f:Void->Void) { f(); }
  public static function readMessage(b:Bool) return null;
  public static function sendMessage(m:Dynamic) {}
  public static function current() return null;
}
""",
    "source/Mutex.hx": """\
class Mutex {
  public function new() {}
  public function acquire() {}
  public function tryAcquire() return true;
  public function release() {}
}
""",
    "source/hxdiscord_rpc/DiscordRpc.hx": """\
package hxdiscord_rpc;
// Stub for hxdiscord_rpc.DiscordRpc, a native hxcpp @:native binding to
// the desktop Discord IPC client. This library is fundamentally
// desktop-only (it binds to a native C++ SDK that talks to the local
// Discord app over IPC) — there is no browser equivalent, so this is a
// permanent, structural no-op for HTML5 rather than a compatibility gap
// that could be closed with a better stub.
class DiscordRpc {
  public static function start(?opts:Dynamic):Void {}
  public static function shutdown():Void {}
  public static function runCallbacks():Void {}
  public static function updatePresence(?presence:Dynamic):Void {}
  public static function clearPresence():Void {}
  public static function respond(?userId:String, ?reply:Int):Void {}
}
""",
    "source/hxdiscord_rpc/DiscordEventHandlers.hx": """\
package hxdiscord_rpc;
// Anonymous-structure-like stub with a static `create` factory, matching
// the shape hxcpp @:native struct bindings expose. Using Dynamic fields
// means any field Psych's Discord.hx (or a fork's variant of it) sets on
// this — onReady, onDisconnected, onError, etc. — resolves without a
// compile error, regardless of the exact field set a given engine version
// expects.
class DiscordEventHandlers {
  public static function create(?opts:Dynamic):DiscordEventHandlers return new DiscordEventHandlers();
  public function new() {}
}
""",
    "source/hxdiscord_rpc/DiscordRichPresence.hx": """\
package hxdiscord_rpc;
// See DiscordEventHandlers above for why this uses a permissive
// create()-factory shape rather than an exact field match.
class DiscordRichPresence {
  public static function create(?opts:Dynamic):DiscordRichPresence return new DiscordRichPresence();
  public function new() {}
}
""",
    "source/hxdiscord_rpc/Types.hx": """\
package hxdiscord_rpc;
class Types {
  public static inline var DISCORD_REPLY_NO = 0;
  public static inline var DISCORD_REPLY_YES = 1;
  public static inline var DISCORD_REPLY_IGNORE = 2;
}
""",
    "source/hxdiscord_rpc/DiscordPresence.hx": (
        "package hxdiscord_rpc; typedef DiscordPresence = Dynamic;\n"
    ),
    "source/ThreadPool.hx": """\
// Stub for sys.thread.FixedThreadPool/IThreadPool, which the Haxe standard
// library deliberately blocks with #error on targets without real OS
// threads (HTML5/js included). Some haxelib dependency or engine code path
// imports this directly without a `#if target.threaded` guard; rewriting
// those imports to this class keeps the reference resolvable while making
// every operation a synchronous same-thread no-op/passthrough, which is a
// safe behavior for a single-threaded JS runtime.
class ThreadPool {
  public function new(threadsCount:Int = 1) {}
  public function submit(task:Void->Void):Void { if (task != null) task(); }
  public function run(task:Void->Void):Void { if (task != null) task(); }
  public function shutdown():Void {}
  public var isShutdown(get, never):Bool;
  function get_isShutdown():Bool return true;
}
""",
}

REGEX_REWRITES = [
    # FileSystem/File specifically: the JS/HTML5 target has real
    # js.html.FileSystem / js.html.File browser DOM APIs, which are
    # visible without an explicit import and can resolve ahead of an
    # unqualified top-level stub class of the same name (observed:
    # File.getContent() resolving to js.html.File, which has no such
    # method, rather than our stub). Rewriting to a fully package-
    # qualified reference makes resolution unambiguous regardless of
    # what's implicitly visible at the top level.
    (r"sys\.FileSystem", "psychporter.compat.FileSystem"),
    (r"sys\.io\.File\b", "psychporter.compat.File"),
    # Bare references to the same two — covers source files that use
    # File/FileSystem unqualified without ever writing the sys.* prefix
    # (e.g. relying on the engine's own now-removed top-level stub, or a
    # local import alias). Word-boundary anchored, and deliberately NOT
    # applied to already-qualified names like js.html.File or
    # psychporter.compat.File (negative lookbehind for a preceding dot),
    # AND NOT applied to the class's own declaration (negative lookbehind
    # for a preceding "class " — `class File {` is where the name is
    # DEFINED, not a reference to rewrite; rewriting it produces the
    # invalid `class psychporter.compat.File {`, which is what this
    # exists to prevent). This runs across the whole source tree
    # including our own freshly-written stub files, so the declaration
    # inside psychporter/compat/File.hx itself must be protected the same
    # way any other class's own declaration would need to be.
    (r"(?<!\.)(?<!class )\bFileSystem\b", "psychporter.compat.FileSystem"),
    (r"(?<!\.)(?<!class )\bFile\b(?!System)", "psychporter.compat.File"),
    (r"sys\.io\.Process", "Process"),
    # haxe.io.Path is a genuine cross-platform standard library class
    # (works fine on HTML5) but isn't auto-imported the way some other
    # std classes are — if source code uses the bare `Path` identifier
    # without importing it (observed in FileDialogHandler.hx, likely
    # copy-pasted from native-target code where a different import chain
    # happened to pull it in transitively), it fails with "Type not
    # found". Route bare, unqualified Path references to the fully
    # qualified standard library class explicitly. Excludes `Path:` (no
    # preceding dot/colon) since that shape is a function PARAMETER named
    # "Path" being type-annotated (`fromTextureAtlas(Path:String)`, a
    # real flxanimate signature using capitalized argument names) rather
    # than a reference to the Path class — the class is only ever used as
    # `Path.method(...)`, `new Path(...)`, or after its own colon as a
    # type annotation (`x:Path`), never immediately followed by a colon.
    (r"(?<!\.)(?<!class )\bPath\b(?!\s*:)", "haxe.io.Path"),
    # FlxG.error(...) isn't a real flixel API — the actual method is
    # FlxG.log.error(...). Psych's CoolUtil.hx calls the former directly;
    # this may be a latent bug in Psych's own source that HTML5's build
    # happens to surface. Redirect it to the real API rather than leave
    # a call to a nonexistent method.
    (r"FlxG\.error\(", "FlxG.log.error("),
    # cpp.vm.Gc.memInfo64(cpp.vm.Gc.MEM_INFO_USAGE) is native-only memory
    # introspection with no HTML5 equivalent. Matched here, BEFORE the
    # generic cpp.* rewrite below runs, since that generic rule only
    # strips one dotted path segment at a time (cpp.vm -> Dynamic) and
    # would leave a dangling, still-broken `Dynamic.Gc.memInfo64(...)`
    # rather than something that compiles. Replaced with a safe literal
    # since there's no meaningful HTML5 equivalent for native GC stats.
    (r"cpp\.vm\.Gc\.memInfo64\(cpp\.vm\.Gc\.MEM_INFO_USAGE\)", "0"),
    (r"sys\.thread\.Thread", "Thread"),
    (r"sys\.thread\.Mutex", "Mutex"),
    (r"sys\.thread\.FixedThreadPool", "ThreadPool"),
    (r"sys\.thread\.IThreadPool", "ThreadPool"),
    (r"sys\.thread\.ElasticThreadPool", "ThreadPool"),
    # Bare (unqualified) references to the same classes, e.g. after an
    # `import sys.thread.FixedThreadPool` (already rewritten above to
    # `import ThreadPool` by the pattern before this one) or a wildcard
    # `import sys.thread.*`. Word-boundary anchored so this only matches
    # the exact identifier, not other identifiers that merely contain it.
    (r"\bFixedThreadPool\b", "ThreadPool"),
    (r"\bIThreadPool\b", "ThreadPool"),
    (r"\bElasticThreadPool\b", "ThreadPool"),
    (r"^\s*import cpp[^\n]*\n", ""),
    (r"cpp\.[a-zA-Z0-9_]+", "Dynamic"),
    # Fallback for the same Gc pattern in case the original wasn't
    # exactly cpp.vm.Gc (unconfirmed — inferred from the already-rewritten
    # form seen in a compile error) and the primary pattern above missed
    # it, leaving the generic cpp.* rule's dangling Dynamic.Gc output.
    (r"Dynamic\.Gc\.memInfo64\(Dynamic\.Gc\.MEM_INFO_USAGE\)", "0"),
    (r"^\s*import llua[^\n]*\n", ""),
    (r"llua\.[a-zA-Z0-9_]+", "Dynamic"),
]


def write_stubs(engine_dir):
    for relpath, content in STUBS.items():
        fullpath = os.path.join(engine_dir, relpath)
        os.makedirs(os.path.dirname(fullpath), exist_ok=True)
        with open(fullpath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Wrote stub: {relpath}")


def strip_discord_from_project_xml(engine_dir):
    proj = os.path.join(engine_dir, "Project.xml")
    if not os.path.isfile(proj):
        print("Project.xml not found, skipping discord-rpc strip.")
        return
    with open(proj, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    kept = [
        line for line in lines
        if "discord_rpc" not in line and "discord-rpc" not in line and "linc_luajit" not in line
    ]
    with open(proj, "w", encoding="utf-8") as f:
        f.writelines(kept)
    print(f"Project.xml: removed {len(lines) - len(kept)} discord/luajit reference line(s).")


def rewrite_source_tree(engine_dir, extra_dirs=None):
    """
    Rewrites native-only API references in engine_dir, and optionally in
    additional directories (e.g. installed haxelibs under ~/haxelib) where
    a dependency's own source may reference a native-only class like
    sys.thread.FixedThreadPool without a proper #if target.threaded guard.
    Haxelib-installed sources are normally never edited, but since these
    are ephemeral CI runner installs (not the user's own environment) and
    the alternative is an unbuildable HTML5 target, patching them in place
    here is safe and contained to this run.
    """
    search_dirs = [engine_dir] + (extra_dirs or [])
    total_changed = 0
    total_files = 0
    for base_dir in search_dirs:
        if not os.path.isdir(base_dir):
            continue
        hx_files = glob.glob(os.path.join(base_dir, "**", "*.hx"), recursive=True)
        changed = 0
        for path in hx_files:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            original = text
            for pattern, repl in REGEX_REWRITES:
                flags = re.MULTILINE if pattern.startswith("^") else 0
                text = re.sub(pattern, repl, text, flags=flags)
            if text != original:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                changed += 1
        print(f"[{base_dir}] Rewrote native-API references in {changed} of {len(hx_files)} .hx files.")
        total_changed += changed
        total_files += len(hx_files)
    print(f"Total: rewrote {total_changed} of {total_files} .hx files across all scanned directories.")


def patch_flx_sound_tray(engine_dir):
    matches = glob.glob(os.path.join(engine_dir, "**", "FlxSoundTray.hx"), recursive=True)
    for path in matches:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if "showIncrement" in text:
            continue  # already patched
        patched = re.sub(
            r"(public function new\()",
            "public function showIncrement() { show(); } "
            "public function showDecrement() { show(); }\n\n\\1",
            text,
            count=1,
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(patched)
        print(f"Patched FlxSoundTray at {path}")


# Psych Engine's own source/backend/Discord.hx directly references
# hxdiscord_rpc's native-binding types (DiscordRichPresence,
# DiscordEventHandlers, etc.) as concrete fields/instance variables, not
# just inside conditional-compilation blocks. Stubbing those individual
# types to match the real library's exact API shape has proven fragile —
# hxdiscord_rpc is a hxcpp @:native extern binding, and its precise type
# shape (typedefs vs classes, required static factories, struct layout)
# isn't reliably discoverable without a native compiler to test against.
# Since Discord RPC is categorically impossible in a browser regardless of
# how well it's stubbed, the more robust fix is to replace this file
# entirely with a minimal implementation exposing the same public API the
# rest of the engine calls (per Main.hx/PlayState.hx usage:
# DiscordClient.shutdown(), DiscordClient.changePresence(...),
# DiscordClient.initialize()) as no-ops, rather than attempting to satisfy
# the real native binding's internal type references at all.
DISCORD_CLIENT_REPLACEMENT = """\
package backend;

// Replaced for HTML5 builds: Discord Rich Presence is a native desktop
// IPC feature (hxdiscord_rpc binds to the local Discord client over a
// native protocol) with no browser equivalent. This class keeps the same
// public API the rest of the engine calls so nothing else needs to change,
// but every method is a no-op. Uses Dynamic for the Lua callback
// parameter rather than a Lua-specific state type, since this file also
// passes through the same native-API rewrite pass as the rest of the
// source tree, which strips Lua-binding imports; Dynamic avoids
// depending on an import that would not survive that pass, and the
// parameter is unused here regardless.
class DiscordClient {
  public static function initialize():Void {}
  public static function shutdown():Void {}
  public static function changePresence(details:String, ?state:String, ?smallImageKey:String,
      ?hasStartTimestamp:Bool, ?endTimestamp:Float):Void {}
  public static function resetClientID():Void {}
  public static function check():Void {}
  public static function prepare():Void {}

  #if LUA_ALLOWED
  public static function addLuaCallbacks(lua:Dynamic):Void {}
  #end
}
"""


def replace_discord_client(engine_dir):
    """
    Finds source/backend/Discord.hx (the conventional Psych location) and
    any similarly-named file elsewhere in the tree, and replaces its
    content with a minimal no-op DiscordClient implementation. Only
    touches files that actually reference hxdiscord_rpc, so forks that
    don't use native Discord RPC at all are left untouched.

    Explicitly excludes anything already under a hxdiscord_rpc/ package
    directory (i.e. our own generated stubs in write_stubs), since those
    are a different, unrelated Discord.hx-named file that must NOT be
    overwritten by this — write_stubs already gives them correct content,
    and this function's job is only the engine's own backend/Discord.hx
    wrapper class.
    """
    candidates = glob.glob(os.path.join(engine_dir, "**", "Discord.hx"), recursive=True)
    candidates = [
        p for p in candidates
        if os.path.basename(os.path.dirname(p)) != "hxdiscord_rpc"
    ]
    replaced = 0
    for path in candidates:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if "hxdiscord_rpc" not in text and "discord_rpc" not in text:
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(DISCORD_CLIENT_REPLACEMENT)
        print(f"Replaced {path} with a no-op DiscordClient (native Discord RPC has no HTML5 equivalent).")
        replaced += 1
    if replaced == 0:
        print("No Discord.hx referencing hxdiscord_rpc/discord_rpc found — nothing to replace.")


# flixel-addons' TransitionFade.hx (and other haxelib source) declares
# empty classes like `class RawGraphicDiagonalGradient extends BitmapData
# {}` purely to trigger OpenFL's @:autoBuild(AssetsMacro.embedBitmap())
# compile-time asset-embedding macro (the same mechanism used throughout
# OpenFL/Flixel for auto-embedding bundled images by naming convention,
# always in the shape `@:bitmap("path/to/image.png") class SomeRawGraphic
# extends BitmapData {}`). On this toolchain that macro crashes with an
# uncaught null-access exception while reading the target asset's bytes —
# reproduced identically whether the library is installed from the
# haxelib registry or fresh via `haxelib git` (i.e. with the complete,
# unmodified repository present), which rules out a missing/incomplete
# package as the cause, and reproduced on a minimal Context.addResource()
# test case unrelated to OpenFL entirely — this is a generic Haxe/hxcpp
# compiler-macro incompatibility on this toolchain, not something fixable
# by better packaging or a different asset.
#
# The fix keeps the REAL "extends BitmapData"/"extends Sound" inheritance
# (so nominal typing is satisfied everywhere flixel code requires a
# literal Class<BitmapData> — e.g. FlxGraphic.fromClass(), debug-tool
# cursors — which a structurally-similar-but-different stand-in class
# fails, since Haxe's typing is nominal here, and @:autoBuild metadata
# propagates down the FULL inheritance chain regardless of how many
# levels deep, so no indirect stand-in can dodge the crash while staying
# a real BitmapData/Sound), but strips the specific
# `@:bitmap("path.png")` / `@:sound("path.ogg")` metadata that triggers
# AssetsMacro's crashing compile-time embed, replacing the class body
# with a constructor that builds a valid placeholder at RUNTIME instead —
# BitmapData's own constructor (width, height, transparent, fillColor)
# creates real solid-color pixel data with no macro involvement at all,
# sidestepping the crash entirely rather than working around its
# consequences. The result is a plain placeholder (solid-color square /
# silent audio) instead of the real bundled asset — an acceptable
# tradeoff for what are exclusively debug-tool icons, preloader splash
# graphics, cursor icons, and a text-typing sound effect, not
# gameplay-critical content.
ASSET_MACRO_METADATA_PATTERN = re.compile(
    r'@:(bitmap|sound|file|font)\(\s*"[^"]*"\s*\)\s*\n\s*(?:private\s+)?class\s+(\w+)\s+extends\s+(BitmapData|Sound)\s*\{\}'
)


def _asset_macro_replacement(match):
    kind = match.group(1)  # "bitmap" or "sound"
    class_name = match.group(2)
    base = match.group(3)  # "BitmapData" or "Sound"
    if base == "BitmapData":
        return (
            f"class {class_name} extends BitmapData {{\n"
            f"  public function new(width:Int = 8, height:Int = 8, ?transparent:Bool, ?fillColor:Int, ?onLoad:Dynamic) {{\n"
            f"    super(width != null && width > 0 ? width : 8, height != null && height > 0 ? height : 8, "
            f"transparent == null ? true : transparent, fillColor == null ? 0xFFCCCCCC : fillColor);\n"
            f"  }}\n"
            f"}}"
        )
    else:
        return (
            f"class {class_name} extends Sound {{\n"
            f"  public function new(?stream:Dynamic, ?context:Dynamic) {{ super(); }}\n"
            f"}}"
        )


# Fallback pattern for any BitmapData-extending empty class that doesn't
# have the @:bitmap/@:sound metadata directly on the preceding line (e.g.
# blank lines in between, or no metadata at all — meaning there's nothing
# for ASSET_MACRO_METADATA_PATTERN to strip). Falls back to the
# StubBitmapData/StubSound stand-ins for those, which avoids the crash by
# not being a real BitmapData/Sound at all — accepting the tradeoff that
# strict Class<BitmapData> typing won't be satisfied for whatever rare
# case reaches this fallback, since avoiding the crash takes priority.
TRANSITION_RAW_GRAPHIC_PATTERN = re.compile(
    r"class\s+(\w+)\s+extends\s+BitmapData\s*\{\}"
)

ASSET_MACRO_SOUND_PATTERN = re.compile(
    r"class\s+(\w+)\s+extends\s+Sound\s*\{\}"
)

# Directories under a haxelib install that are safe to skip: test suites,
# samples, and docs sometimes contain their own throwaway BitmapData
# subclasses that aren't part of what actually gets compiled, and
# skipping them keeps this patch's output focused on files that matter.
_SKIP_DIR_NAMES = {"test", "tests", "samples", "sample", "demo", "demos", "docs", "documentation"}


def patch_asset_macro_bitmapdata_classes(haxelib_dir):
    """
    Scans every .hx file across the whole haxelib install directory and
    neutralizes classes that trigger OpenFL's crashing AssetsMacro
    auto-embed, regardless of which library declares them. Tries the
    metadata-stripping approach first (ASSET_MACRO_METADATA_PATTERN,
    preserves real BitmapData/Sound inheritance so nominal typing still
    works everywhere), and falls back to the stand-in-class approach
    (StubBitmapData/StubSound) for any occurrence that pattern doesn't
    match. See the module-level comment above for the full rationale.
    """
    if not haxelib_dir or not os.path.isdir(haxelib_dir):
        return

    hx_files = glob.glob(os.path.join(haxelib_dir, "**", "*.hx"), recursive=True)
    total_patched_files = 0
    total_patched_classes = 0
    for path in hx_files:
        parts = os.path.normpath(path).split(os.sep)
        if any(p.lower() in _SKIP_DIR_NAMES for p in parts):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if "extends BitmapData" not in text and "extends Sound" not in text:
            continue
        file_class_count = 0

        # Primary approach: strip @:bitmap/@:sound metadata, keep real
        # inheritance, runtime-construct a placeholder instead.
        text, count = ASSET_MACRO_METADATA_PATTERN.subn(_asset_macro_replacement, text)
        file_class_count += count

        # Fallback: anything still matching the bare empty-class pattern
        # (no metadata line directly above it for the primary pattern to
        # have caught) goes to the stand-in-class approach.
        if "extends BitmapData" in text:
            text, count = TRANSITION_RAW_GRAPHIC_PATTERN.subn(
                lambda m: f"class {m.group(1)} extends psychporter.compat.StubBitmapData {{}}",
                text,
            )
            file_class_count += count
        if "extends Sound" in text:
            text, count = ASSET_MACRO_SOUND_PATTERN.subn(
                lambda m: f"class {m.group(1)} extends psychporter.compat.StubSound {{}}",
                text,
            )
            file_class_count += count

        if file_class_count == 0:
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Patched {path}: neutralized {file_class_count} AssetsMacro-triggering class(es) "
              f"to prevent the AssetsMacro null-access crash.")
        total_patched_files += 1
        total_patched_classes += file_class_count

    if total_patched_files == 0:
        print("No BitmapData/Sound auto-embed pattern found anywhere under the haxelib directory — nothing to patch.")
    else:
        print(f"AssetsMacro patch: {total_patched_classes} class(es) across {total_patched_files} file(s).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-dir", required=True)
    parser.add_argument(
        "--haxelib-dir",
        default=None,
        help="Optional path to the haxelib repository (e.g. ~/haxelib) to "
             "also scan/rewrite for native-only API references that a "
             "dependency's own source may use without a target guard. "
             "Safe to point at the whole haxelib cache: these specific "
             "classes (Thread/Mutex/FixedThreadPool/etc.) are unavailable "
             "on HTML5 regardless, so there's no correctly-working HTML5 "
             "code path this rewrite could break — and any occurrence "
             "inside a proper #if cpp/#if sys guard is never reached by "
             "an HTML5 compile anyway, so rewriting it there is inert.",
    )
    args = parser.parse_args()

    write_stubs(args.engine_dir)
    strip_discord_from_project_xml(args.engine_dir)
    replace_discord_client(args.engine_dir)

    extra_dirs = []
    if args.haxelib_dir:
        expanded = os.path.expanduser(args.haxelib_dir)
        if os.path.isdir(expanded):
            extra_dirs.append(expanded)
        else:
            print(f"::warning::--haxelib-dir given ({args.haxelib_dir}) but not found on disk, skipping.")

    rewrite_source_tree(args.engine_dir, extra_dirs=extra_dirs)
    patch_flx_sound_tray(args.engine_dir)

    if extra_dirs:
        patch_asset_macro_bitmapdata_classes(extra_dirs[0])

    print("Engine patching complete.")


if __name__ == "__main__":
    main()
