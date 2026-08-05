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
    "source/FileSystem.hx": """\
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
    "source/File.hx": """\
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
    "source/hxdiscord_rpc/Discord.hx": """\
package hxdiscord_rpc;
class Discord {
  public static function Initialize(a:String,b:Bool,c:Dynamic){}
  public static function Shutdown(){}
  public static function RunCallbacks(){}
  public static function UpdatePresence(a:Dynamic){}
  public static function ClearPresence(){}
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
}

REGEX_REWRITES = [
    (r"sys\.FileSystem", "FileSystem"),
    (r"sys\.io\.File", "File"),
    (r"sys\.io\.Process", "Process"),
    (r"sys\.thread\.Thread", "Thread"),
    (r"sys\.thread\.Mutex", "Mutex"),
    (r"^\s*import cpp[^\n]*\n", ""),
    (r"cpp\.[a-zA-Z0-9_]+", "Dynamic"),
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


def rewrite_source_tree(engine_dir):
    hx_files = glob.glob(os.path.join(engine_dir, "**", "*.hx"), recursive=True)
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
    print(f"Rewrote native-API references in {changed} of {len(hx_files)} .hx files.")


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-dir", required=True)
    args = parser.parse_args()

    write_stubs(args.engine_dir)
    strip_discord_from_project_xml(args.engine_dir)
    rewrite_source_tree(args.engine_dir)
    patch_flx_sound_tray(args.engine_dir)

    print("Engine patching complete.")


if __name__ == "__main__":
    main()
