#!/usr/bin/env python3
"""
Nexus Consulting Group — Migration Validation Report
Compares source CSV against migrated monday.com boards.
Requires migration_manifest.json produced by migrate.py.
"""

import os, csv, json
from datetime import datetime
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN    = os.environ['MONDAY_API_TOKEN']
API_URL  = 'https://api.monday.com/v2'
CSV_PATH = os.path.join(os.path.dirname(__file__), 'nexus_smartsheet_export.csv')

ENG_STATUS_MAP = {
    'Not Started': 'Not started', 'In Progress': 'Active',
    'Active':      'Active',      'On Hold':     'On hold',
    'Complete':    'Complete',    'Done':         'Complete',
}
DEL_STATUS_MAP = {
    'To Do':         'To do',       'Not Started':  'To do',
    'In Progress':   'In progress', 'Working on it':'In progress',
    'In Review':     'In review',   'Done':          'Done',
}

VALID_ENG_STATUSES = set(ENG_STATUS_MAP.values())
VALID_DEL_STATUSES = set(DEL_STATUS_MAP.values())


# ── API ───────────────────────────────────────────────────────────────────────
def gql(query: str, variables: dict = None) -> dict:
    headers = {
        'Authorization': TOKEN,
        'Content-Type':  'application/json',
        'API-Version':   '2024-01',
    }
    r = requests.post(API_URL, json={'query': query, 'variables': variables or {}}, headers=headers)
    r.raise_for_status()
    body = r.json()
    if 'errors' in body:
        raise RuntimeError(json.dumps(body['errors'], indent=2))
    return body['data']


def fetch_items(board_id: str) -> list:
    """Fetch all items from a board (handles up to 500 items via cursor pagination)."""
    items, cursor = [], None
    while True:
        cursor_arg = f', cursor: "{cursor}"' if cursor else ''
        data = gql(f'''
            {{
              boards(ids: [{board_id}]) {{
                items_page(limit: 100{cursor_arg}) {{
                  cursor
                  items {{
                    id name
                    column_values {{
                      id text value
                    }}
                  }}
                }}
              }}
            }}
        ''')
        page   = data['boards'][0]['items_page']
        items += page['items']
        cursor  = page.get('cursor')
        if not cursor:
            break
    return items


def col_map(item: dict) -> dict:
    """Return {column_id: column_values_entry} for an item."""
    return {c['id']: c for c in item['column_values']}


def col_text(cm: dict, col_id: str) -> str:
    """Safely extract the text value of a column, returning '' for None/missing."""
    entry = cm.get(col_id) or {}
    return (entry.get('text') or '').strip()


# ── CSV ───────────────────────────────────────────────────────────────────────
def fmt_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), '%m/%d/%Y').strftime('%Y-%m-%d')


def load_csv_source() -> tuple:
    engagements: dict = {}
    deliverables: list = []

    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            eid = row['engagement_id']
            if eid not in engagements:
                engagements[eid] = {
                    'name':   row['engagement_name'],
                    'client': row['client'],
                    'lead':   row['engagement_lead'],
                    'status': ENG_STATUS_MAP[row['engagement_status'].strip()],
                    'budget': int(row['budget']),
                }
            deliverables.append({
                'id':           row['deliverable_id'],
                'name':         row['deliverable_name'],
                'engagement_id': eid,
                'assignee':     row['assignee'],
                'due_date':     fmt_date(row['due_date']),
                'priority':     row['priority'],
                'status':       DEL_STATUS_MAP[row['deliverable_status'].strip()],
                'hours':        int(row['hours_estimated']),
            })

    return engagements, deliverables


# ── Checks ────────────────────────────────────────────────────────────────────
def check(label: str, passed: bool, expected=None, actual=None, detail: list = None) -> dict:
    result = {'check': label, 'pass': passed}
    if expected is not None:
        result['expected'] = expected
        result['actual']   = actual
    if detail:
        result['detail'] = detail
    return result


def run_validation(manifest_path: str = None) -> dict:
    if manifest_path is None:
        manifest_path = os.path.join(os.path.dirname(__file__), 'migration_manifest.json')

    with open(manifest_path) as f:
        manifest = json.load(f)

    eng_board_id = manifest['eng_board_id']
    del_board_id = manifest['del_board_id']
    eng_cols     = manifest['eng_cols']
    del_cols     = manifest['del_cols']

    print('Fetching source data…')
    src_engagements, src_deliverables = load_csv_source()

    print('Fetching migrated Engagements from monday.com…')
    eng_items = fetch_items(eng_board_id)
    print('Fetching migrated Deliverables from monday.com…')
    del_items = fetch_items(del_board_id)

    checks = []

    # ── 1. Row counts ─────────────────────────────────────────────────────────
    checks.append(check(
        'Deliverable row count',
        len(del_items) == len(src_deliverables),
        expected=len(src_deliverables),
        actual=len(del_items),
    ))
    checks.append(check(
        'Engagement count',
        len(eng_items) == len(src_engagements),
        expected=len(src_engagements),
        actual=len(eng_items),
    ))

    # ── 2. Orphan deliverables (no engagement name in text reference column) ──
    orphans = []
    for item in del_items:
        cm = col_map(item)
        if not col_text(cm, del_cols['engagement']):
            orphans.append(item['name'])
    checks.append(check('No orphan deliverables', not orphans, detail=orphans or None))

    # ── 3. Engagement status values canonical ─────────────────────────────────
    bad_eng_status = []
    for item in eng_items:
        cm = col_map(item)
        s = col_text(cm, eng_cols['status'])
        if s and s not in VALID_ENG_STATUSES:
            bad_eng_status.append(f'{item["name"]}: "{s}"')
    checks.append(check('Engagement statuses canonical', not bad_eng_status, detail=bad_eng_status or None))

    # ── 4. Deliverable status values canonical ────────────────────────────────
    bad_del_status = []
    for item in del_items:
        cm = col_map(item)
        s = col_text(cm, del_cols['status'])
        if s and s not in VALID_DEL_STATUSES:
            bad_del_status.append(f'{item["name"]}: "{s}"')
    checks.append(check('Deliverable statuses canonical', not bad_del_status, detail=bad_del_status or None))

    # ── 5. Missing assignee or due date ───────────────────────────────────────
    missing_data = []
    for item in del_items:
        cm     = col_map(item)
        issues = []
        if not col_text(cm, del_cols['assignee']):
            issues.append('missing assignee')
        if not col_text(cm, del_cols['due_date']):
            issues.append('missing due date')
        if issues:
            missing_data.append(f'{item["name"]}: {", ".join(issues)}')
    checks.append(check('No missing assignee or due date', not missing_data, detail=missing_data or None))

    # ── 6. Engagement field consistency ──────────────────────────────────────
    # Verify that all rows sharing an engagement_id in the CSV agreed on key fields.
    # (Checks source data integrity — flags if Smartsheet export had inconsistencies.)
    eng_consistency_issues = []
    seen: dict = {}
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            eid = row['engagement_id']
            snapshot = (row['engagement_name'], row['client'], row['engagement_lead'],
                        row['engagement_start'], row['engagement_end'], row['budget'])
            if eid in seen:
                if seen[eid] != snapshot:
                    eng_consistency_issues.append(f'{eid}: conflicting engagement fields across rows')
            else:
                seen[eid] = snapshot
    checks.append(check('Engagement fields consistent across rows', not eng_consistency_issues,
                         detail=eng_consistency_issues or None))

    # ── 7. People column note ─────────────────────────────────────────────────
    # Consultant names are not provisioned as monday.com users in this account.
    # They are stored in text columns; People columns require user provisioning.
    consultant_names = sorted({d['assignee'] for d in src_deliverables} |
                               {e['lead'] for e in src_engagements.values()})
    checks.append({
        'check': 'People fields (advisory)',
        'pass':  True,
        'detail': [
            f'Stored as text — {len(consultant_names)} consultants require monday.com user '
            f'provisioning before People column assignment: {", ".join(consultant_names)}'
        ],
    })

    # ── Report ────────────────────────────────────────────────────────────────
    passed = sum(1 for c in checks if c['pass'])
    total  = len(checks)

    print()
    print('=' * 60)
    print('NEXUS MIGRATION VALIDATION REPORT')
    print(f'Run: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'Engagements board : {eng_board_id}')
    print(f'Deliverables board: {del_board_id}')
    print('=' * 60)

    for c in checks:
        icon = '✓' if c['pass'] else '✗'
        line = f'{icon}  {c["check"]}'
        if 'expected' in c:
            line += f' (expected {c["expected"]}, got {c["actual"]})'
        print(line)
        if c.get('detail'):
            for d in c['detail'][:5]:
                print(f'     • {d}')

    print()
    print(f'Result: {passed}/{total} checks passed')

    report = {
        'timestamp':         datetime.now().isoformat(),
        'eng_board_id':      eng_board_id,
        'del_board_id':      del_board_id,
        'summary':           {'passed': passed, 'total': total},
        'checks':            checks,
    }
    report_path = os.path.join(os.path.dirname(__file__), 'validation_report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'Full report → validation_report.json')

    return report


if __name__ == '__main__':
    run_validation()
