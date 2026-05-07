#!/usr/bin/env python3
"""
Backfill the Connect Boards column on existing Deliverables items.

The monday.com API does not support creating board_relation columns, so the
column was created manually in the UI. This script writes the linked engagement
item ID to each deliverable using change_column_value.

Run AFTER the UI column exists and migrate.py has already been run.
"""

import os, json, time
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN   = os.environ['MONDAY_API_TOKEN']
API_URL = 'https://api.monday.com/v2'

# Column created manually in the UI on the Deliverables board.
CONNECT_COL = 'board_relation_mm34f6c4'


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


def fetch_all_deliverable_items(board_id: str, engagement_col: str) -> list:
    """Paginate through all items on the Deliverables board."""
    items, cursor = [], None
    while True:
        cursor_arg = f', cursor: "{cursor}"' if cursor else ''
        data = gql(f'''
            {{
              boards(ids: [{board_id}]) {{
                items_page(limit: 50{cursor_arg}) {{
                  cursor
                  items {{
                    id
                    name
                    column_values(ids: ["{engagement_col}"]) {{
                      text
                    }}
                  }}
                }}
              }}
            }}
        ''')
        page = data['boards'][0]['items_page']
        items.extend(page['items'])
        cursor = page.get('cursor')
        if not cursor:
            break
    return items


def main():
    manifest_path = os.path.join(os.path.dirname(__file__), 'migration_manifest.json')
    with open(manifest_path) as f:
        manifest = json.load(f)

    del_board_id    = manifest['del_board_id']
    engagement_col  = manifest['del_cols']['engagement']   # text col storing engagement name
    eng_item_map    = manifest['eng_item_map']             # ENG-xxx → monday item id

    # Build reverse map: engagement name → monday engagement item id
    # Derived from the CSV engagement names that were used as item names on the Engagements board.
    name_to_eng_id = {
        'Digital Transformation Strategy': 'ENG-001',
        'Operational Excellence Program':  'ENG-002',
        'Market Entry Analysis':           'ENG-003',
        'Supply Chain Optimization':       'ENG-004',
        'Customer Experience Redesign':    'ENG-005',
        'Workforce Planning Initiative':   'ENG-006',
    }

    print(f'Fetching deliverable items from board {del_board_id}…')
    items = fetch_all_deliverable_items(del_board_id, engagement_col)
    print(f'Found {len(items)} items.')

    updated = 0
    for item in items:
        eng_name = (item['column_values'][0].get('text') or '').strip()
        eng_id   = name_to_eng_id.get(eng_name)
        if not eng_id:
            print(f'  SKIP {item["name"]!r}: no engagement match for {eng_name!r}')
            continue

        monday_eng_item_id = int(eng_item_map[eng_id])
        col_value = json.dumps({'item_ids': [monday_eng_item_id]})

        gql(
            '''
            mutation($boardId: ID!, $itemId: ID!, $colId: String!, $val: JSON!) {
              change_column_value(board_id: $boardId, item_id: $itemId,
                                  column_id: $colId, value: $val) {
                id
              }
            }
            ''',
            {
                'boardId': del_board_id,
                'itemId':  item['id'],
                'colId':   CONNECT_COL,
                'val':     col_value,
            },
        )
        print(f'  Linked {item["name"]!r} → {eng_name} (eng item {monday_eng_item_id})')
        updated += 1
        time.sleep(0.4)

    print(f'\nDone. {updated}/{len(items)} deliverables linked.')


if __name__ == '__main__':
    main()
