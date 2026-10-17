#!/usr/bin/env python3
"""Split index-automation.html into code (for the admin app) and data (for S3).

The Internal Admin app hosts this dashboard at /dashboards/registrations. It
serves the page with every embedded data block EMPTY, and the page fetches each
block's JSON from S3 through short-lived signed URLs the admin app hands it:

    block                 S3 key (bucket = the warehouse export bucket)
    dashboard-data        dashboards/registrations/dashboard-data.json   manual
    extra-data            dashboards/registrations/extra-data.json       manual
    app-dashboard-data    dashboards/registrations/app-dashboard.json    manual
    ieo-dashboard-data    reports/v1/latest/ieo-dashboard.json           NIGHTLY

The mapping is owned by the admin app (apps/api/src/lib/server/dashboards/
config.ts); MANUAL below must agree with it.

Two jobs, either or both:

  1. Page code changed -> regenerate the admin app's copy, then commit/deploy
     the admin app:
       python3 admin_page.py --page-out ../isha-internal-admin/apps/web/public/embedded/registrations-dashboard.html

  2. Manual numbers changed (you re-embedded them with your usual scripts) ->
     extract the manual blocks and upload them. No deploy needed:
       python3 admin_page.py --data-out /tmp/registrations
       aws s3 cp /tmp/registrations/ s3://$BUCKET/dashboards/registrations/ \\
           --recursive --content-type application/json

The IEO block is never extracted: the nightly export owns
reports/v1/latest/ieo-dashboard.json, and a hand upload would only be
overwritten (or, worse, mistaken for the nightly).
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# block id -> file name under dashboards/registrations/ (hand-uploaded blocks only)
MANUAL = {
    'dashboard-data': 'dashboard-data.json',
    'extra-data': 'extra-data.json',
    'app-dashboard-data': 'app-dashboard.json',
}
NIGHTLY = {'ieo-dashboard-data'}
DEFERRED_TYPE = 'text/x-dashboard-deferred'

JSON_BLOCK = re.compile(
    r'(<script id="([a-z-]+)" type="application/json">)(.*?)(</script>)', re.S)
INLINE_SCRIPT = re.compile(r'<script>(.*?)</script>', re.S)

# Runs last. Asks the hosting admin page for signed URLs, fetches every block,
# fills the empty <script type="application/json"> elements, then runs the
# page's own scripts in their original order — they were parked as inert
# DEFERRED_TYPE scripts so they cannot start before their data exists.
LOADER = r"""
<script>
(function () {
  "use strict";
  var DEFERRED = "%(deferred)s";
  var started = false;

  function notice(title, lines, isError) {
    var box = document.getElementById("admin-loader-notice");
    if (!box) {
      box = document.createElement("div");
      box.id = "admin-loader-notice";
      box.style.cssText = "margin:24px;padding:16px 20px;border-radius:10px;font:14px/1.5 system-ui,sans-serif;";
      var host = document.getElementById("content") || document.body;
      host.parentNode.insertBefore(box, host);
    }
    box.style.border = isError ? "1px solid #d9534f" : "1px solid #ccc";
    box.style.background = isError ? "#fdf2f2" : "transparent";
    box.innerHTML = "";
    var h = document.createElement("strong");
    h.textContent = title;
    box.appendChild(h);
    (lines || []).forEach(function (l) {
      var p = document.createElement("div");
      p.textContent = l;
      box.appendChild(p);
    });
  }

  function clearNotice() {
    var box = document.getElementById("admin-loader-notice");
    if (box) box.parentNode.removeChild(box);
  }

  function runDeferred() {
    var parked = document.querySelectorAll('script[type="' + DEFERRED + '"]');
    for (var i = 0; i < parked.length; i++) {
      var live = document.createElement("script");
      live.text = parked[i].text;
      parked[i].parentNode.replaceChild(live, parked[i]);
    }
  }

  function fetchBlock(src) {
    return fetch(src.url, { cache: "no-store" }).then(
      function (res) {
        if (!res.ok) {
          // Without s3:ListBucket a missing object answers 403, not 404.
          throw new Error(res.status === 403 || res.status === 404
            ? "not uploaded yet (HTTP " + res.status + ")"
            : "HTTP " + res.status);
        }
        return res.text().then(function (text) { return { src: src, text: text }; });
      },
      function () {
        throw new Error("network or CORS failure — is the bucket's CORS rule set for this origin?");
      }
    ).catch(function (err) { return { src: src, error: err.message }; });
  }

  function load(sources) {
    notice("Loading dashboard data…", [], false);
    Promise.all(sources.map(fetchBlock)).then(function (results) {
      var fatal = results.filter(function (r) { return r.error && r.src.required; });
      if (fatal.length) {
        notice("The dashboard data could not be loaded", fatal.map(function (r) {
          return r.src.key + ": " + r.error;
        }), true);
        return;
      }
      results.forEach(function (r) {
        var el = document.getElementById(r.src.block);
        if (el && !r.error) el.textContent = r.text;
        if (r.error && window.console) console.warn("[dashboard] " + r.src.key + ": " + r.error);
      });
      clearNotice();
      runDeferred();
    });
  }

  if (window.parent === window) {
    notice("Open this dashboard from the Internal Admin app",
           ["Its data is loaded by the admin app (Dashboards → Program Registrations)."], true);
    return;
  }
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || e.source !== window.parent) return;
    if (!e.data || e.data.type !== "dashboard-sources" || started) return;
    started = true;
    load(e.data.sources || []);
  });
  window.parent.postMessage({ type: "dashboard-ready" }, location.origin);
})();
</script>
""" % {'deferred': DEFERRED_TYPE}


def blocks_of(html):
    return {m.group(2): m.group(3) for m in JSON_BLOCK.finditer(html)}


def build_page(html):
    found = set(blocks_of(html))
    expected = set(MANUAL) | NIGHTLY
    if found != expected:
        raise SystemExit(f'page blocks {sorted(found)} do not match the admin mapping '
                         f'{sorted(expected)} — update MANUAL/NIGHTLY here and '
                         'config.ts in the admin app together')
    page = JSON_BLOCK.sub(lambda m: m.group(1) + m.group(4), html)
    page, parked = INLINE_SCRIPT.subn(
        lambda m: f'<script type="{DEFERRED_TYPE}">{m.group(1)}</script>', page)
    if not parked:
        raise SystemExit('no inline <script> found to defer — has the page structure changed?')
    # The page's own hint points at the embedding script, which is not how the
    # admin copy gets its data.
    page = page.replace('Re-run update_page_data.py.',
                        'Check that its JSON has been published to S3.')
    # The page has no </body>; the loader just has to come after everything it fills.
    if '</body>' in page:
        return page.replace('</body>', LOADER + '</body>', 1), parked
    return page.rstrip() + '\n' + LOADER, parked


def extract_data(html, out_dir):
    blocks = blocks_of(html)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for block, name in MANUAL.items():
        text = blocks.get(block, '').strip()
        if not text:
            raise SystemExit(f'block #{block} is empty in the page — nothing to upload')
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f'block #{block} is not valid JSON: {exc}')
        path = out_dir / name
        path.write_text(text + '\n', encoding='utf-8')
        written.append(path)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', type=Path, default=HERE / 'index-automation.html')
    ap.add_argument('--page-out', type=Path, help='write the data-free admin page here')
    ap.add_argument('--data-out', type=Path, help='write the manual blocks as JSON files here')
    args = ap.parse_args()
    if not (args.page_out or args.data_out):
        ap.error('give --page-out, --data-out or both')

    html = args.page.read_text(encoding='utf-8')
    if args.page_out:
        page, parked = build_page(html)
        args.page_out.parent.mkdir(parents=True, exist_ok=True)
        args.page_out.write_text(page, encoding='utf-8')
        print(f'wrote {args.page_out} ({len(page):,} bytes, {parked} scripts deferred)')
    if args.data_out:
        for path in extract_data(html, args.data_out):
            print(f'wrote {path} ({path.stat().st_size:,} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
