#!/usr/bin/env python3
"""
Nexus Consulting Group — Migration Validation
Produces: console output + validation_report.html + validation_report.json
"""

import os, csv, json, re
from collections import defaultdict
from datetime import datetime, date
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
    'Active': 'Active', 'On Hold': 'On hold',
    'Complete': 'Complete', 'Done': 'Complete',
}
DEL_STATUS_MAP = {
    'To Do': 'To do', 'Not Started': 'To do',
    'In Progress': 'In progress', 'Working on it': 'In progress',
    'In Review': 'In review', 'Done': 'Done',
}
VALID_ENG_STATUSES = set(ENG_STATUS_MAP.values())
VALID_DEL_STATUSES = set(DEL_STATUS_MAP.values())
DONE_STATUSES      = {'Done', 'Complete'}
VALID_PRIORITIES   = {'High', 'Medium', 'Low'}


def gql(query, variables=None):
    # No retry here — validate is read-only. A transient 429 should surface
    # immediately so the operator knows to wait before re-running the check.
    headers = {'Authorization': TOKEN, 'Content-Type': 'application/json', 'API-Version': '2024-01'}
    r = requests.post(API_URL, json={'query': query, 'variables': variables or {}}, headers=headers)
    r.raise_for_status()
    body = r.json()
    if 'errors' in body:
        raise RuntimeError(json.dumps(body['errors'], indent=2))
    return body['data']


def fetch_items(board_id):
    # monday.com caps item_page results at 100 per request. Cursor-based pagination
    # loops until the API returns no cursor, collecting all items regardless of board size.
    items, cursor = [], None
    while True:
        ca = f', cursor: "{cursor}"' if cursor else ''
        data = gql(f'{{ boards(ids: [{board_id}]) {{ items_page(limit: 100{ca}) '
                   f'{{ cursor items {{ id name column_values {{ id text value }} }} }} }} }}')
        page = data['boards'][0]['items_page']
        items += page['items']
        cursor = page.get('cursor')
        if not cursor:
            break
    return items


def col_text(item, col_id):
    for c in item['column_values']:
        if c['id'] == col_id:
            # API returns None (not '') for columns that have never been written to.
            return (c.get('text') or '').strip()
    return ''


def normalize_number(raw):
    # monday.com returns numbers as formatted strings ("$125,000"), while the CSV
    # stores raw integers. Strip currency symbols, commas, and spaces before comparing.
    try:
        return str(int(float(re.sub(r'[,$\s]', '', raw))))
    except (ValueError, TypeError):
        return raw.strip()


def fmt_date(raw):
    return datetime.strptime(raw.strip(), '%m/%d/%Y').strftime('%Y-%m-%d')


def load_csv():
    engagements, deliverables = {}, []
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            eid = row['engagement_id']
            if eid not in engagements:
                engagements[eid] = {
                    'id': eid, 'name': row['engagement_name'],
                    'client': row['client'], 'lead': row['engagement_lead'],
                    'start': fmt_date(row['engagement_start']),
                    'end': fmt_date(row['engagement_end']),
                    'budget': str(int(row['budget'])),
                    'status': ENG_STATUS_MAP[row['engagement_status'].strip()],
                }
            deliverables.append({
                'id': row['deliverable_id'], 'name': row['deliverable_name'],
                'engagement': row['engagement_name'], 'eng_id': eid,
                'assignee': row['assignee'], 'due_date': fmt_date(row['due_date']),
                'priority': row['priority'], 'hours': str(int(row['hours_estimated'])),
                'status': DEL_STATUS_MAP[row['deliverable_status'].strip()],
            })

    # Second pass: detect rows where the same engagement_id carries conflicting field
    # values — a source-data quality problem that would produce an unreliable migration.
    seen, inconsistencies = {}, []
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            eid = row['engagement_id']
            snap = (row['engagement_name'], row['client'], row['engagement_lead'],
                    row['engagement_start'], row['engagement_end'], row['budget'])
            if eid in seen and seen[eid] != snap:
                inconsistencies.append(eid)
            seen[eid] = snap

    return engagements, deliverables, inconsistencies


def run_validation(manifest_path=None):
    if manifest_path is None:
        manifest_path = os.path.join(os.path.dirname(__file__), 'migration_manifest.json')

    with open(manifest_path) as f:
        m = json.load(f)

    eng_board_id = m['eng_board_id']
    del_board_id = m['del_board_id']
    ec, dc = m['eng_cols'], m['del_cols']
    migrated_at = m.get('migrated_at', 'unknown')

    print('Fetching source data…')
    src_engs, src_dels, src_inconsistencies = load_csv()
    print('Fetching Engagements from monday.com…')
    eng_items = fetch_items(eng_board_id)
    print('Fetching Deliverables from monday.com…')
    del_items = fetch_items(del_board_id)

    live_engs = {i['name']: i for i in eng_items}
    live_dels = {i['name']: i for i in del_items}
    run_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    checks = []  # {section, label, pass, advisory, detail}
    rd     = {}  # aggregates and structured rows shared between console and HTML renderers

    def chk(section, label, passed, detail=None, advisory=False):
        # advisory=True: finding is informational and excluded from the pass-rate calculation.
        # Use for known non-errors (user provisioning reminders, etc.)
        checks.append({'section': section, 'label': label, 'pass': passed,
                       'advisory': advisory, 'detail': detail or []})

    # --- Section 1: Structural Integrity ---
    src_eng_names  = {e['name'] for e in src_engs.values()}
    src_del_names  = {d['name'] for d in src_dels}
    live_eng_names = {i['name'] for i in eng_items}
    live_del_names = {i['name'] for i in del_items}

    chk(1, f'Engagement count matches (expected {len(src_engs)}, live {len(eng_items)})',
        len(eng_items) == len(src_engs))
    chk(1, f'Deliverable count matches (expected {len(src_dels)}, live {len(del_items)})',
        len(del_items) == len(src_dels))

    missing_engs = src_eng_names - live_eng_names
    chk(1, f'All {len(src_engs)} CSV engagements present in monday.com',
        not missing_engs, [f'Missing: {n}' for n in sorted(missing_engs)])

    extra_engs = live_eng_names - src_eng_names
    chk(1, 'No extra engagements in monday.com (not in CSV)',
        not extra_engs, [f'Extra: {n}' for n in sorted(extra_engs)])

    missing_dels = src_del_names - live_del_names
    chk(1, f'All {len(src_dels)} CSV deliverables present in monday.com',
        not missing_dels, [f'Missing: {n}' for n in sorted(missing_dels)])

    extra_dels = live_del_names - src_del_names
    chk(1, 'No extra deliverables in monday.com (not in CSV)',
        not extra_dels, [f'Extra: {n}' for n in sorted(extra_dels)])

    dup_engs = [n for n, c in {i['name']: sum(1 for j in eng_items if j['name'] == i['name'])
                                for i in eng_items}.items() if c > 1]
    chk(1, 'No duplicate engagement names', not dup_engs, dup_engs)

    dup_dels = [n for n, c in {i['name']: sum(1 for j in del_items if j['name'] == i['name'])
                                for i in del_items}.items() if c > 1]
    chk(1, 'No duplicate deliverable names', not dup_dels, dup_dels)

    orphans = [i['name'] for i in del_items if not col_text(i, dc['engagement'])]
    chk(1, 'No orphan deliverables (all have engagement reference)', not orphans, orphans)

    # Confirm each deliverable's engagement reference names an actual engagement
    invalid_refs = [i['name'] for i in del_items
                    if col_text(i, dc['engagement']) and
                    col_text(i, dc['engagement']) not in src_eng_names]
    chk(1, 'All deliverable engagement references point to a valid engagement',
        not invalid_refs, invalid_refs)

    chk(1, 'Engagement fields consistent across all CSV rows',
        not src_inconsistencies, [f'Inconsistent: {e}' for e in src_inconsistencies])

    # --- Section 2: Status Validation ---
    bad_eng_s = [f'{i["name"]}: "{col_text(i, ec["status"])}"' for i in eng_items
                 if col_text(i, ec['status']) not in VALID_ENG_STATUSES]
    chk(2, f'All {len(eng_items)} engagement statuses are canonical', not bad_eng_s, bad_eng_s)

    bad_del_s = [f'{i["name"]}: "{col_text(i, dc["status"])}"' for i in del_items
                 if col_text(i, dc['status']) not in VALID_DEL_STATUSES]
    chk(2, f'All {len(del_items)} deliverable statuses are canonical', not bad_del_s, bad_del_s)

    live_eng_status = {i['name']: col_text(i, ec['status']) for i in eng_items}
    eng_s_mm = [f'{e["name"]}: expected "{e["status"]}", live "{live_eng_status.get(e["name"], "?")}"'
                for e in src_engs.values() if live_eng_status.get(e['name']) != e['status']]
    chk(2, f'Engagement statuses match CSV ({len(src_engs)-len(eng_s_mm)}/{len(src_engs)} correct)',
        not eng_s_mm, eng_s_mm)

    live_del_status = {i['name']: col_text(i, dc['status']) for i in del_items}
    del_s_mm = [f'{d["name"]}: expected "{d["status"]}", live "{live_del_status.get(d["name"], "?")}"'
                for d in src_dels if live_del_status.get(d['name']) != d['status']]
    chk(2, f'Deliverable statuses match CSV ({len(src_dels)-len(del_s_mm)}/{len(src_dels)} correct)',
        not del_s_mm, del_s_mm)

    eng_dist = {s: (sum(1 for e in src_engs.values() if e['status'] == s),
                    sum(1 for i in eng_items if col_text(i, ec['status']) == s))
                for s in sorted(VALID_ENG_STATUSES)}
    del_dist = {s: (sum(1 for d in src_dels if d['status'] == s),
                    sum(1 for i in del_items if col_text(i, dc['status']) == s))
                for s in sorted(VALID_DEL_STATUSES)}
    rd['eng_dist'] = eng_dist
    rd['del_dist'] = del_dist

    # --- Section 3: Field-Level Cross-Reference ---
    eng_field_defs = {
        'Engagement ID':   (ec['eng_id'], lambda e: e['id'],      str),
        'Client':          (ec['client'], lambda e: e['client'],   str),
        'Engagement Lead': (ec['lead'],   lambda e: e['lead'],     str),
        'Start Date':      (ec['start'],  lambda e: e['start'],    str),
        'End Date':        (ec['end'],    lambda e: e['end'],      str),
        'Budget ($)':      (ec['budget'], lambda e: e['budget'],   normalize_number),
    }
    del_field_defs = {
        'Deliverable ID': (dc['del_id'],     lambda d: d['id'],         str),
        'Assignee':       (dc['assignee'],   lambda d: d['assignee'],   str),
        'Due Date':       (dc['due_date'],   lambda d: d['due_date'],   str),
        'Priority':       (dc['priority'],   lambda d: d['priority'],   str),
        'Est. Hours':     (dc['hours'],      lambda d: d['hours'],      normalize_number),
        'Engagement':     (dc['engagement'], lambda d: d['engagement'], str),
    }

    total_fields = matched_fields = 0
    field_results = []

    for label, (col_id, src_fn, norm) in eng_field_defs.items():
        mm = []
        for e in src_engs.values():
            if e['name'] not in live_engs:
                continue
            sv, lv = src_fn(e), norm(col_text(live_engs[e['name']], col_id))
            total_fields += 1
            if sv == lv:
                matched_fields += 1
            else:
                mm.append(f'{e["name"]}: expected "{sv}", got "{lv}"')
        n = len(src_engs)
        field_results.append({'board': 'Engagements', 'field': label,
                               'matched': n - len(mm), 'total': n, 'mismatches': mm})
        chk(3, f'Engagement {label}: {n-len(mm)}/{n} match', not mm, mm)

    for label, (col_id, src_fn, norm) in del_field_defs.items():
        mm = []
        for d in src_dels:
            if d['name'] not in live_dels:
                continue
            sv, lv = src_fn(d), norm(col_text(live_dels[d['name']], col_id))
            total_fields += 1
            if sv == lv:
                matched_fields += 1
            else:
                mm.append(f'{d["name"]}: expected "{sv}", got "{lv}"')
        n = len(src_dels)
        field_results.append({'board': 'Deliverables', 'field': label,
                               'matched': n - len(mm), 'total': n, 'mismatches': mm})
        chk(3, f'Deliverable {label}: {n-len(mm)}/{n} match', not mm, mm)

    rd['field_results'] = field_results
    rd['total_fields'] = total_fields
    rd['matched_fields'] = matched_fields

    # --- Section 4: Engagement Drill-Down ---
    dels_by_eng = defaultdict(list)
    for d in src_dels:
        dels_by_eng[d['eng_id']].append(d)

    drilldown = []
    for eng in src_engs.values():
        eng_dels = dels_by_eng[eng['id']]
        live_s = live_eng_status.get(eng['name'], '?')
        deliverable_rows = []
        for d in eng_dels:
            live_ds = live_del_status.get(d['name'], '?')
            deliverable_rows.append({
                'name': d['name'], 'priority': d['priority'],
                'expected_status': d['status'], 'live_status': live_ds,
                'status_ok': live_ds == d['status'],
                'due_date': d['due_date'], 'assignee': d['assignee'],
                'hours': d['hours'],
            })
        drilldown.append({
            'id': eng['id'], 'name': eng['name'], 'client': eng['client'],
            'lead': eng['lead'], 'budget': eng['budget'], 'status': eng['status'],
            'live_status': live_s, 'status_ok': live_s == eng['status'],
            'start': eng['start'], 'end': eng['end'],
            'total': len(eng_dels),
            'done': sum(1 for d in eng_dels if d['status'] == 'Done'),
            'hours': sum(int(d['hours']) for d in eng_dels),
            'deliverables': deliverable_rows,
        })
    rd['drilldown'] = drilldown

    # --- Section 5: Data Quality ---
    no_client = [e['name'] for e in src_engs.values() if not e['client'].strip()]
    chk(5, 'No engagements missing client', not no_client, no_client)

    no_lead = [e['name'] for e in src_engs.values() if not e['lead'].strip()]
    chk(5, 'No engagements missing lead', not no_lead, no_lead)

    no_assignee = [d['name'] for d in src_dels if not d['assignee'].strip()]
    chk(5, 'No deliverables missing assignee', not no_assignee, no_assignee)

    no_due = [d['name'] for d in src_dels if not d['due_date'].strip()]
    chk(5, 'No deliverables missing due date', not no_due, no_due)

    bad_dates = [f'{e["name"]}: start {e["start"]} ≥ end {e["end"]}'
                 for e in src_engs.values() if e['start'] >= e['end']]
    chk(5, 'All engagement date ranges are valid (start before end)', not bad_dates, bad_dates)

    bad_priority = [f'{d["name"]}: "{d["priority"]}"'
                    for d in src_dels if d['priority'] not in VALID_PRIORITIES]
    chk(5, 'All deliverable priorities are valid (High / Medium / Low)',
        not bad_priority, bad_priority)

    total_budget = sum(int(e['budget']) for e in src_engs.values())
    total_hours  = sum(int(d['hours']) for d in src_dels)
    budget_by_status = defaultdict(int)
    for e in src_engs.values():
        budget_by_status[e['status']] += int(e['budget'])
    hours_by_person = defaultdict(int)
    for d in src_dels:
        hours_by_person[d['assignee']] += int(d['hours'])

    rd['total_budget']     = total_budget
    rd['total_hours']      = total_hours
    rd['budget_by_status'] = dict(sorted(budget_by_status.items()))
    rd['hours_by_person']  = dict(sorted(hours_by_person.items(), key=lambda x: -x[1]))

    # --- Section 6: People Provisioning ---
    # monday.com has no public API to verify user account existence. This advisory
    # lists every unique person from the data so the admin knows which accounts to
    # create before reassigning items from text fields to real monday.com users.
    all_people = sorted({d['assignee'] for d in src_dels} | {e['lead'] for e in src_engs.values()})
    chk(6, f'{len(all_people)} consultants require monday.com user provisioning',
        True, all_people, advisory=True)
    rd['people'] = all_people

    # Summary
    hard   = [c for c in checks if not c['advisory']]
    passed = sum(1 for c in hard if c['pass'])
    pct    = 100 * matched_fields // total_fields if total_fields else 0

    header = {
        'run_ts': run_ts, 'migrated_at': migrated_at[:19],
        'eng_board_id': eng_board_id, 'del_board_id': del_board_id,
        'src_engs': len(src_engs), 'src_dels': len(src_dels),
        'live_engs': len(eng_items), 'live_dels': len(del_items),
        'total_budget': total_budget, 'total_hours': total_hours,
    }
    summary = {
        'checks_passed': passed, 'checks_total': len(hard),
        'fields_matched': matched_fields, 'fields_total': total_fields,
        'field_pct': pct,
    }

    _print_console(header, checks, rd, summary)
    _write_html(header, checks, rd, summary)

    report = {'timestamp': run_ts, 'migrated_at': migrated_at,
              'eng_board_id': eng_board_id, 'del_board_id': del_board_id,
              'summary': summary, 'checks': checks}
    with open(os.path.join(os.path.dirname(__file__), 'validation_report.json'), 'w') as f:
        json.dump(report, f, indent=2)

    print('\n  Outputs written:')
    print('    validation_report.html')
    print('    validation_report.json\n')
    return report


def _print_console(h, checks, rd, s):
    W = 68
    TITLES = {
        1: 'STRUCTURAL INTEGRITY',
        2: 'STATUS VALIDATION',
        3: 'FIELD-LEVEL CROSS-REFERENCE',
        4: 'ENGAGEMENT DRILL-DOWN',
        5: 'DATA QUALITY FLAGS',
        6: 'PEOPLE PROVISIONING (advisory)',
    }

    print()
    print('=' * W)
    print('  NEXUS CONSULTING GROUP — MIGRATION VALIDATION REPORT')
    print('=' * W)
    print(f'  Report generated : {h["run_ts"]}')
    print(f'  Migration run    : {h["migrated_at"]}')
    print(f'  Source records   : {h["src_engs"]} engagements · {h["src_dels"]} deliverables')
    print(f'  Live records     : {h["live_engs"]} engagements · {h["live_dels"]} deliverables')
    print(f'  Total budget     : ${h["total_budget"]:,}  ·  Total hours: {h["total_hours"]} h')

    cur_section = None
    for c in checks:
        if c['section'] != cur_section:
            cur_section = c['section']
            print(f'\n{"─" * W}')
            print(f'  {cur_section} · {TITLES[cur_section]}')
            print(f'{"─" * W}')
        icon = '✓' if c['pass'] else ('⚠' if c['advisory'] else '✗')
        print(f'  {icon}  {c["label"]}')
        for d in c['detail'][:10]:
            print(f'       • {d}')
        if len(c['detail']) > 10:
            print(f'       … and {len(c["detail"]) - 10} more')

    print(f'\n{"─" * W}')
    print('  Status distributions')
    print(f'{"─" * W}')
    print('  Engagements:')
    for st, (src_n, live_n) in rd['eng_dist'].items():
        m = '✓' if src_n == live_n else '✗'
        print(f'    {m}  {st:<16}  CSV: {src_n}  Live: {live_n}')
    print('  Deliverables:')
    for st, (src_n, live_n) in rd['del_dist'].items():
        m = '✓' if src_n == live_n else '✗'
        print(f'    {m}  {st:<16}  CSV: {src_n}  Live: {live_n}')

    print(f'\n  Field accuracy: {s["fields_matched"]}/{s["fields_total"]} data points ({s["field_pct"]}%)')

    print(f'\n{"─" * W}')
    print('  Engagement drill-down')
    print(f'{"─" * W}')
    for eng in rd['drilldown']:
        sk = '✓' if eng['status_ok'] else '✗'
        print(f'\n  {eng["id"]}  {eng["name"]}')
        print(f'         Client: {eng["client"]}  ·  Lead: {eng["lead"]}')
        print(f'         Status: {sk} {eng["status"]}  ·  Budget: ${int(eng["budget"]):,}')
        print(f'         Period: {eng["start"]} → {eng["end"]}')
        print(f'         Deliverables: {eng["total"]} total  ·  {eng["done"]} done  ·  {eng["hours"]} h')
        for d in eng['deliverables']:
            ok = '✓' if d['status_ok'] else '✗'
            print(f'           {ok}  [{d["priority"]:<6}] {d["name"]:<42}'
                  f'  {d["live_status"]:<14}  {d["due_date"]}')

    print(f'\n{"─" * W}')
    print('  Budget reconciliation')
    print(f'{"─" * W}')
    for status, amt in rd['budget_by_status'].items():
        print(f'    {status:<16}  ${amt:>10,}')
    print(f'    {"TOTAL":<16}  ${rd["total_budget"]:>10,}')
    print(f'\n  Hours by assignee')
    for person, hrs in rd['hours_by_person'].items():
        print(f'    {person:<24}  {hrs:>4} h')
    print(f'    {"TOTAL":<24}  {rd["total_hours"]:>4} h')

    hard = [c for c in checks if not c['advisory']]
    passed = sum(1 for c in hard if c['pass'])
    failed = [c for c in hard if not c['pass']]
    print(f'\n{"=" * W}')
    print('  SUMMARY')
    print(f'{"=" * W}')
    print(f'  Checks passed   : {passed}/{len(hard)}')
    print(f'  Field accuracy  : {s["fields_matched"]}/{s["fields_total"]} ({s["field_pct"]}%)')
    print(f'  Budget verified : ${rd["total_budget"]:,}')
    print(f'  Hours verified  : {rd["total_hours"]} h')
    if failed:
        print('\n  Issues:')
        for c in failed:
            print(f'    ✗  {c["label"]}')
    else:
        print('\n  No issues found. Migration data integrity confirmed.')
    print('=' * W)


def _write_html(h, checks, rd, s):
    GREEN  = '#00a650'
    RED    = '#e2445c'
    ORANGE = '#fdab3d'
    GREY   = '#f5f6f8'
    DARK   = '#323338'

    def badge(passed, advisory=False):
        if passed:
            return f'<span style="color:{GREEN};font-weight:600">✓ Pass</span>'
        if advisory:
            return f'<span style="color:{ORANGE};font-weight:600">⚠ Advisory</span>'
        return f'<span style="color:{RED};font-weight:600">✗ Fail</span>'

    def row_bg(passed, advisory=False):
        if passed:
            return ''
        return 'background:#fff8f0' if advisory else 'background:#fff0f2'

    hard   = [c for c in checks if not c['advisory']]
    passed = sum(1 for c in hard if c['pass'])
    failed = [c for c in hard if not c['pass']]

    TITLES = {
        1: 'Structural Integrity',
        2: 'Status Validation',
        3: 'Field-Level Cross-Reference',
        4: 'Engagement Drill-Down',
        5: 'Data Quality Flags',
        6: 'People Provisioning',
    }

    section_checks = defaultdict(list)
    for c in checks:
        section_checks[c['section']].append(c)

    sections_html = ''
    for sec_num, title in TITLES.items():
        rows = ''
        for c in section_checks[sec_num]:
            detail_html = ''
            if c['detail']:
                items = ''.join(f'<li>{d}</li>' for d in c['detail'])
                detail_html = f'<ul style="margin:4px 0 0 16px;font-size:13px;color:#555">{items}</ul>'
            rows += f'''
            <tr style="{row_bg(c["pass"], c["advisory"])}">
              <td style="padding:10px 12px;border-bottom:1px solid #e6e9ef">
                {badge(c["pass"], c["advisory"])}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #e6e9ef">
                {c["label"]}{detail_html}</td>
            </tr>'''

        extra = ''
        if sec_num == 2:
            def dist_table(label, dist):
                dist_rows = ''.join(
                    f'<tr><td style="padding:5px 10px">{"✓" if a==b else "✗"} {st}</td>'
                    f'<td style="padding:5px 10px;text-align:center">{a}</td>'
                    f'<td style="padding:5px 10px;text-align:center">{b}</td></tr>'
                    for st, (a, b) in dist.items()
                )
                return f'''
                <div style="margin-top:16px">
                  <p style="font-weight:600;margin-bottom:6px">{label}</p>
                  <table style="border-collapse:collapse;font-size:13px;width:auto">
                    <thead><tr style="background:{GREY}">
                      <th style="padding:5px 10px;text-align:left">Status</th>
                      <th style="padding:5px 10px">CSV</th>
                      <th style="padding:5px 10px">Live</th>
                    </tr></thead>
                    <tbody>{dist_rows}</tbody>
                  </table>
                </div>'''
            extra = dist_table('Engagements', rd['eng_dist']) + dist_table('Deliverables', rd['del_dist'])

        if sec_num == 3:
            extra = (f'<p style="margin-top:12px;font-weight:600;color:{GREEN}">'
                     f'Field accuracy: {s["fields_matched"]}/{s["fields_total"]} '
                     f'data points ({s["field_pct"]}%)</p>')

        if sec_num == 4:
            eng_rows = ''
            for eng in rd['drilldown']:
                sk = (f'<span style="color:{GREEN if eng["status_ok"] else RED}">'
                      f'{"✓" if eng["status_ok"] else "✗"}</span>')
                del_rows = ''
                for d in eng['deliverables']:
                    ok_c = GREEN if d['status_ok'] else RED
                    del_rows += f'''
                    <tr>
                      <td style="padding:4px 8px;font-size:12px;color:{ok_c}">
                        {"✓" if d["status_ok"] else "✗"}</td>
                      <td style="padding:4px 8px;font-size:12px">{d["name"]}</td>
                      <td style="padding:4px 8px;font-size:12px">{d["priority"]}</td>
                      <td style="padding:4px 8px;font-size:12px">{d["live_status"]}</td>
                      <td style="padding:4px 8px;font-size:12px">{d["due_date"]}</td>
                      <td style="padding:4px 8px;font-size:12px">{d["assignee"]}</td>
                      <td style="padding:4px 8px;font-size:12px;text-align:right">{d["hours"]} h</td>
                    </tr>'''
                eng_rows += f'''
                <div style="margin-bottom:20px;border:1px solid #e6e9ef;border-radius:6px;overflow:hidden">
                  <div style="background:{GREY};padding:10px 14px;font-weight:600">
                    {eng["id"]} — {eng["name"]}
                    <span style="font-weight:400;font-size:13px;margin-left:12px;color:#555">
                      {eng["client"]}  ·  Lead: {eng["lead"]}  ·  ${int(eng["budget"]):,}  ·
                      {sk} {eng["status"]}  ·  {eng["start"]} → {eng["end"]}
                    </span>
                  </div>
                  <div style="padding:8px 14px;font-size:13px;color:#555">
                    {eng["total"]} deliverables  ·  {eng["done"]} done  ·  {eng["hours"]} h estimated
                  </div>
                  <table style="width:100%;border-collapse:collapse;font-size:12px">
                    <thead><tr style="background:{GREY}">
                      <th style="padding:4px 8px;text-align:left">✓</th>
                      <th style="padding:4px 8px;text-align:left">Deliverable</th>
                      <th style="padding:4px 8px;text-align:left">Priority</th>
                      <th style="padding:4px 8px;text-align:left">Status</th>
                      <th style="padding:4px 8px;text-align:left">Due Date</th>
                      <th style="padding:4px 8px;text-align:left">Assignee</th>
                      <th style="padding:4px 8px;text-align:right">Hours</th>
                    </tr></thead>
                    <tbody>{del_rows}</tbody>
                  </table>
                </div>'''
            extra = eng_rows

        if sec_num == 5:
            brows = ''.join(
                f'<tr><td style="padding:5px 10px">{st}</td>'
                f'<td style="padding:5px 10px;text-align:right">${amt:,}</td></tr>'
                for st, amt in rd['budget_by_status'].items()
            )
            brows += (f'<tr style="font-weight:600;background:{GREY}">'
                      f'<td style="padding:5px 10px">TOTAL</td>'
                      f'<td style="padding:5px 10px;text-align:right">${rd["total_budget"]:,}</td></tr>')
            hrows = ''.join(
                f'<tr><td style="padding:5px 10px">{p}</td>'
                f'<td style="padding:5px 10px;text-align:right">{hrs} h</td></tr>'
                for p, hrs in rd['hours_by_person'].items()
            )
            hrows += (f'<tr style="font-weight:600;background:{GREY}">'
                      f'<td style="padding:5px 10px">TOTAL</td>'
                      f'<td style="padding:5px 10px;text-align:right">{rd["total_hours"]} h</td></tr>')
            extra = f'''
            <div style="display:flex;gap:32px;margin-top:16px;flex-wrap:wrap">
              <div>
                <p style="font-weight:600;margin-bottom:6px">Budget by Engagement Status</p>
                <table style="border-collapse:collapse;font-size:13px">
                  <thead><tr style="background:{GREY}">
                    <th style="padding:5px 10px;text-align:left">Status</th>
                    <th style="padding:5px 10px;text-align:right">Budget</th>
                  </tr></thead><tbody>{brows}</tbody>
                </table>
              </div>
              <div>
                <p style="font-weight:600;margin-bottom:6px">Estimated Hours by Assignee</p>
                <table style="border-collapse:collapse;font-size:13px">
                  <thead><tr style="background:{GREY}">
                    <th style="padding:5px 10px;text-align:left">Consultant</th>
                    <th style="padding:5px 10px;text-align:right">Hours</th>
                  </tr></thead><tbody>{hrows}</tbody>
                </table>
              </div>
            </div>'''

        sections_html += f'''
        <div style="margin-bottom:32px">
          <h2 style="font-size:15px;font-weight:700;color:{DARK};margin-bottom:12px;
                     padding-bottom:6px;border-bottom:2px solid {GREEN}">
            {sec_num}. {title}
          </h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px">
            <tbody>{rows}</tbody>
          </table>
          {extra}
        </div>'''

    fail_banner = ''
    if failed:
        items = ''.join(f'<li>{c["label"]}</li>' for c in failed)
        fail_banner = (f'<div style="background:#fff0f2;border-left:4px solid {RED};'
                       f'padding:12px 16px;margin-bottom:24px;border-radius:4px">'
                       f'<strong>Issues requiring attention:</strong>'
                       f'<ul style="margin:8px 0 0 16px">{items}</ul></div>')

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nexus Migration Validation Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           color: {DARK}; margin: 0; padding: 0; background: #f5f6f8; }}
    .container {{ max-width: 1000px; margin: 0 auto; padding: 32px 24px; }}
    .header {{ background: {DARK}; color: white; padding: 28px 32px;
               border-radius: 8px; margin-bottom: 28px; }}
    .header h1 {{ margin: 0 0 4px; font-size: 20px; font-weight: 700; }}
    .header .sub {{ font-size: 13px; color: #c3c6d4; margin-top: 6px; }}
    .cards {{ display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }}
    .card {{ background: white; border-radius: 8px; padding: 18px 22px;
             flex: 1; min-width: 160px; border-top: 3px solid {GREEN}; }}
    .card .val {{ font-size: 26px; font-weight: 700; color: {DARK}; }}
    .card .lbl {{ font-size: 12px; color: #777; margin-top: 2px; }}
    .section-wrap {{ background: white; border-radius: 8px; padding: 24px 28px;
                     margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Nexus Consulting Group — Migration Validation Report</h1>
    <div class="sub">
      Report generated: {h["run_ts"]} &nbsp;·&nbsp;
      Migration run: {h["migrated_at"]} &nbsp;·&nbsp;
      Engagements board: {h["eng_board_id"]} &nbsp;·&nbsp;
      Deliverables board: {h["del_board_id"]}
    </div>
  </div>

  <div class="cards">
    <div class="card" style="border-color:{'#00a650' if s['checks_passed']==s['checks_total'] else RED}">
      <div class="val">{s["checks_passed"]}/{s["checks_total"]}</div>
      <div class="lbl">Checks Passed</div>
    </div>
    <div class="card">
      <div class="val">{s["fields_matched"]}/{s["fields_total"]}</div>
      <div class="lbl">Fields Verified ({s["field_pct"]}%)</div>
    </div>
    <div class="card">
      <div class="val">{h["src_engs"]} / {h["src_dels"]}</div>
      <div class="lbl">Engagements / Deliverables</div>
    </div>
    <div class="card">
      <div class="val">${h["total_budget"]:,}</div>
      <div class="lbl">Total Budget Verified</div>
    </div>
    <div class="card">
      <div class="val">{h["total_hours"]} h</div>
      <div class="lbl">Total Hours Verified</div>
    </div>
  </div>

  {fail_banner}

  <div class="section-wrap">
    {sections_html}
  </div>

  <p style="text-align:center;font-size:12px;color:#aaa;margin-top:24px">
    Nexus Consulting Group · Smartsheet → monday.com Migration · {h["run_ts"]}
  </p>
</div>
</body>
</html>'''

    path = os.path.join(os.path.dirname(__file__), 'validation_report.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    run_validation()
