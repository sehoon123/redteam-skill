#!/usr/bin/env python3
"""
Pentest Knowledge Base — SQLite FTS5 기반 공유 지식 저장소.

Agent들이 board/findings/memory의 모든 데이터를 의미론적으로 검색할 수 있다.
설치 필요 없음 (Python stdlib만 사용).

사용법:
  # 인덱싱 (board/findings/memory의 모든 파일을 DB에 삽입)
  python3 .pi/pentest/kb.py index

  # 검색
  python3 .pi/pentest/kb.py search "prototype pollution XSS"
  python3 .pi/pentest/kb.py search "credential password admin"
  python3 .pi/pentest/kb.py search "SQLi UNION 8컬럼"

  # 삽입 (agent가 직접 새 지식 추가)
  python3 .pi/pentest/kb.py add --source worker-1 --type intel --body "Found open redirect on /blog?back= parameter"

  # 카테고리별 조회
  python3 .pi/pentest/kb.py list --type technique
  python3 .pi/pentest/kb.py list --type intel
  python3 .pi/pentest/kb.py list --source worker-1

  # 통계
  python3 .pi/pentest/kb.py stats
"""

import sqlite3
import json
import os
import sys
import glob
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb.db")
PENTEST_DIR = os.path.dirname(os.path.abspath(__file__))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # FTS5 table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS kb USING fts5(
            ts, source, type, body, file_origin,
            tokenize='unicode61'
        )
    """)
    # Regular table for structured data (credentials, endpoints, etc.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS endpoints (
            endpoint TEXT,
            technique TEXT,
            tested_by TEXT,
            result TEXT,
            finding_id TEXT,
            ts TEXT,
            PRIMARY KEY (endpoint, technique)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            username TEXT,
            value TEXT,
            source TEXT,
            validated INTEGER DEFAULT 0,
            ts TEXT
        )
    """)
    conn.commit()
    return conn


def index_all():
    """board/, findings/, memory/의 모든 파일을 KB에 인덱싱."""
    conn = get_conn()
    # Clear existing FTS data
    conn.execute("DELETE FROM kb")
    count = 0

    # Index JSONL files (board/, memory/)
    for pattern in ["board/*.jsonl", "memory/*.jsonl"]:
        for fpath in glob.glob(os.path.join(PENTEST_DIR, pattern)):
            fname = os.path.relpath(fpath, PENTEST_DIR)
            with open(fpath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        conn.execute(
                            "INSERT INTO kb (ts, source, type, body, file_origin) VALUES (?,?,?,?,?)",
                            (rec.get("ts", ""), rec.get("agent", ""), rec.get("type", ""), rec.get("body", ""), fname)
                        )
                        count += 1
                    except json.JSONDecodeError:
                        continue

    # Index JSON files (findings/, state/)
    for pattern in ["findings/*.json", "state/*.json"]:
        for fpath in glob.glob(os.path.join(PENTEST_DIR, pattern)):
            fname = os.path.relpath(fpath, PENTEST_DIR)
            try:
                with open(fpath, 'r') as f:
                    data = json.load(f)
                body = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
                conn.execute(
                    "INSERT INTO kb (ts, source, type, body, file_origin) VALUES (?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), "system", "structured", body[:10000], fname)
                )
                count += 1
            except (json.JSONDecodeError, IOError):
                continue

    # Index markdown files
    for pattern in ["board/*.md", "findings/*.md", "memory/*.md"]:
        for fpath in glob.glob(os.path.join(PENTEST_DIR, pattern)):
            fname = os.path.relpath(fpath, PENTEST_DIR)
            try:
                with open(fpath, 'r') as f:
                    body = f.read()[:10000]
                conn.execute(
                    "INSERT INTO kb (ts, source, type, body, file_origin) VALUES (?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), "system", "document", body, fname)
                )
                count += 1
            except IOError:
                continue

    conn.commit()
    print(f"Indexed {count} records into {DB_PATH}")
    return count


def search(query, limit=20):
    """FTS5 full-text search with LIKE fallback for CJK."""
    conn = get_conn()
    results = []

    # Try FTS5 MATCH first
    try:
        rows = conn.execute("""
            SELECT ts, source, type, body, file_origin, rank
            FROM kb
            WHERE kb MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit)).fetchall()
        for row in rows:
            results.append({
                "ts": row["ts"],
                "source": row["source"],
                "type": row["type"],
                "body": row["body"][:500],
                "file": row["file_origin"],
                "score": round(row["rank"], 3)
            })
    except Exception:
        pass

    # Fallback: LIKE search for CJK and phrases FTS5 misses
    if not results:
        keywords = query.split()
        where_parts = []
        params = []
        for kw in keywords:
            where_parts.append("body LIKE ?")
            params.append(f"%{kw}%")
        if where_parts:
            sql = f"SELECT ts, source, type, body, file_origin FROM kb WHERE {' AND '.join(where_parts)} LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                results.append({
                    "ts": row["ts"],
                    "source": row["source"],
                    "type": row["type"],
                    "body": row["body"][:500],
                    "file": row["file_origin"],
                    "score": -1.0  # LIKE fallback
                })

    return results


def add_record(source, rtype, body):
    """Agent가 직접 새 지식을 KB에 추가."""
    conn = get_conn()
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO kb (ts, source, type, body, file_origin) VALUES (?,?,?,?,?)",
        (ts, source, rtype, body, "direct-insert")
    )
    conn.commit()
    print(f"Added: [{rtype}] from {source}")


def add_endpoint(endpoint, technique, tested_by, result, finding_id=None):
    """테스트된 엔드포인트 기록 (중복 방지용)."""
    conn = get_conn()
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO endpoints (endpoint, technique, tested_by, result, finding_id, ts)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (endpoint, technique, tested_by, result, finding_id, ts))
    conn.commit()
    print(f"Endpoint: {endpoint} × {technique} = {result}")


def add_credential(cred_type, username, value, source, validated=False):
    """발견된 자격증명 기록."""
    conn = get_conn()
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO credentials (type, username, value, source, validated, ts)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (cred_type, username, value, source, 1 if validated else 0, ts))
    conn.commit()
    print(f"Credential: {cred_type} {username} from {source}")


def get_untested(technique=None):
    """아직 테스트되지 않은 엔드포인트×기법 조합 조회."""
    conn = get_conn()
    if technique:
        rows = conn.execute("""
            SELECT DISTINCT endpoint FROM endpoints
            WHERE endpoint NOT IN (
                SELECT endpoint FROM endpoints WHERE technique = ?
            )
        """, (technique,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM endpoints WHERE result IS NULL").fetchall()
    return [dict(r) for r in rows]


def get_credentials():
    """모든 자격증명 조회."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM credentials ORDER BY ts DESC").fetchall()
    return [dict(r) for r in rows]


def list_records(rtype=None, source=None, limit=50):
    """카테고리별 또는 소스별 레코드 조회."""
    conn = get_conn()
    if rtype:
        rows = conn.execute("SELECT * FROM kb WHERE type = ? ORDER BY ts DESC LIMIT ?", (rtype, limit)).fetchall()
    elif source:
        rows = conn.execute("SELECT * FROM kb WHERE source = ? ORDER BY ts DESC LIMIT ?", (source, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM kb ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats():
    """KB 통계."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM kb").fetchone()[0]
    by_type = conn.execute("SELECT type, COUNT(*) as cnt FROM kb GROUP BY type ORDER BY cnt DESC").fetchall()
    by_source = conn.execute("SELECT source, COUNT(*) as cnt FROM kb GROUP BY source ORDER BY cnt DESC").fetchall()
    endpoints = conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0]
    creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]

    print(f"=== KB Stats ===")
    print(f"Total records: {total}")
    print(f"Endpoints tracked: {endpoints}")
    print(f"Credentials: {creds}")
    print(f"\nBy type:")
    for r in by_type:
        print(f"  {r['type']}: {r['cnt']}")
    print(f"\nBy source:")
    for r in by_source:
        print(f"  {r['source']}: {r['cnt']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "index":
        index_all()

    elif cmd == "search" and len(sys.argv) >= 3:
        query = " ".join(sys.argv[2:])
        results = search(query)
        if not results:
            print("No results.")
        for r in results:
            print(f"\n[{r['type']}] {r['source']} ({r['file']}) score={r['score']}")
            print(f"  {r['body'][:200]}")

    elif cmd == "add":
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("cmd")
        p.add_argument("--source", required=True)
        p.add_argument("--type", required=True)
        p.add_argument("--body", required=True)
        args = p.parse_args()
        add_record(args.source, args.type, args.body)

    elif cmd == "endpoint":
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("cmd")
        p.add_argument("--endpoint", required=True)
        p.add_argument("--technique", required=True)
        p.add_argument("--tested-by", required=True)
        p.add_argument("--result", required=True)
        p.add_argument("--finding-id", default=None)
        args = p.parse_args()
        add_endpoint(args.endpoint, args.technique, args.tested_by, args.result, args.finding_id)

    elif cmd == "credential":
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("cmd")
        p.add_argument("--type", required=True)
        p.add_argument("--username", required=True)
        p.add_argument("--value", required=True)
        p.add_argument("--source", required=True)
        p.add_argument("--validated", action="store_true")
        args = p.parse_args()
        add_credential(args.type, args.username, args.value, args.source, args.validated)

    elif cmd == "creds":
        for c in get_credentials():
            v = "✓" if c["validated"] else "?"
            print(f"  {v} [{c['type']}] {c['username']} from {c['source']}")

    elif cmd == "untested":
        technique = sys.argv[2] if len(sys.argv) >= 3 else None
        for e in get_untested(technique):
            print(f"  {e}")

    elif cmd == "list":
        rtype = None
        source = None
        for i, a in enumerate(sys.argv):
            if a == "--type" and i + 1 < len(sys.argv):
                rtype = sys.argv[i + 1]
            if a == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
        for r in list_records(rtype, source):
            print(f"[{r['type']}] {r['source']}: {str(r['body'])[:150]}")

    elif cmd == "stats":
        stats()

    else:
        print(__doc__)
