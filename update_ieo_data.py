#!/usr/bin/env python3
"""Embed a fresh warehouse IEO export into index.html.

The page is standalone: the IEO data lives inside index.html in
<script id="ieo-dashboard-data" type="application/json">. This replaces the
contents of that one block and touches nothing else.

Usage:
    python3 update_ieo_data.py /path/to/latest/ieo-dashboard.json
"""
import json
import re
import sys
from pathlib import Path

PAGE = Path(__file__).resolve().parent / 'index.html'
BLOCK = re.compile(
    r'(<script id="ieo-dashboard-data" type="application/json">)(.*?)(</script>)',
    re.S)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    report = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    for key in ('provenance', 'progress', 'languages',
                'completionsMonthly', 'completionsWeekly'):
        if key not in report:
            print(f'not an ieo_dashboard export: missing {key!r}')
            return 1
    # "</" would end the <script> block early; "<\/" means the same thing in JSON.
    payload = json.dumps(report, ensure_ascii=False).replace('</', '<\\/')

    html = PAGE.read_text(encoding='utf-8')
    if len(BLOCK.findall(html)) != 1:
        print('index.html must contain exactly one ieo-dashboard-data block')
        return 1
    html = BLOCK.sub(lambda m: m.group(1) + payload + m.group(3), html)
    PAGE.write_text(html, encoding='utf-8')
    p = report['provenance']
    print(f"embedded IEO data generated {p['generatedAt']} "
          f"(dbt run {p['dbtRunId']}, rows {p['rowCounts']})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
