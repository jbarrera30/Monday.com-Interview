#!/usr/bin/env python3
"""
Nexus Consulting Group — monday vibe Dashboard Setup Guide
Prints a step-by-step configuration guide using board/column IDs from the migration manifest.
Run this before your demo to get exact values for each widget.
"""

import os, json
from datetime import datetime, timedelta

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), 'migration_manifest.json')


def main():
    with open(MANIFEST_PATH) as f:
        m = json.load(f)

    eng_id = m['eng_board_id']
    del_id = m['del_board_id']
    eng_c  = m['eng_cols']
    del_c  = m['del_cols']

    today      = datetime.today()
    cutoff     = today + timedelta(days=14)
    today_str  = today.strftime('%Y-%m-%d')
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    SEP = '─' * 62

    print()
    print('=' * 62)
    print('  NEXUS CLIENT OVERVIEW — Dashboard Setup Guide')
    print(f'  Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 62)
    print()
    print('BOARD IDs (for reference during widget setup)')
    print(SEP)
    print(f'  Nexus — Engagements  : {eng_id}')
    print(f'  Nexus — Deliverables : {del_id}')
    print()
    print('STEP 0 — Create the dashboard')
    print(SEP)
    print('  1. In monday.com, click "+ Add" in the left sidebar')
    print('  2. Select "Dashboard"')
    print('  3. Name it: Nexus Client Overview')
    print('  4. Keep it in draft mode (no need to publish)')
    print()

    print(SEP)
    print('WIDGET 1 — Engagements by Status  (Numbers widget)')
    print(SEP)
    print('  Purpose : Show how many engagements are in each status.')
    print()
    print('  1. Click "+ Add widget" → choose "Numbers"')
    print('  2. Connect board: Nexus — Engagements')
    print(f'  3. Set "Count by": Status  (column: {eng_c["status"]})')
    print('  4. Display mode: Breakdown / Chart')
    print('  5. No filter needed — shows all 6 engagements by status:')
    print('     Active (2) · Complete (1) · On hold (1) · Not started (1)')
    print()
    print('  Demo talking point:')
    print('  "Two engagements active, one on hold — that\'s Retail Plus,')
    print('   which the team flagged during discovery."')
    print()

    print(SEP)
    print('WIDGET 2 — Deliverables Pipeline  (Chart widget)')
    print(SEP)
    print('  Purpose : Pipeline health across all 27 deliverables.')
    print()
    print('  1. Click "+ Add widget" → choose "Chart"')
    print('  2. Connect board: Nexus — Deliverables')
    print('  3. Chart type: Bar or Pie')
    print(f'  4. Group by: Status  (column: {del_c["status"]})')
    print('  5. No filter — shows all deliverables:')
    print('     Done (10) · To do (10) · In progress (4) · In review (1)')
    print()
    print('  Demo talking point:')
    print('  "About a third complete, a third in flight. The In Review')
    print('   item is the Efficiency Analysis — waiting on client sign-off."')
    print()

    print(SEP)
    print('WIDGET 3 — Workload by Assignee  (Chart + Numbers widgets)')
    print(SEP)
    print('  Purpose : Deliverable count + hours per consultant.')
    print()
    print('  3a. Deliverable count per consultant')
    print('  1. Click "+ Add widget" → choose "Chart"')
    print('  2. Connect board: Nexus — Deliverables')
    print(f'  3. Group by: Assignee  (column: {del_c["assignee"]})')
    print('  4. Count items (not sum)')
    print()
    print('  3b. Estimated hours per consultant')
    print('  1. Click "+ Add widget" → choose "Numbers"')
    print('  2. Connect board: Nexus — Deliverables')
    print(f'  3. Summarize: Sum of Est. Hours  (column: {del_c["hours"]})')
    print(f'  4. Group by: Assignee  (column: {del_c["assignee"]})')
    print()
    print('  Key consultants by hours:')
    print('    Chris Anderson   104 h')
    print('    James Wilson      88 h')
    print('    Sarah Chen        72 h')
    print('    Rachel Martinez   68 h')
    print()
    print('  Demo talking point:')
    print('  "Chris is heaviest — all his hours are on the completed')
    print('   Supply Chain engagement, so capacity is freed up now."')
    print()

    print(SEP)
    print('WIDGET 4 — On-Track View  (Board / Table widget)')
    print(SEP)
    print(f'  Purpose : Deliverables due in the next 14 days ({today_str} – {cutoff_str}).')
    print()
    print('  1. Click "+ Add widget" → choose "Board" (embedded board view)')
    print('  2. Connect board: Nexus — Deliverables')
    print('  3. Add filter → Due Date is between:')
    print(f'       From : {today_str}')
    print(f'       To   : {cutoff_str}')
    print('  4. Group by: Status')
    print('  5. Visible columns: Name, Engagement, Assignee, Due Date, Status')
    print()
    print('  Note: Data is historical (2024-2025). For demo, remove the date')
    print('  filter and sort by Due Date ascending to show the full pipeline.')
    print()

    print(SEP)
    print('WIDGET 5 — Total Active Budget  (Numbers widget)')
    print(SEP)
    print('  Purpose : Sum of budget across Active engagements only.')
    print()
    print('  1. Click "+ Add widget" → choose "Numbers"')
    print('  2. Connect board: Nexus — Engagements')
    print(f'  3. Summarize: Sum of Budget ($)  (column: {eng_c["budget"]})')
    print(f'  4. Add filter → Status = Active')
    print('  5. Expected value: $350,000')
    print('     (ENG-001 $150k + ENG-002 $200k)')
    print()
    print('  Demo talking point:')
    print('  "$350K in active billings — ENG-003 was In Progress in')
    print('   Smartsheet but normalized to Active per the spec."')
    print()

    print('=' * 62)
    print('  LAYOUT SUGGESTION')
    print('=' * 62)
    print('  Row 1 (full width)  : Widget 1 — Engagements by Status')
    print('  Row 2 (full width)  : Widget 2 — Deliverables Pipeline')
    print('  Row 3 (split 60/40) : Widget 3 (chart) left | Widget 5 right')
    print('  Row 4 (full width)  : Widget 4 — On-Track View')
    print()
    print('  Tip: Keep the two boards open in adjacent browser tabs')
    print('  for drill-down during the live demo.')
    print()


if __name__ == '__main__':
    main()
