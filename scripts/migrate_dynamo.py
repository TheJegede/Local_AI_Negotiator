"""
Migrate NegotiationSessions from DynamoDB → SQLite sessions.db

Usage:
    cd backend
    python ../scripts/migrate_dynamo.py

Requirements: boto3 (pip install boto3)
AWS credentials must be configured (aws configure or env vars).
"""

import json
import sqlite3
import os
import boto3
from decimal import Decimal

DYNAMO_TABLE = "NegotiationSessions"
DYNAMO_REGION = "us-east-2"
DB_PATH = os.environ.get("DB_PATH", "sessions.db")


def convert_decimals(obj):
    """boto3 resource returns Decimal for all numbers — convert back to int/float."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    return obj


def get_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


def scan_all(table) -> list:
    items = []
    resp = table.scan()
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp["Items"])
    return items


def main():
    if not os.path.exists(DB_PATH) and not os.path.isabs(DB_PATH):
        print(f"Warning: {DB_PATH} not found in current directory.")
        print("Run from the backend/ folder: cd backend && python ../scripts/migrate_dynamo.py")

    print(f"Connecting to DynamoDB table '{DYNAMO_TABLE}' in {DYNAMO_REGION}...")
    dynamodb = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
    table = dynamodb.Table(DYNAMO_TABLE)

    print("Scanning all items (paginated)...")
    raw_items = scan_all(table)
    print(f"Found {len(raw_items)} sessions in DynamoDB.")

    conn = get_db(DB_PATH)
    inserted = 0
    skipped = 0

    for item in raw_items:
        session_id = item.get("session_id", {})
        if isinstance(session_id, dict):
            session_id = session_id.get("S", "")

        data = convert_decimals(dict(item))
        created_at = data.get("created_at", "")

        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, data, created_at) VALUES (?, ?, ?)",
                (session_id, json.dumps(data), created_at)
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR on session {session_id}: {e}")

    conn.commit()
    conn.close()

    print(f"\nDone. {inserted} imported, {skipped} skipped (already existed).")
    print(f"SQLite file: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    main()
