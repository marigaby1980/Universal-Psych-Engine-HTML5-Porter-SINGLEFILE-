#!/usr/bin/env python3
"""
bundle_single_file.py

Takes a compiled Lime/OpenFL HTML5 build directory (index.html + a .js file +
an assets/ folder, typically) and inlines everything into ONE standalone
.html file: JS as an inline <script>, and every asset referenced by the
build as a base64 data: URI, patched into the virtual filesystem the
Lime/OpenFL HTML5 target expects.

Hard-fails (non-zero exit) if the resulting file would exceed --max-bytes,
which defaults to just under GitHub's 100MB per-file push limit. This is
intentional: a mod that produces a single-file HTML larger than that limit
literally cannot be pushed to a GitHub repo via a normal commit, so silently
producing an oversized file would just move the failure downstream and
make it more confusing.
"""

import argparse
import base64
import mimetypes
import os
import re
import sys
import json


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def guess_mime(path):
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    # Common game-asset extensions mimetypes sometimes misses
    ext = os.path.splitext(path)[1].lower()
    return {
        ".ogg": "audio/ogg",
        ".xml": "application/xml",
        ".txt": "text/plain",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")


def find_main_js(build_dir):
    candidates = [
        f for f in os.listdir(build_dir)
        if f.endswith(".js") and "howler" not in f.lower() and "pako" not in f.lower()
    ]
    if not candidates:
        raise SystemExit("::error::No main JS file found in build output — compile step likely failed upstream.")
    # Prefer the largest .js file — that's virtually always the actual
    # engine/game bundle, not a small helper/vendor script.
    candidates.sort(key=lambda f: os.path.getsize(os.path.join(build_dir, f)), reverse=True)
    return candidates[0]


def collect_assets(build_dir):
    """
    Walk the build dir for everything that isn't the html/js/css we're
    inlining directly, and return {relative_path: absolute_path}.
    """
    assets = {}
    skip_names = {"index.html"}
    for root, _dirs, files in os.walk(build_dir):
        for fname in files:
            abspath = os.path.join(root, fname)
            relpath = os.path.relpath(abspath, build_dir).replace(os.sep, "/")
            if fname in skip_names:
                continue
            if fname.endswith(".js") or fname.endswith(".css"):
                continue
            assets[relpath] = abspath
    return assets


def build_asset_manifest_js(assets):
    """
    Emit a JS object mapping virtual paths -> base64 data URIs, plus a small
    shim that intercepts the runtime's asset-fetching so it resolves against
    this in-memory manifest instead of doing network fetches for files that
    no longer exist on disk (since everything is now inline).
    """
    entries = []
    total_encoded = 0
    for relpath, abspath in sorted(assets.items()):
        with open(abspath, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        mime = guess_mime(relpath)
        uri = f"data:{mime};base64,{b64}"
        total_encoded += len(uri)
        # JSON-encode the key to safely handle any path characters
        entries.append(f"{json.dumps(relpath)}:{json.dumps(uri)}")

    manifest_js = "window.__EMBEDDED_ASSETS__ = {\n  " + ",\n  ".join(entries) + "\n};\n"
    return manifest_js, total_encoded


SHIM_JS = r"""
// --- Single-file asset resolution shim -------------------------------
// Redirects XHR/fetch requests for build-relative paths to the inline
// base64 manifest embedded above, so the compiled Lime/OpenFL runtime
// (which expects to fetch files like "assets/songs/foo.ogg" over HTTP)
// works with zero external files.
(function () {
  function resolve(url) {
    if (!url) return null;
    var clean = url.replace(/^\.?\//, "").split("?")[0].split("#")[0];
    if (window.__EMBEDDED_ASSETS__.hasOwnProperty(clean)) {
      return window.__EMBEDDED_ASSETS__[clean];
    }
    return null;
  }

  function dataUriToBlob(dataUri) {
    var parts = dataUri.split(",");
    var meta = parts[0];
    var isBase64 = meta.indexOf("base64") !== -1;
    var mime = meta.split(":")[1].split(";")[0];
    var raw = isBase64 ? atob(parts[1]) : decodeURIComponent(parts[1]);
    var arr = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return new Blob([arr], { type: mime });
  }

  var OrigXHR = window.XMLHttpRequest;
  function ShimXHR() {
    var xhr = new OrigXHR();
    var origOpen = xhr.open;
    xhr.open = function (method, url) {
      var embedded = resolve(url);
      if (embedded) {
        this.__embeddedData = embedded;
        this.__isEmbedded = true;
      }
      return origOpen.apply(this, arguments);
    };
    var origSend = xhr.send;
    xhr.send = function () {
      if (this.__isEmbedded) {
        var self = this;
        var blob = dataUriToBlob(this.__embeddedData);
        setTimeout(function () {
          var reader = new FileReader();
          reader.onload = function () {
            Object.defineProperty(self, "response", { value: reader.result, configurable: true });
            Object.defineProperty(self, "responseText", { value: "", configurable: true });
            Object.defineProperty(self, "status", { value: 200, configurable: true });
            Object.defineProperty(self, "readyState", { value: 4, configurable: true });
            if (self.onreadystatechange) self.onreadystatechange();
            if (self.onload) self.onload();
          };
          if (self.responseType === "arraybuffer" || self.responseType === "blob") {
            reader.readAsArrayBuffer(blob);
          } else {
            reader.readAsText(blob);
          }
        }, 0);
        return;
      }
      return origSend.apply(this, arguments);
    };
    return xhr;
  }
  window.XMLHttpRequest = ShimXHR;

  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input.url;
    var embedded = resolve(url);
    if (embedded) {
      return origFetch(embedded, init);
    }
    return origFetch.apply(this, arguments);
  };
})();
// -----------------------------------------------------------------------
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-bytes", type=int, default=99_000_000)
    args = parser.parse_args()

    build_dir = args.build_dir
    index_path = os.path.join(build_dir, "index.html")
    if not os.path.isfile(index_path):
        raise SystemExit("::error::index.html not found in build output.")

    with open(index_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    main_js_name = find_main_js(build_dir)
    with open(os.path.join(build_dir, main_js_name), "r", encoding="utf-8", errors="replace") as f:
        main_js = f.read()

    print(f"Main engine JS: {main_js_name} ({human(os.path.getsize(os.path.join(build_dir, main_js_name)))})")

    assets = collect_assets(build_dir)
    print(f"Found {len(assets)} asset files to inline.")

    manifest_js, encoded_total = build_asset_manifest_js(assets)
    print(f"Encoded asset payload: ~{human(encoded_total)}")

    # Strip existing <script src="...">/<link rel="stylesheet" href="...">
    # tags that point at now-inlined files, and any other <script src>.
    html = re.sub(r'<script[^>]+src=["\'][^"\']+["\'][^>]*></script>', "", html, flags=re.IGNORECASE)
    html = re.sub(r'<link[^>]+rel=["\']stylesheet["\'][^>]*>', "", html, flags=re.IGNORECASE)

    inline_block = (
        "<script>\n"
        + manifest_js
        + SHIM_JS
        + "\n</script>\n"
        + "<script>\n"
        + main_js
        + "\n</script>\n"
    )

    if "</body>" in html:
        html = html.replace("</body>", inline_block + "</body>")
    else:
        html += inline_block

    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    final_size = os.path.getsize(output_path)
    print(f"Final standalone HTML size: {human(final_size)} ({final_size:,} bytes)")

    if final_size > args.max_bytes:
        over_by = final_size - args.max_bytes
        print("::error::" + (
            f"Standalone HTML is {human(final_size)}, which exceeds the {human(args.max_bytes)} "
            f"limit by {human(over_by)}. GitHub rejects pushes of files over 100MB, so this build "
            f"cannot be committed as a single file. This mod is too large to produce as one "
            f"self-contained HTML — consider a smaller mod/week, or a non-single-file hosting "
            f"approach (index.html + separate asset files)."
        ))
        sys.exit(1)

    print("Size OK — within GitHub's single-file push limit.")


if __name__ == "__main__":
    main()
