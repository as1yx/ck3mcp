import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import Config, Session


def main():
    parser = argparse.ArgumentParser(description=f"CK3 AI Agent v{__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="显式启动游戏操作")
    run.add_argument("--config", default="config.json")
    run.add_argument("--start-delay", type=float, default=8, help="留出切换到游戏的时间")
    stop = sub.add_parser("stop", help="为一个运行目录创建 STOP 文件")
    stop.add_argument("run_directory")
    pending = sub.add_parser("pending", help="列出历史未确认动作，不控制游戏")
    pending.add_argument("--config", default="config.json")
    reconcile = sub.add_parser("reconcile", help="停止 Agent 后，记录人工核查结论；不重放动作")
    reconcile.add_argument("action_id")
    reconcile.add_argument("outcome", choices=("success", "failed", "not_executed"))
    reconcile.add_argument("--note", required=True)
    reconcile.add_argument("--config", default="config.json")
    args = parser.parse_args()
    if args.command in ("pending", "reconcile"):
        from .journal import Journal
        cfg = Config.load(args.config)
        Path(cfg.run_directory).mkdir(parents=True, exist_ok=True)
        journal = Journal(Path(cfg.run_directory) / "actions.sqlite3")
        try:
            if args.command == "reconcile":
                journal.resolve(args.action_id, args.outcome, args.note)
            print(json.dumps(journal.unresolved(), ensure_ascii=False, indent=2))
        finally:
            journal.close()
        return
    if args.command == "stop":
        path = Path(args.run_directory).resolve()
        if not (path / "config.json").is_file() or not (path / "events.jsonl").is_file():
            parser.error("不是有效的运行目录")
        (path / "STOP").touch()
        return
    if sys.platform != "win32":
        parser.error("游戏控制仅支持 Windows")
    if not 0 <= args.start_delay <= 60:
        parser.error("启动延迟必须在 0 到 60 秒")
    from .windows import WindowsGame
    from .runner import Runner
    s = Session(Config.load(args.config))
    game = None
    try:
        s.log("created", version=__version__, validation="live_unverified")
        print(f"运行目录：{s.path}\n{args.start_delay} 秒后绑定前台 CK3；F12 紧急停止。", flush=True)
        s.wait(args.start_delay)
        s.ensure_reconciled()
        game = WindowsGame(s)
        Runner(s, game).run()
    except KeyboardInterrupt:
        s.stop("Ctrl+C")
    except Exception as exc:
        s.stop("startup_" + type(exc).__name__)
        print(str(exc), file=sys.stderr)
    finally:
        s.stop("cli_finished")
        s.cleanup(*([game.close] if game else []), s.journal.close)
        print(json.dumps(s.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
