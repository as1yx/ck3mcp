"""Durable write-ahead action records shared by runs. Never automatically replay inputs."""
import json
import sqlite3
import threading
import time


class Journal:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, run TEXT, state TEXT, data TEXT, updated REAL)")
            self.db.commit()
        except BaseException:
            self.db.close()
            raise

    def write(self, aid, run, state, data):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO actions VALUES (?,?,?,?,?)",
                            (aid, run, state, json.dumps(data, ensure_ascii=False), time.time()))

    def unresolved(self):
        with self.lock:
            return [{"action_id": row[0], "run_id": row[1], "state": row[2], "data": json.loads(row[3])}
                    for row in self.db.execute("SELECT id,run,state,data FROM actions WHERE state IN ('prepared','dispatching','dispatched','awaiting_final','unknown')")]

    def record_action_verification(self, aid, run, data, requires_final=False):
        """A successful submission remains unresolved until final verification."""
        record = dict(data)
        state = "unknown"
        if record["status"] == "success":
            state = "awaiting_final" if requires_final else "verified"
        if state == "awaiting_final":
            record["final_verification"] = {"status": "pending"}
        record["journal_state"] = state
        self.write(aid, run, state, record)
        data.update(record)

    def record_final_verification(self, aid, run, data, verdict, outcome, observation):
        """Persist the final interpretation before reporting task completion."""
        # Replace fields on a copy; never mutate shared nested data before commit.
        record = dict(data)
        success = outcome["status"] == "success" and outcome["advanced"]
        state = "verified" if success else "unknown"
        record.update(status="success" if success else "unknown", journal_state=state,
                    final_verification={"status": outcome["status"], "advanced": outcome["advanced"],
                        "evidence": outcome["evidence"], "facts": outcome["facts"],
                        "validation_error": outcome["error"], "raw_verdict": verdict,
                        "observation": observation})
        self.write(aid, run, state, record)
        data.update(record)

    def resolve(self, aid, outcome, note):
        if outcome not in ("success", "failed", "not_executed") or not note.strip():
            raise ValueError("必须指定 success/failed/not_executed 和人工核查说明")
        with self.lock, self.db:
            row = self.db.execute("SELECT data FROM actions WHERE id=?", (aid,)).fetchone()
            if row is None:
                raise ValueError("未知动作")
            data = json.loads(row[0])
            data.update(manual_outcome=outcome, manual_note=note)
            self.db.execute("UPDATE actions SET state='manually_resolved',data=?,updated=? WHERE id=?",
                            (json.dumps(data, ensure_ascii=False), time.time(), aid))

    def close(self):
        with self.lock:
            self.db.close()
