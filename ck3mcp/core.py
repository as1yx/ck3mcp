from __future__ import annotations

import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path


class Stopped(RuntimeError):
    pass


class Replan(RuntimeError):
    pass


class ReplanPolicy:
    """One consecutive preflight rejection policy shared by every input entry point."""
    def __init__(self, session):
        self.session = session
        self.consecutive = 0

    def rejected(self, error):
        self.session.check()
        self.consecutive += 1
        self.session.log("replan", reason=str(error), attempt=self.consecutive,
                         limit=self.session.cfg.max_replans)
        if self.consecutive >= self.session.cfg.max_replans:
            self.session.stop("replan_limit")
            self.session.check()

    def dispatched(self):
        # Only an actual successful dispatch clears the streak; screenshots,
        # model calls and arbitrary caller retries cannot reset this limit.
        self.consecutive = 0


@dataclass
class Config:
    process_name: str = "ck3.exe"
    model: str = "REPLACE_WITH_VISION_MODEL_ID"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "CK3_API_KEY"
    request_timeout_seconds: float = 45
    max_runtime_seconds: float = 1800
    max_calls: int = 100
    max_tokens: int = 100000
    max_output_tokens: int = 1200
    input_reserve_tokens: int = 16000
    max_steps: int = 40
    observation_ttl_seconds: float = 15
    max_wait_seconds: float = 2
    max_replans: int = 3
    scene_difference_limit: float = 0.008
    task: str = "任命一个空缺内阁职位"
    run_directory: str = "runs"

    @classmethod
    def load(cls, filename: str):
        path = Path(filename).resolve()
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        allowed = {f.name for f in fields(cls)}
        if set(raw) - allowed:
            raise ValueError("未知配置字段: " + str(set(raw) - allowed))
        cfg = cls(**raw)
        for name in ("request_timeout_seconds", "max_runtime_seconds", "max_calls",
                     "max_tokens", "max_output_tokens", "input_reserve_tokens", "max_steps",
                     "observation_ttl_seconds", "max_wait_seconds", "max_replans", "scene_difference_limit"):
            value = getattr(cfg, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是正数")
        for name in ("max_calls", "max_tokens", "max_output_tokens", "input_reserve_tokens", "max_steps", "max_replans"):
            if not isinstance(getattr(cfg, name), int):
                raise ValueError(f"{name} 必须是整数")
        if cfg.process_name.lower() != "ck3.exe":
            raise ValueError("此版本只允许绑定 ck3.exe")
        cfg.run_directory = str((path.parent / cfg.run_directory).resolve())
        if cfg.scene_difference_limit > 0.05:
            raise ValueError("画面差异阈值不得超过 0.05")
        return cfg


class Session:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.id = uuid.uuid4().hex
        self.path = Path(cfg.run_directory) / self.id
        self.path.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.log_lock = threading.Lock()
        self.reason = None
        self.calls = 0
        self.tokens = 0
        self.status = "idle"
        self.actions = {}
        self.leases = {}
        self.cleanup_errors = []
        self.replanning = ReplanPolicy(self)
        from .journal import Journal
        self.journal = Journal(Path(cfg.run_directory) / "actions.sqlite3")
        self.stop_file = self.path / "STOP"
        try:
            (self.path / "config.json").write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
        except BaseException:
            self.cleanup(self.journal.close)
            raise

    def log(self, kind, **data):
        record = {"time": time.time(), "run_id": self.id, "kind": kind, **data}
        with self.log_lock:
            with (self.path / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stop(self, reason="manual_stop"):
        with self.lock:
            if not self.stop_event.is_set():
                self.reason = reason
                self.status = ("completed" if reason == "task_completed_visual_claim" else
                               "budget_exhausted" if "budget" in reason else
                               "cancelled" if reason in ("F12", "Ctrl+C", "stop_file", "MCP_emergency_stop") else "stopped")
                self.stop_event.set()
                # Unconsumed calls can no longer start once stopped.
                self.tokens -= sum(self.leases.values())
                self.leases.clear()
                self.cleanup(lambda: self.log("stop", reason=reason, stop_requested=True, game_paused="unknown"))

    def cleanup(self, *operations):
        """Attempt every cleanup even if logging or another resource fails."""
        for operation in operations:
            try:
                operation()
            except BaseException as exc:
                self.cleanup_errors.append({"operation": getattr(operation, "__name__", "cleanup"),
                                            "error_type": type(exc).__name__})

    def check(self):
        if time.monotonic() - self.started >= self.cfg.max_runtime_seconds:
            self.stop("runtime_budget")
        if self.stop_file.exists():
            self.stop("stop_file")
        if self.stop_event.is_set():
            raise Stopped(self.reason)

    def reserve_call(self, purpose="model"):
        return self.reserve_calls(purpose)[0]

    def reserve_calls(self, *purposes):
        """Reserve an entire verification sequence before allowing its input."""
        with self.lock:
            self.check()
            reserve = self.cfg.input_reserve_tokens + self.cfg.max_output_tokens
            count = len(purposes)
            if self.calls + len(self.leases) + count > self.cfg.max_calls or self.tokens + reserve * count > self.cfg.max_tokens:
                self.stop("model_budget")
                raise Stopped(self.reason)
            leases = [uuid.uuid4().hex for _ in purposes]
            for lease in leases:
                self.leases[lease] = reserve
            self.tokens += reserve * count
            try:
                for lease, purpose in zip(leases, purposes):
                    self.log("budget_reserved", lease=lease, purpose=purpose, tokens=reserve)
            except BaseException:
                for lease in leases:
                    self.release_call(lease)
                self.stop("reservation_log_failed")
                raise
            return leases

    def consume_call(self, lease):
        with self.lock:
            self.check()
            reserve = self.leases.pop(lease)
            self.calls += 1
            return reserve

    def release_call(self, lease):
        with self.lock:
            self.tokens -= self.leases.pop(lease, 0)

    def settle_call(self, reserve, total):
        with self.lock:
            if type(total) is int and total >= 0:
                self.tokens += total - reserve

    def refund_unstarted_call(self, reserve):
        with self.lock:
            self.calls -= 1
            self.tokens -= reserve

    def ensure_reconciled(self):
        pending = self.journal.unresolved()
        if pending:
            self.log("reconciliation_required", actions=pending)
            raise RuntimeError("存在历史未确认动作，先用 pending/reconcile 命令人工核查，禁止自动重放")

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            self.stop_event.wait(min(0.05, max(0, end - time.monotonic())))

    def snapshot(self):
        return {"run_id": self.id, "status": self.status, "reason": self.reason,
                "calls": self.calls, "tokens_used_or_reserved": self.tokens,
                "reserved_calls": len(self.leases),
                "cleanup_errors": list(self.cleanup_errors),
                "consecutive_replans": self.replanning.consecutive,
                "directory": str(self.path), "game_paused": "unknown"}
