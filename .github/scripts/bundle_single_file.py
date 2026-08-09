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
  var errorCounts = Object.create(null);
  var MAX_DISTINCT_ERRORS = 20;

  // Deduplicates by exact message text with a running occurrence count,
  // rather than pushing every occurrence individually — a single
  // systemic failure (e.g. every one of 262 embedded assets hitting the
  // same underlying bug) would otherwise bury the one distinct, useful
  // message under 260+ near-identical repeats, making the overlay
  // unreadable right when it matters most.
  function pushError(text) {
    if (errorCounts[text]) {
      errorCounts[text]++;
      return;
    }
    if (errors.length >= MAX_DISTINCT_ERRORS) return;
    errorCounts[text] = 1;
    errors.push(text);
  }

  function renderOverlay() {
    var existing = document.getElementById("__psych_error_overlay__");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "__psych_error_overlay__";
    overlay.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;z-index:999999;" +
      "background:rgba(10,10,15,0.96);color:#f5f5f5;font-family:monospace;font-size:13px;" +
      "padding:16px;overflow:auto;box-sizing:border-box;";

    var totalOccurrences = 0;
    for (var key in errorCounts) if (errorCounts.hasOwnProperty(key)) totalOccurrences += errorCounts[key];

    var title = document.createElement("div");
    title.textContent = "Build failed to start — " + errors.length + " distinct error(s), " +
      totalOccurrences + " total occurrence(s)";
    title.style.cssText = "font-size:16px;font-weight:bold;margin-bottom:10px;color:#ff6b6b;";
    overlay.appendChild(title);

    var hint = document.createElement("div");
    hint.textContent = "Copy the text below and share it to diagnose the build.";
    hint.style.cssText = "margin-bottom:10px;color:#aaa;";
    overlay.appendChild(hint);

    var textArea = document.createElement("textarea");
    textArea.readOnly = true;
    textArea.value = errors.map(function (text) {
      var count = errorCounts[text];
      return (count > 1 ? "[x" + count + "] " : "") + text;
    }).join("\n\n---\n\n");
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
    pushError(formatError(event.message, event.filename, event.lineno, event.colno, event.error));
    renderOverlay();
  });

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    var message = (reason && reason.message) ? reason.message : String(reason);
    pushError(formatError("Unhandled promise rejection: " + message, null, null, null, reason));
    renderOverlay();
  });

  // Exposed for explicit try/catch blocks elsewhere in the bundle (e.g.
  // around the lime.embed bootstrap call) to report a caught exception
  // directly, with full detail intact. This bypasses the browser's
  // same-origin error-sanitization entirely — that sanitization only
  // applies to the window.onerror global handler above, not to an error
  // object a script already has in hand from its own try/catch. This is
  // the reliable path when the page is opened as a file:// URL, which
  // browsers treat as an opaque origin and reduces window.onerror to a
  // content-free "Script error." with no message, source, or stack.
  window.__psychReportError = function (error) {
    var message = (error && error.message) ? error.message : String(error);
    pushError(formatError("Caught during boot: " + message, null, null, null, error));
    renderOverlay();
  };
})();
"""


SHIM_JS = r"""
// --- Single-file asset resolution shim -------------------------------
// Redirects XHR/fetch requests for build-relative paths to the inline
// base64 manifest embedded above, so the compiled Lime/OpenFL runtime
// (which expects to fetch files like "assets/songs/foo.ogg" over HTTP)
// works with zero external files.
//
// LIVE STATUS READOUT: a small on-screen counter (bottom-left) tracks how
// many embedded-asset requests have been opened vs. completed
// successfully vs. failed. This exists because a stuck-at-0% preloader
// with no error overlay is genuinely ambiguous — it could mean asset
// loads are silently hanging, OR it could mean they're succeeding fine
// but Lime's own progress-percentage display just isn't being driven by
// this shim's blob: URL loads the way it expects. This readout answers
// that directly instead of requiring another guess-and-rebuild cycle.
window.__psychAssetStats = { opened: 0, loaded: 0, failed: 0 };
(function () {
  var statsEl = null;
  function renderStats() {
    if (!statsEl) {
      statsEl = document.createElement("div");
      statsEl.style.cssText = "position:fixed;bottom:0;left:0;z-index:999998;" +
        "background:rgba(0,0,0,0.7);color:#0f0;font-family:monospace;font-size:11px;" +
        "padding:4px 8px;pointer-events:none;";
      document.body.appendChild(statsEl);
    }
    var s = window.__psychAssetStats;
    statsEl.textContent = "assets: opened=" + s.opened + " loaded=" + s.loaded + " failed=" + s.failed +
      (s.lastProgress ? " | progress: " + s.lastProgress : "") +
      (s.lastReadyState4Status !== undefined ? " | status@rs4: " + s.lastReadyState4Status : "");
  }
  window.__psychRenderAssetStats = renderStats;
})();

//
// MEMORY DESIGN NOTE: each asset's base64 data: URI string is decoded
// into a Blob LAZILY, on first request, then cached and the original
// base64 string is discarded from the manifest immediately afterward.
// The original design kept every asset's full base64 string resident in
// memory for the entire page lifetime AND re-decoded it from scratch on
// every single request (even repeat requests for the same asset) — on
// iOS Safari specifically, holding many large strings plus repeated
// decode-allocation churn is a documented trigger for the OS's Jetsam
// process force-killing/reloading the tab under memory pressure, with no
// catchable JS error at all when that happens (confirmed against a real
// build: identical bytes produced a normal, detailed JS error on one
// test and 150+ content-free "Script error." messages — consistent with
// a partial page teardown — on another, purely as a function of runtime
// memory conditions). Converting to a Blob once, and dropping the
// string afterward, means peak memory reflects roughly the decoded
// asset size once, not the base64 string size held indefinitely plus
// repeated re-decode allocations on top.
(function () {
  var blobCache = Object.create(null);

  function resolve(url) {
    if (!url) return null;
    var candidates = [];
    var raw = String(url);
    candidates.push(raw);

    // Strip a single leading "./" or "/" (original behavior).
    candidates.push(raw.replace(/^\.?\//, ""));

    // Strip query string / fragment from every candidate so far.
    var stripped = candidates.map(function (c) {
      return c.split("?")[0].split("#")[0];
    });
    candidates = candidates.concat(stripped);

    // If this resolves against a real or synthetic base URL (e.g. inside
    // an about:blank page opened via document.write(), where relative
    // paths can pick up an unexpected absolute prefix like
    // "about:blank/assets/..." or "blob:.../assets/..."), take just
    // everything from the first recognizable top-level asset folder
    // name onward, so odd/unexpected prefixes don't prevent a match.
    var topLevelMatch = raw.match(/(?:^|[\/:])((?:assets|flixel|mods)\/.+)$/);
    if (topLevelMatch) candidates.push(topLevelMatch[1]);

    // Strip any leading "../" segments repeatedly (handles paths that
    // walk up from a nested request context).
    candidates.push(raw.replace(/^(\.\.\/)+/, "").replace(/^\.?\//, ""));

    for (var i = 0; i < candidates.length; i++) {
      var clean = candidates[i];
      if (!clean) continue;
      if (blobCache[clean]) return { key: clean, blob: blobCache[clean] };
      if (window.__EMBEDDED_ASSETS__.hasOwnProperty(clean)) {
        return { key: clean, dataUri: window.__EMBEDDED_ASSETS__[clean] };
      }
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

  // Resolves a manifest match down to an actual Blob, decoding and
  // caching on first use, then freeing the base64 string from the
  // manifest so it isn't held in memory twice (as a string AND as
  // decoded Blob bytes) for the rest of the page's lifetime.
  function getBlob(match) {
    if (match.blob) return match.blob;
    var blob = dataUriToBlob(match.dataUri);
    blobCache[match.key] = blob;
    delete window.__EMBEDDED_ASSETS__[match.key];
    return blob;
  }

  var OrigXHR = window.XMLHttpRequest;
  function ShimXHR() {
    var xhr = new OrigXHR();
    var origOpen = xhr.open;
    xhr.open = function (method, url) {
      var embedded = resolve(url);
      if (embedded) {
        // Open against a genuine blob: URL for the already-decoded Blob,
        // and let the REAL native XHR machinery handle everything else
        // (send, readyState transitions, status, response) rather than
        // manually faking each of those properties one at a time — that
        // approach kept failing at a new spot each time a different
        // internal browser state was involved (readyState after open(),
        // then setRequestHeader's own internal "was this really opened"
        // tracking separate from the public readyState property). Three
        // placeholder URL schemes were tried and rejected before this:
        //   1. The raw relative asset path itself: native open() throws
        //      "SyntaxError: The string did not match the expected
        //      pattern" — not resolvable to a valid URL inside a
        //      document.write()-constructed page with no real
        //      base/server behind it.
        //   2. Skipping open() and faking xhr.readyState via
        //      Object.defineProperty: the browser's real internal
        //      "was open() genuinely called" state is tracked
        //      separately from the public property, so the very next
        //      call (setRequestHeader) threw "InvalidStateError".
        //   3. window.location.href as a syntactically-valid
        //      placeholder: inside this bundle's context that resolves
        //      to the literal string "about:blank", and "about" is a
        //      scheme XHR/fetch implementations don't support for
        //      actually establishing a request — this reproduced the
        //      exact same failure 100% of the time rather than
        //      intermittently, consistent with a scheme-level rejection
        //      rather than a memory-pressure-driven one.
        // blob: is the one scheme purpose-built for exactly this: a
        // same-origin, genuinely fetchable reference to in-memory binary
        // data, which is precisely what an already-decoded embedded
        // asset is.
        //
        // ROOT CAUSE OF THE STUCK-AT-0% PRELOADER (found by reading the
        // actual compiled engine JS directly): Lime's own
        // HTML5HTTPRequest.__loadText/__loadBinary success check is
        // `status >= 200 && status < 400 || (validStatus0 && status ==
        // 0)` — and validStatus0 is set via
        // `new EReg("Tizen","gi").match(navigator.userAgent)`, i.e. it's
        // ONLY true on Samsung Tizen TV browsers, a narrow platform
        // workaround, not a general one. blob: URLs universally report
        // status 0 in every browser (this is standard, unavoidable
        // browser behavior — not something fixable from this shim by
        // choosing a "better" blob configuration). So on any non-Tizen
        // browser, EVERY embedded asset load that reaches this check
        // fails Lime's own success test and is silently routed to
        // promise.error(...) instead of promise.complete(...) — even
        // though the browser genuinely, successfully loaded the data
        // (confirmed separately: opened/loaded counts and progress
        // event byte counts were both perfect). This is why nothing
        // ever advanced past the preloader despite every diagnostic
        // showing successful loads: the failure was happening entirely
        // inside Lime's own compiled success-check, invisible to any
        // shim-level diagnostic that only observes the XHR layer itself.
        // Fix: override the `status` property specifically for embedded
        // asset requests to read as 200 rather than the blob: URL's
        // native 0, satisfying Lime's check without needing Lime's code
        // to change at all.
        var blob = getBlob(embedded);
        var blobUrl = URL.createObjectURL(blob);
        this.__isEmbedded = true;
        this.__embeddedBlobUrl = blobUrl;
        try {
          // Get-based override rather than a plain value, and applied
          // to BOTH this instance and (defensively) checked again right
          // before send() below — in case some browser-internal
          // lifecycle step re-touches the property between open() and
          // when Lime's own readystatechange handler reads it, which a
          // one-time value-based override at open()-time wouldn't
          // survive. A getter re-asserts 200 on every single read,
          // regardless of when that read happens.
          Object.defineProperty(this, "status", {
            get: function () { return 200; },
            configurable: true
          });
        } catch (__psychStatusErr) {
          if (window.__psychReportError) window.__psychReportError(__psychStatusErr);
        }
        window.__psychAssetStats.opened++;
        if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
        return origOpen.call(this, method, blobUrl, true);
      }
      return origOpen.apply(this, arguments);
    };
    var origSend = xhr.send;
    xhr.send = function () {
      if (this.__isEmbedded) {
        // Real native send() against the real blob: URL — no manual
        // property faking needed, the browser handles the actual load
        // and fires the normal onload/onreadystatechange events itself.
        // Revoke the blob URL once the request completes to avoid
        // accumulating unreleased object URLs over many asset loads.
        var self = this;
        var blobUrl = this.__embeddedBlobUrl;
        this.addEventListener("loadend", function () {
          try { URL.revokeObjectURL(blobUrl); } catch (e) {}
        });
        this.addEventListener("load", function () {
          window.__psychAssetStats.loaded++;
          if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
        });
        // Diagnostic: directly mirror the exact check Lime's own
        // compiled HTML5HTTPRequest.__loadText/__loadBinary performs
        // (readyState === 4, then reads .status) to see definitively
        // whether the status override is actually visible at the same
        // point Lime's own code reads it, or whether something resets
        // it / a different object identity is involved in practice
        // (despite the override being verified sound in isolation).
        this.addEventListener("readystatechange", function () {
          if (self.readyState === 4) {
            window.__psychAssetStats.lastReadyState4Status = self.status;
            if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
          }
        });
        // Diagnostic: capture what a real "progress" event reports for a
        // blob: URL load — this is very likely what Lime's own
        // HTTPRequest/Preloader classes read to compute bytesLoaded/
        // bytesTotal and drive the visible percentage. If
        // lengthComputable is false or total is 0 here, that's almost
        // certainly why the preloader stays frozen at 0% even though
        // every individual asset genuinely loads successfully (confirmed
        // separately via opened/loaded/failed counts above).
        var progressLogged = false;
        this.addEventListener("progress", function (ev) {
          if (progressLogged) return;
          progressLogged = true;
          window.__psychAssetStats.lastProgress =
            "lengthComputable=" + ev.lengthComputable + " loaded=" + ev.loaded + " total=" + ev.total;
          if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
        });
        // Diagnostic: a failed blob: URL load fires a native "error"
        // event, NOT a throwable JS exception — so it would never reach
        // try/catch-based reporting (__psychReportError), and would only
        // ever surface via the generic window.onerror path, which is
        // exactly the content-free "Script error." sanitization this is
        // meant to get past. Report it explicitly and directly here
        // instead, with as much real detail as the event/xhr object
        // exposes (status, readyState — genuine values here since
        // they're read from a real completed/failed native request, not
        // a faked property).
        this.addEventListener("error", function () {
          window.__psychAssetStats.failed++;
          if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
          if (window.__psychReportError) {
            window.__psychReportError(new Error(
              "XHR native error event for embedded asset blob URL. " +
              "status=" + self.status + " readyState=" + self.readyState +
              " blobUrl=" + blobUrl
            ));
          }
        });
        this.addEventListener("abort", function () {
          window.__psychAssetStats.failed++;
          if (window.__psychRenderAssetStats) window.__psychRenderAssetStats();
          if (window.__psychReportError) {
            window.__psychReportError(new Error(
              "XHR aborted for embedded asset blob URL. blobUrl=" + blobUrl
            ));
          }
        });
        return origSend.apply(this, arguments);
      }
      return origSend.apply(this, arguments);
    };
    return xhr;
  }
  window.XMLHttpRequest = ShimXHR;

  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      var url = typeof input === "string" ? input : input.url;
      var embedded = resolve(url);
      if (embedded) {
        var blob = getBlob(embedded);
        return Promise.resolve(new Response(blob));
      }
    } catch (__psychShimErr3) {
      if (window.__psychReportError) window.__psychReportError(__psychShimErr3);
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
        # Wrapped in try/catch rather than left to window.onerror: when
        # this HTML is opened directly from disk (a file:// URL, the most
        # common way someone opens a single downloaded HTML file) rather
        # than served over http(s), browsers treat the page as an opaque
        # origin for error-reporting purposes the same way they treat a
        # genuine cross-origin <script src>, and window.onerror is
        # reduced to a content-free "Script error." with no message, no
        # source location, and no stack — observed directly on a real
        # build. A local try/catch is NOT subject to that suppression
        # (it's not going through the onerror same-origin check at all),
        # so it reliably surfaces the real error to the overlay above
        # regardless of how the file is being opened.
        inline_block += (
            "<script>\ntry {\n"
            + bootstrap_js
            + "\n} catch (__psychBootErr) {\n"
            + "  if (window.__psychReportError) window.__psychReportError(__psychBootErr);\n"
            + "  else throw __psychBootErr;\n"
            + "}\n</script>\n"
        )

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
