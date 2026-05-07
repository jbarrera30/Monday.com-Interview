#!/usr/bin/env python3
"""
Nexus Consulting Group — Smartsheet to monday.com Migration
Reads nexus_smartsheet_export.csv and creates two connected boards:
  - Nexus — Engagements
  - Nexus — Deliverables (linked to Engagements via Connect Boards)
"""

import os, csv, json, time
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN    = os.environ['MONDAY_API_TOKEN']
API_URL  = 'https://api.monday.com/v2'
CSV_PATH = os.path.join(os.path.dirname(__file__), 'nexus_smartsheet_export.csv')
WS_ID    = 15369712  # Nexus workspace

# ── Status normalization ──────────────────────────────────────────────────────
# Source Smartsheet values → canonical monday.com labels
ENG_STATUS_MAP = {
    'Not Started': 'Not started',
    'In Progress': 'Active',
    'Active':      'Active',
    'On Hold':     'On hold',
    'Complete':    'Complete',
    'Done':        'Complete',
}
DEL_STATUS_MAP = {
    'To Do':        'To do',
    'Not Started':  'To do',
    'In Progress':  'In progress',
    'Working on it':'In progress',
    'In Review':    'In review',
    'Done':         'Done',
}

# Label → status column index (must match the settings_str used at column creation)
ENG_STATUS_IDX = {'Active': 0, 'On hold': 1, 'Not started': 2, 'Complete': 3}
DEL_STATUS_IDX = {'To do': 0, 'In progress': 1, 'In review': 2, 'Done': 3}


# ── API helper ────────────────────────────────────────────────────────────────
def gql(query: str, variables: dict = None) -> dict:
    headers = {
        'Authorization': TOKEN,
        'Content-Type':  'application/json',
        'API-Version':   '2024-01',
    }
    for attempt in range(8):
        r = requests.post(API_URL, json={'query': query, 'variables': variables or {}}, headers=headers)
        if r.status_code == 429:
            wait = min(2 ** attempt, 60)
            print(f'  Rate limited — retrying in {wait}s…')
            time.sleep(wait)
            continue
        r.raise_for_status()
        body = r.json()
        if 'errors' in body:
            raise RuntimeError(json.dumps(body['errors'], indent=2))
        return body['data']
    raise RuntimeError('Exceeded retry limit due to rate limiting')


def fmt_date(raw: str) -> str:
    """MM/DD/YYYY → YYYY-MM-DD"""
    return datetime.strptime(raw.strip(), '%m/%d/%Y').strftime('%Y-%m-%d')


# ── CSV parsing ───────────────────────────────────────────────────────────────
def load_csv() -> tuple:
    engagements: dict = {}
    deliverables: list = []

    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            eid = row['engagement_id']
            if eid not in engagements:
                engagements[eid] = {
                    'id':     eid,
                    'name':   row['engagement_name'],
                    'client': row['client'],
                    'lead':   row['engagement_lead'],
                    'start':  fmt_date(row['engagement_start']),
                    'end':    fmt_date(row['engagement_end']),
                    'budget': int(row['budget']),
                    'status': ENG_STATUS_MAP[row['engagement_status'].strip()],
                }
            deliverables.append({
                'id':         row['deliverable_id'],
                'name':       row['deliverable_name'],
                'engagement': eid,
                'assignee':   row['assignee'],
                'due_date':   fmt_date(row['due_date']),
                'priority':   row['priority'],
                'status':     DEL_STATUS_MAP[row['deliverable_status'].strip()],
                'hours':      int(row['hours_estimated']),
            })

    return engagements, deliverables


# ── Board setup ───────────────────────────────────────────────────────────────
def create_board(name: str) -> str:
    data = gql(f'''
        mutation {{
          create_board(board_name: "{name}", board_kind: public, workspace_id: {WS_ID}) {{
            id
          }}
        }}
    ''')
    board_id = data['create_board']['id']
    print(f'  Created board "{name}" → id {board_id}')
    _delete_default_item(board_id)
    return board_id


def _delete_default_item(board_id: str):
    """Delete the 'Task 1' placeholder monday.com adds to new boards."""
    data = gql(f'{{ boards(ids: [{board_id}]) {{ items_page(limit: 10) {{ items {{ id name }} }} }} }}')
    for item in data['boards'][0]['items_page']['items']:
        if item['name'] == 'Task 1':
            gql(f'mutation {{ delete_item(item_id: {item["id"]}) {{ id }} }}')
            print(f'  Removed default placeholder item')


def add_column(board_id: str, title: str, col_type: str, defaults: str = None) -> str:
    defaults_arg = f', defaults: {json.dumps(defaults)}' if defaults else ''
    data = gql(f'''
        mutation {{
          create_column(board_id: {board_id}, title: "{title}", column_type: {col_type}{defaults_arg}) {{
            id
          }}
        }}
    ''')
    col_id = data['create_column']['id']
    print(f'    + column "{title}" ({col_type}) → {col_id}')
    time.sleep(1.0)  # avoid rate limiting between column creations
    return col_id


def setup_engagements_board() -> tuple:
    print('\n[1/4] Creating Engagements board…')
    bid = create_board('Nexus — Engagements')

    eng_status_defaults = json.dumps({
        'labels': {'0': 'Active', '1': 'On hold', '2': 'Not started', '3': 'Complete'}
    })

    cols = {}
    cols['client']  = add_column(bid, 'Client',           'text')
    cols['lead']    = add_column(bid, 'Engagement Lead',  'text')
    cols['start']   = add_column(bid, 'Start Date',       'date')
    cols['end']     = add_column(bid, 'End Date',         'date')
    cols['budget']  = add_column(bid, 'Budget ($)',       'numbers')
    cols['status']  = add_column(bid, 'Status',           'status', eng_status_defaults)

    return bid, cols


def setup_deliverables_board(eng_board_id: str) -> tuple:
    print('\n[2/4] Creating Deliverables board…')
    bid = create_board('Nexus — Deliverables')

    del_status_defaults = json.dumps({
        'labels': {'0': 'To do', '1': 'In progress', '2': 'In review', '3': 'Done'}
    })

    cols = {}
    # NOTE: monday.com's API does not support creating Connect Boards columns programmatically.
    # We store the engagement name as text here. Post-migration, manually add a Connect Boards
    # column in the UI pointing to "Nexus — Engagements" to enable native board linking.
    cols['engagement'] = add_column(bid, 'Engagement',  'text')
    cols['assignee']   = add_column(bid, 'Assignee',    'text')
    cols['due_date']   = add_column(bid, 'Due Date',    'date')
    cols['hours']      = add_column(bid, 'Est. Hours',  'numbers')
    cols['priority']   = add_column(bid, 'Priority',    'text')
    cols['status']     = add_column(bid, 'Status',      'status', del_status_defaults)

    return bid, cols


# ── Item creation ─────────────────────────────────────────────────────────────
def create_item(board_id: str, name: str, col_values: dict) -> str:
    data = gql(
        '''
        mutation($boardId: ID!, $name: String!, $vals: JSON!) {
          create_item(board_id: $boardId, item_name: $name, column_values: $vals) {
            id
          }
        }
        ''',
        {'boardId': board_id, 'name': name, 'vals': json.dumps(col_values)},
    )
    return data['create_item']['id']


# ── Migration ─────────────────────────────────────────────────────────────────
def migrate_engagements(board_id: str, cols: dict, engagements: dict) -> dict:
    print('\n[3/4] Migrating engagements…')
    eng_item_map = {}  # engagement_id → monday item_id

    for eng in engagements.values():
        col_values = {
            cols['client']:  eng['client'],
            cols['lead']:    eng['lead'],
            cols['start']:   {'date': eng['start']},
            cols['end']:     {'date': eng['end']},
            cols['budget']:  eng['budget'],
            cols['status']:  {'index': ENG_STATUS_IDX[eng['status']]},
        }
        item_id = create_item(board_id, eng['name'], col_values)
        eng_item_map[eng['id']] = item_id
        print(f'  {eng["id"]} → item {item_id}: {eng["name"]} [{eng["status"]}]')
        time.sleep(0.6)  # respect rate limit

    return eng_item_map


def migrate_deliverables(board_id: str, cols: dict, deliverables: list,
                         eng_item_map: dict, engagements: dict):
    print('\n[4/4] Migrating deliverables…')

    for d in deliverables:
        eng_name = engagements[d['engagement']]['name']
        col_values = {
            cols['engagement']: eng_name,
            cols['assignee']:   d['assignee'],
            cols['due_date']:   {'date': d['due_date']},
            cols['hours']:      d['hours'],
            cols['priority']:   d['priority'],
            cols['status']:     {'index': DEL_STATUS_IDX[d['status']]},
        }
        item_id = create_item(board_id, d['name'], col_values)
        print(f'  {d["id"]} → item {item_id}: {d["name"]} [{d["status"]}]')
        time.sleep(0.3)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print('=' * 60)
    print('Nexus Consulting Group — Smartsheet → monday.com Migration')
    print('=' * 60)

    engagements, deliverables = load_csv()
    print(f'Source: {len(engagements)} engagements, {len(deliverables)} deliverables')

    eng_board_id, eng_cols = setup_engagements_board()
    del_board_id, del_cols = setup_deliverables_board(eng_board_id)

    eng_item_map = migrate_engagements(eng_board_id, eng_cols, engagements)
    migrate_deliverables(del_board_id, del_cols, deliverables, eng_item_map, engagements)

    manifest = {
        'eng_board_id':  eng_board_id,
        'del_board_id':  del_board_id,
        'eng_cols':      eng_cols,
        'del_cols':      del_cols,
        'eng_item_map':  eng_item_map,
        'migrated_at':   datetime.now().isoformat(),
    }
    manifest_path = os.path.join(os.path.dirname(__file__), 'migration_manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print('\n' + '=' * 60)
    print(f'Migration complete.')
    print(f'Engagements board id : {eng_board_id}')
    print(f'Deliverables board id: {del_board_id}')
    print(f'Manifest saved to    : migration_manifest.json')
    print('Run validate.py to verify the migration.')


if __name__ == '__main__':
    main()
