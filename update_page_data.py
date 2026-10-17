#!/usr/bin/env python3
"""Embed warehouse report exports into the standalone dashboard page.

Each warehouse-fed tab has one embedded block in the page:
    <script id="<block>" type="application/json"> ...export JSON... </script>
This script replaces the contents of those blocks and touches nothing else.

Two ways to run it:

  1. From exports you already have (e.g. the nightly S3 copy, or a manual run):
       python3 update_page_data.py --from-dir /tmp/reports/latest

  2. Let it run the exporter first (needs the data-analysis-scripts repo and
     its .env / warehouse access):
       python3 update_page_data.py --export ../data-analysis-scripts

Options:
  --page FILE    page to update (default: index-automation.html next to this script)
  --only a,b     limit to these report names (default: every report in REPORTS)

Adding a tab later = one entry in REPORTS below, one embedded block and one
adapter in the page, and one entry in the page's WAREHOUSE_TABS list.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# report name (the manifest's `report:`) -> exported file, page block, required keys
REPORTS = {
    'ieo_dashboard': {
        'file': 'ieo-dashboard.json',
        'block': 'ieo-dashboard-data',
        'keys': ['provenance', 'progress', 'languages', 'completionsMonthly', 'completionsWeekly'],
    },
    'app_dashboard': {
        'file': 'app-dashboard.json',
        'block': 'app-dashboard-data',
        'keys': ['provenance', 'downloadsDaily', 'downloadsAllTime'],
    },
}


def block_re(block_id):
    return re.compile(
        r'(<script id="' + re.escape(block_id) + r'" type="application/json">)(.*?)(</script>)',
        re.S)


def run_exporter(repo: Path, names, out: Path) -> None:
    python = repo / '.venv' / 'bin' / 'python'
    python = str(python) if python.exists() else sys.executable
    for name in names:
        print(f'exporting {name} ...')
        r = subprocess.run([python, '-m', 'tools.export', '--report', name, '--out', str(out)],
                           cwd=repo, capture_output=True, text=True)
        sys.stdout.write(r.stdout[-1500:])
        if r.returncode != 0:
            sys.stderr.write(r.stderr[-3000:])
            raise SystemExit(f'exporter failed for {name} — nothing was embedded')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--from-dir', type=Path, help='directory holding the exported *.json (e.g. .../latest)')
    src.add_argument('--export', type=Path, help='path to the data-analysis-scripts repo; run the exporter first')
    ap.add_argument('--page', type=Path, default=HERE / 'index-automation.html')
    ap.add_argument('--only', default='', help='comma-separated report names')
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(',') if n.strip()] or list(REPORTS)
    unknown = [n for n in names if n not in REPORTS]
    if unknown:
        print(f'unknown report(s) {unknown}; known: {list(REPORTS)}')
        return 2

    tmp = None
    if args.export:
        tmp = tempfile.TemporaryDirectory()
        run_exporter(args.export.resolve(), names, Path(tmp.name))
        src_dir = Path(tmp.name) / 'latest'
    else:
        src_dir = args.from_dir

    html = args.page.read_text(encoding='utf-8')
    done = []
    # Validate everything first; write once at the end, so a bad file changes nothing.
    for name in names:
        cfg = REPORTS[name]
        path = src_dir / cfg['file']
        if not path.exists():
            print(f'{name}: {path} not found — skipped')
            continue
        report = json.loads(path.read_text(encoding='utf-8'))
        missing = [k for k in cfg['keys'] if k not in report]
        if missing:
            print(f'{name}: {path} is not a {name} export (missing {missing})')
            return 1
        pattern = block_re(cfg['block'])
        found = pattern.findall(html)
        if len(found) != 1:
            print(f'{name}: page must contain exactly one #{cfg["block"]} block, found {len(found)}')
            return 1
        old = found[0][1].strip()
        if old and old != '{}':
            try:
                old_at = json.loads(old.replace('<\\/', '</'))['provenance']['generatedAt']
                if old_at > report['provenance']['generatedAt']:
                    print(f'{name}: WARNING — replacing newer data ({old_at}) with older '
                          f'({report["provenance"]["generatedAt"]})')
            except (ValueError, KeyError):
                pass
        # "</" would end the <script> block early; "<\/" means the same in JSON.
        payload = json.dumps(report, ensure_ascii=False).replace('</', '<\\/')
        html = pattern.sub(lambda m, p=payload: m.group(1) + p + m.group(3), html)
        p = report['provenance']
        done.append(f"  {name:<15} generated {p['generatedAt']}  dbt {p['dbtRunId']}  rows {p['rowCounts']}")

    if not done:
        print('nothing embedded')
        return 1
    fd, tmp_page = tempfile.mkstemp(dir=args.page.parent, suffix='.html')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(html)
    os.replace(tmp_page, args.page)
    print(f'updated {args.page.name}:')
    print('\n'.join(done))
    return 0


if __name__ == '__main__':
    sys.exit(main())
