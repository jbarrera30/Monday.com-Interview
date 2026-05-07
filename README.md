# Nexus Consulting Group — Smartsheet to monday.com Migration

Take-home project for the monday.com Technical Consultant interview. Migrates Nexus Consulting Group's project data from a Smartsheet export into two connected monday.com boards, then validates the result field-by-field against the source.

## What's included

| File | Purpose |
|---|---|
| `migrate.py` | Creates the Engagements and Deliverables boards and populates all items |
| `validate.py` | Audits the live boards against the CSV and writes `validation_report.html` |
| `nexus_smartsheet_export.csv` | Source data — 6 engagements, 27 deliverables |
| `validation_report.html` | Latest validation output (open in browser) |

## Setup

```bash
pip install requests python-dotenv
```

Create a `.env` file in the project root:

```
MONDAY_API_TOKEN=your_token_here
```

## Usage

Run the migration first, then validate:

```bash
python3 migrate.py
python3 validate.py
```

`migrate.py` writes a `migration_manifest.json` with the board and column IDs from the run. `validate.py` reads that manifest to know exactly which boards to audit.

## Boards created

- **Nexus — Engagements** — one item per engagement with ID, client, lead, dates, budget, and status
- **Nexus — Deliverables** — one item per deliverable with ID, engagement reference, assignee, due date, hours, priority, and status

Status values are normalized from Smartsheet vocabulary to monday.com canonical labels on the way in.

## Validation results

38/38 checks passed · 198/198 fields verified · 100% field accuracy

> **Note:** The monday.com API does not support creating Connect Boards columns programmatically. The `Engagement` column on the Deliverables board stores the engagement name as text. To enable native board linking, add a Connect Boards column in the UI pointing to the Engagements board.
