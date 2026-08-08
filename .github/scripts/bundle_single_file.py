#!/usr/bin/env python3
"""
bundle_single_file.py

Takes a compiled Lime/OpenFL HTML5 build directory (index.html + a .js file +
an assets/ folder, typically) and inlines everything into ONE standalone
.html file: JS as an inline <script>, and every asset referenced by the
build as a base64 data: URI, patched into the virtual filesystem the
Lime/OpenFL HTML5 target expects.

Reports the result via GITHUB_OUTPUT (final_size_bytes, fits_in_repo) so
the calling workflow can choose how to deliver the file: committed
directly to the repo if it fits under GitHub's ~100MB git-push limit, or
uploaded as a GitHub Release asset (up to ~2GB) if it doesn't. Only exits
non-zero if the file exceeds even the Release asset ceiling, since at that
point there's no GitHub-native way to deliver a single file that large.
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


ERROR_OVERLAY_JS = r"""
// --- On-page error overlay --------------------------------------------
// Installed as early as possible (before the asset shim and engine code)
// so it catches failures at any point during boot, including errors that
// happen before the game canvas ever appears. Shows a readable panel
// with the error, stack trace, and a copy button — for when DevTools
// isn't available (mobile browsers, some kiosk/embedded contexts, or
// someone who just doesn't have it open) but the actual error text is
// still needed to diagnose a build.
(function () {
  var errors = [];

  function renderOverlay() {
    var existing = document.getElementById("__psych_error_overlay__");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "__psych_error_overlay__";
    overlay.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;z-index:999999;" +
      "background:rgba(10,10,15,0.96);color:#f5f5f5;font-family:monospace;font-size:13px;" +
      "padding:16px;overflow:auto;box-sizing:border-box;";

    var title = document.createElement("div");
    title.textContent = "Build failed to start — " + errors.length + " error(s) captured";
    title.style.cssText = "font-size:16px;font-weight:bold;margin-bottom:10px;color:#ff6b6b;";
    overlay.appendChild(title);

    var hint = document.createElement("div");
    hint.textContent = "Copy the text below and share it to diagnose the build.";
    hint.style.cssText = "margin-bottom:10px;color:#aaa;";
    overlay.appendChild(hint);

    var textArea = document.createElement("textarea");
    textArea.readOnly = true;
    textArea.value = errors.join("\n\n---\n\n");
    textArea.style.cssText = "width:100%;height:60%;background:#1a1a1f;color:#e0e0e0;" +
      "border:1px solid #444;padding:8px;box-sizing:border-box;white-space:pre;font-family:monospace;";
    overlay.appendChild(textArea);

    var copyBtn = document.createElement("button");
    copyBtn.textContent = "Copy to clipboard";
    copyBtn.style.cssText = "margin-top:10px;padding:8px 16px;cursor:pointer;";
    copyBtn.onclick = function () {
      textArea.select();
      try {
        document.execCommand("copy");
        copyBtn.textContent = "Copied!";
        setTimeout(function () { copyBtn.textContent = "Copy to clipboard"; }, 1500);
      } catch (e) {
        if (navigator.clipboard) navigator.clipboard.writeText(textArea.value);
      }
    };
    overlay.appendChild(copyBtn);

    document.body.appendChild(overlay);
  }

  function formatError(message, source, lineno, colno, error) {
    var lines = ["Message: " + message];
    if (source) lines.push("Source: " + source + (lineno ? (":" + lineno + (colno ? ":" + colno : "")) : ""));
    if (error && error.stack) lines.push("Stack:\n" + error.stack);
    return lines.join("\n");
  }

  window.addEventListener("error", function (event) {
    errors.push(formatError(event.message, event.filename, event.lineno, event.colno, event.error));
    renderOverlay();
  });

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    var message = (reason && reason.message) ? reason.message : String(reason);
    errors.push(formatError("Unhandled promise rejection: " + message, null, null, null, reason));
    renderOverlay();
  });
})();
"""


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
    parser.add_argument("--max-release-bytes", type=int, default=1_950_000_000)
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

    # The original index.html also contains INLINE <script> blocks with no
    # src attribute — critically, the lime.embed(...) bootstrap call that
    # actually starts the game. That call depends on globals (lime,
    # openfl, etc.) defined by the compiled engine JS. In the original
    # build, script execution order guaranteed the engine JS file loaded
    # before this inline block ran. Appending the engine JS at the end of
    # <body> (below) while leaving this inline block in its original,
    # earlier position breaks that ordering: the embed call would fire
    # before the engine exists to be embedded, silently doing nothing —
    # this is what produced a black screen with no visible error. Extract
    # every remaining inline <script> block (preserving relative order)
    # and move them to run AFTER the engine code instead of wherever they
    # originally sat in the document.
    inline_script_pattern = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    extracted_inline_scripts = inline_script_pattern.findall(html)
    html = inline_script_pattern.sub("", html)
    bootstrap_js = "\n".join(s.strip() for s in extracted_inline_scripts if s.strip())
    if extracted_inline_scripts:
        print(f"Deferred {len(extracted_inline_scripts)} inline <script> block(s) "
              f"(e.g. the lime.embed bootstrap call) to run after the engine code, "
              f"preserving their original relative order.")

    inline_block = (
        "<script>\n"
        + manifest_js
        + SHIM_JS
        + "\n</script>\n"
        + "<script>\n"
        + main_js
        + "\n</script>\n"
    )
    if bootstrap_js:
        inline_block += "<script>\n" + bootstrap_js + "\n</script>\n"

    error_overlay_block = "<script>\n" + ERROR_OVERLAY_JS + "\n</script>\n"
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n" + error_overlay_block, 1)
    elif "<html>" in html:
        html = html.replace("<html>", "<html>\n" + error_overlay_block, 1)
    else:
        html = error_overlay_block + html

    if "</body>" in html:
        html = html.replace("</body>", inline_block + "</body>")
    else:
        html += inline_block

    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    final_size = os.path.getsize(output_path)
    print(f"Final standalone HTML size: {human(final_size)} ({final_size:,} bytes)")

    # Surface the result to the calling workflow step via GITHUB_OUTPUT so
    # it can decide how to deliver the file: a normal git push works fine
    # under the limit, but GitHub rejects git pushes of files over 100MB
    # outright — for anything over that (but still under the 2GB GitHub
    # Release asset ceiling), the workflow falls back to uploading the
    # file as a Release asset instead of committing it to the repo.
    github_output = os.environ.get("GITHUB_OUTPUT")
    fits_in_repo = final_size <= args.max_bytes
    fits_in_release = final_size <= args.max_release_bytes

    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"final_size_bytes={final_size}\n")
            f.write(f"fits_in_repo={'true' if fits_in_repo else 'false'}\n")

    if fits_in_repo:
        print("Size OK — within GitHub's single-file push limit, will be committed directly to the repo.")
    elif fits_in_release:
        over_by = final_size - args.max_bytes
        print("::warning::" + (
            f"Standalone HTML is {human(final_size)}, which exceeds the {human(args.max_bytes)} "
            f"repo push limit by {human(over_by)}. GitHub rejects a normal git push of files over "
            f"100MB, so this build cannot be committed directly to the repo. The file was still "
            f"built successfully and will be uploaded as a GitHub Release asset instead (supports "
            f"up to 2GB) in the next step."
        ))
    else:
        over_by = final_size - args.max_release_bytes
        print("::error::" + (
            f"Standalone HTML is {human(final_size)}, which exceeds even the {human(args.max_release_bytes)} "
            f"GitHub Release asset limit by {human(over_by)}. This mod is too large to deliver as a "
            f"single file through any GitHub-native mechanism — consider a smaller mod/week, or a "
            f"non-single-file hosting approach (index.html + separate asset files, or external "
            f"storage such as itch.io)."
        ))
        sys.exit(1)


if __name__ == "__main__":
    main()
