"""SQLite store for the board (SPEC-0.3 section 4). Python 3.10 stdlib only.

One connection, guarded by a re-entrant lock, autocommit mode (isolation_level=None) with explicit
BEGIN IMMEDIATE for writes. `transaction()` lets a caller group check-then-insert steps atomically
(e.g. rate limit + dedupe + insert); nested transaction() / write calls join the outer transaction.
BEGIN IMMEDIATE also takes SQLite's RESERVED lock, so a second process on the same file is serialised too.
"""
import contextlib
import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ws TEXT NOT NULL,
    sender TEXT NOT NULL,
    sender_kind TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    paths TEXT NOT NULL DEFAULT '[]',
    reply_to INTEGER NULL,
    thread INTEGER NULL,
    status TEXT NOT NULL,
    answered_by TEXT NULL,
    about TEXT NULL,
    event TEXT NULL
);
CREATE INDEX IF NOT EXISTS posts_ws_id ON posts(ws, id);
CREATE INDEX IF NOT EXISTS posts_sender_ts ON posts(sender, ts);
CREATE INDEX IF NOT EXISTS posts_open_q ON posts(ws, kind, status);
CREATE TABLE IF NOT EXISTS agent_meta(
    aid TEXT PRIMARY KEY,
    owner TEXT, client TEXT, parent TEXT, ws TEXT, cwd TEXT, goal TEXT,
    paths TEXT, created REAL, ended REAL, end_reason TEXT
);
CREATE TABLE IF NOT EXISTS cursors(
    aid TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0
);
"""

POST_COLS = ("id", "ts", "ws", "sender", "sender_kind", "kind", "text", "paths", "reply_to", "thread",
             "status", "answered_by", "about", "event")
META_COLS = ("owner", "client", "parent", "ws", "cwd", "goal", "paths", "created", "ended", "end_reason")
STATUSES = ("open", "answered", "closed")
_ANY = object()


def _row_to_post(r):
    if r is None:
        return None
    d = dict(zip(POST_COLS, r))
    try:
        d["paths"] = json.loads(d["paths"] or "[]")
    except ValueError:
        d["paths"] = []
    return d


class Store:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
        with self._lock:
            c = self._conn
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            with self.transaction():
                for stmt in SCHEMA.split(";"):
                    if stmt.strip():
                        c.execute(stmt)
                # forward-compatible: add columns to a db created by an earlier build
                have = {r[1] for r in c.execute("PRAGMA table_info(posts)")}
                for col in ("about", "event"):
                    if col not in have:
                        c.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT NULL")

    # ---- transactions -------------------------------------------------------------------------------
    @contextlib.contextmanager
    def transaction(self):
        """Exclusive write transaction (BEGIN IMMEDIATE). Re-entrant: inner uses join the outer one."""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("store is closed")
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    try:
                        self._conn.execute("COMMIT")
                    except BaseException:
                        with contextlib.suppress(sqlite3.Error):
                            self._conn.execute("ROLLBACK")
                        raise

    def _q(self, sql, args=()):
        with self._lock:
            if self._conn is None:
                raise RuntimeError("store is closed")
            return self._conn.execute(sql, args).fetchall()

    # ---- posts --------------------------------------------------------------------------------------
    def add_post(self, ts, ws, sender, sender_kind, kind, text, paths=None, reply_to=None, thread=None,
                 status="closed", answered_by=None, about=None, event=None):
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO posts(ts,ws,sender,sender_kind,kind,text,paths,reply_to,thread,status,answered_by,about,event)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (float(ts), ws, sender, sender_kind, kind, text, json.dumps(list(paths or [])), reply_to, thread,
                 status, answered_by, about, event))
            return cur.lastrowid

    def get_post(self, post_id):
        rows = self._q(f"SELECT {','.join(POST_COLS)} FROM posts WHERE id=?", (int(post_id),))
        return _row_to_post(rows[0]) if rows else None

    def list_posts(self, ws, since=0, kinds=None, limit=100, exclude_sender=None, status=None,
                   max_id=None, before_ts=None, newest_first=False):
        """Posts with id > since (ascending unless newest_first). ws=None means every workspace."""
        where, args = ["id > ?"], [int(since or 0)]
        if ws is not None:
            where.append("ws = ?")
            args.append(ws)
        if kinds:
            kinds = [kinds] if isinstance(kinds, str) else list(kinds)
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if exclude_sender:
            ex = [exclude_sender] if isinstance(exclude_sender, str) else list(exclude_sender)
            where.append(f"sender NOT IN ({','.join('?' * len(ex))})")
            args += ex
        if status:
            where.append("status = ?")
            args.append(status)
        if max_id is not None:
            where.append("id <= ?")
            args.append(int(max_id))
        if before_ts is not None:
            where.append("ts <= ?")
            args.append(float(before_ts))
        limit = max(1, min(int(limit or 100), 1000))
        order = "DESC" if newest_first else "ASC"
        sql = f"SELECT {','.join(POST_COLS)} FROM posts WHERE {' AND '.join(where)} ORDER BY id {order} LIMIT ?"
        return [_row_to_post(r) for r in self._q(sql, args + [limit])]

    def update_post_status(self, post_id, status, answered_by=None):
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
        with self.transaction() as c:
            return c.execute("UPDATE posts SET status=?, answered_by=COALESCE(?, answered_by) WHERE id=?",
                             (status, answered_by, int(post_id))).rowcount

    def count_recent_posts(self, sender, since_ts):
        return self._q("SELECT COUNT(*) FROM posts WHERE sender=? AND ts >= ?", (sender, float(since_ts)))[0][0]

    def recent_duplicate(self, sender, text, since_ts, reply_to=_ANY):
        """id of a post by `sender` with identical `text` at or after since_ts, else None.
        reply_to (optional) narrows the match to the same thread target (None = top-level)."""
        sql, args = "SELECT id FROM posts WHERE sender=? AND text=? AND ts >= ?", [sender, text, float(since_ts)]
        if reply_to is not _ANY:
            if reply_to is None:
                sql += " AND reply_to IS NULL"
            else:
                sql += " AND reply_to = ?"
                args.append(int(reply_to))
        rows = self._q(sql + " ORDER BY id DESC LIMIT 1", args)
        return rows[0][0] if rows else None

    def max_post_id(self, ws=None):
        if ws is None:
            return self._q("SELECT COALESCE(MAX(id),0) FROM posts")[0][0]
        return self._q("SELECT COALESCE(MAX(id),0) FROM posts WHERE ws=?", (ws,))[0][0]

    def query(self, sql, args=()):
        """Read-only escape hatch for callers that need a custom SELECT (used by board digest)."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("query() is read-only")
        return self._q(sql, args)

    # ---- agent meta ---------------------------------------------------------------------------------
    def upsert_agent_meta(self, aid, **fields):
        bad = set(fields) - set(META_COLS)
        if bad:
            raise ValueError(f"unknown agent_meta fields: {sorted(bad)}")
        if "paths" in fields and not isinstance(fields["paths"], str):
            fields["paths"] = json.dumps(list(fields["paths"] or []))
        with self.transaction() as c:
            c.execute("INSERT OR IGNORE INTO agent_meta(aid) VALUES(?)", (aid,))
            if fields:
                sets = ",".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE agent_meta SET {sets} WHERE aid=?", list(fields.values()) + [aid])

    def get_agent_meta(self, aid):
        rows = self._q(f"SELECT aid,{','.join(META_COLS)} FROM agent_meta WHERE aid=?", (aid,))
        if not rows:
            return None
        d = dict(zip(("aid",) + META_COLS, rows[0]))
        try:
            d["paths"] = json.loads(d["paths"] or "[]")
        except ValueError:
            d["paths"] = []
        return d

    # ---- cursors ------------------------------------------------------------------------------------
    def set_cursor(self, aid, seq):
        """Monotonic: the cursor never moves backwards."""
        with self.transaction() as c:
            c.execute("INSERT INTO cursors(aid,last_seq) VALUES(?,?) "
                      "ON CONFLICT(aid) DO UPDATE SET last_seq=MAX(last_seq, excluded.last_seq)", (aid, int(seq)))

    def get_cursor(self, aid):
        rows = self._q("SELECT last_seq FROM cursors WHERE aid=?", (aid,))
        return rows[0][0] if rows else 0

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None
