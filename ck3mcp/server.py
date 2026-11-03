"""Local stdio MCP, sharing journal, scene guard, budget and council task stages."""
import argparse
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .core import Config, Session, Replan
from .model import VisionModel
from .tasks import CouncilTask, ProgressTracker
from .verification import interpret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    cfg = Config.load(parser.parse_args().config)
    mcp, state, operation_lock = FastMCP("ck3mcp"), {}, threading.Lock()

    def require():
        if "game" not in state:
            raise ValueError("先调用 bind_game；CK3 须在前台")
        return state["session"], state["game"]

    @mcp.tool()
    def bind_game() -> dict:
        """Bind foreground CK3, check unresolved history. Failed startup releases all resources."""
        with operation_lock:
            if state:
                raise ValueError("已经绑定；停止后请重启服务")
            from .windows import WindowsGame
            s, game = Session(cfg), None
            try:
                s.ensure_reconciled()
                game = WindowsGame(s)
                model = VisionModel(s)
            except BaseException:
                s.stop("bind_failed")
                s.cleanup(*([game.close] if game else []), s.journal.close)
                raise
            state.update(session=s, game=game, model=model, task=CouncilTask(), pending=None, steps=0,
                         progress=ProgressTracker(), final_lease=None, submission=None)
            s.status = "running"
            try:
                s.log("bound", version="0.4.4", validation="untested")
            except BaseException:
                s.stop("bind_log_failed")
                s.cleanup(game.close, s.journal.close)
                state.clear()
                raise
            return s.snapshot()

    @mcp.tool()
    def get_game_state() -> dict:
        with operation_lock:
            s, game = require()
            return {**game.capture(), "task": state["task"].snapshot()}

    @mcp.tool()
    def screenshot(observation_id: str) -> Image:
        with operation_lock:
            game = require()[1]
            obs = game.validate(observation_id)
            return Image(data=Path(obs["screenshot"]).read_bytes(), format="png")

    @mcp.tool()
    def execute_action(observation_id: str, action: dict, task: dict, expected: str,
                       pause_evidence: str, irreversible: bool = False) -> dict:
        """Council-only staged action. Requires configured vision API for pause and result checks.

        task: stage,panel,slot,candidate,intent:{kind,slot,candidate}. See AGENTS.md.
        Must call verify_action before the next input. Replan errors require a new observation.
        """
        with operation_lock:
            s, game = require()
            if state["pending"]:
                raise ValueError("上一动作尚未核验")
            if state["steps"] >= cfg.max_steps:
                s.stop("step_budget")
                s.check()
            s.ensure_reconciled()
            if not expected or not pause_evidence:
                raise ValueError("缺少证据或预期结果")
            decision = {"decision": "act", "action": action, "task": task, "irreversible": irreversible}
            state["task"].validate(decision)
            if observation_id not in game.observations:
                raise ValueError("观察已被替代")
            before = game.observations[observation_id][1]
            pause = state["model"].ask('核对暂停状态，输出 {"paused":true|false|"unknown","evidence":"可见证据"}。', [before])
            if pause.get("paused") is not True or not pause.get("evidence"):
                s.stop("pause_unconfirmed")
                s.check()
            purposes = ["MCP_post_action_verification"]
            if state["task"].snapshot()["stage"] == "submit_assignment":
                purposes.append("MCP_final_task_verification")
            reserved = s.reserve_calls(*purposes)
            lease = reserved[0]
            final_lease = reserved[1] if len(reserved) == 2 else None
            try:
                action = dict(action)
                action["contract"] = {"paused": True, "pause_evidence": pause["evidence"],
                                      "expected": expected, "irreversible": irreversible, "task": task,
                                      "intent": task["intent"]}
                result = game.execute(observation_id, action)
            except Replan as exc:
                s.release_call(lease)
                s.release_call(final_lease)
                s.replanning.rejected(exc)
                raise
            except BaseException:
                s.release_call(lease)
                s.release_call(final_lease)
                raise
            state["final_lease"] = final_lease
            state["pending"] = {"result": result, "before": result["execution_before"], "lease": lease,
                                "decision": decision, "expected": expected}
            s.replanning.dispatched()
            state["steps"] += 1
            return result

    @mcp.tool()
    def verify_action(action_id: str) -> dict:
        """Use reserved vision call to compare before/after. Unknown blocks further inputs."""
        with operation_lock:
            s, game = require()
            p = state["pending"]
            if not p or p["result"]["action_id"] != action_id:
                raise ValueError("不是当前待核验动作")
            try:
                s.wait(0.4)
                after = game.capture()
                verdict = state["model"].ask("比较前后截图，动作意图仅作参考：" + p["expected"] +
                    "\n" + state["task"].verification_prompt(),
                    [p["before"], after], lease=p["lease"])
                outcome = interpret(state["task"], verdict, p["decision"]["task"]["intent"])
                status, advanced = outcome["status"], outcome["advanced"]
                p["result"].update(status=status, evidence=outcome["evidence"], after=after["observation_id"],
                                   facts=outcome["facts"], validation_error=outcome["error"], raw_verdict=verdict)
                requires_final = p["decision"]["task"]["intent"]["kind"] == "submit"
                s.journal.record_action_verification(action_id, s.id, p["result"], requires_final=requires_final)
                if requires_final and status == "success":
                    state["submission"] = p["result"]
                s.log("verification", **p["result"])
                if status == "success":
                    state["task"] = outcome["next_task"]
                    s.log("task_postcondition", met=advanced, task=state["task"].snapshot())
                    reason = state["progress"].observe(state["task"].snapshot(), outcome["facts"])
                    if reason:
                        s.stop(reason)
                else:
                    s.stop("action_" + status)
                state["pending"] = None
                return p["result"]
            except BaseException:
                s.stop("verification_interrupted")
                raise
            finally:
                s.release_call(p["lease"])

    @mcp.tool()
    def verify_task() -> dict:
        """After assignment submission, check a fresh screenshot for the fixed person and slot."""
        with operation_lock:
            s, game = require()
            task = state["task"].snapshot()
            if state["pending"] or task["stage"] != "verify_assignment":
                raise ValueError("尚未到最终核验阶段")
            lease = state["final_lease"]
            if lease is None:
                raise ValueError("缺少提交前预留的最终核验额度")
            submission = state["submission"]
            if submission is None:
                raise ValueError("缺少待最终核验的提交记录")
            try:
                fresh = game.capture()
                result = state["model"].ask(state["task"].verification_prompt(), [fresh], lease=lease)
                s.log("task_verification", result=result)
                outcome = interpret(state["task"], result, None)
                s.journal.record_final_verification(submission["action_id"], s.id, submission,
                                                    result, outcome, fresh)
                s.log("task_verification_validation", status=outcome["status"], error=outcome["error"])
                s.stop("task_completed_visual_claim" if outcome["status"] == "success" and outcome["advanced"] else "task_completion_unconfirmed")
                return {**s.snapshot(), "result": result, "submission": submission}
            except BaseException:
                s.stop("task_verification_interrupted")
                raise
            finally:
                s.release_call(lease)
                state["final_lease"] = None

    @mcp.tool()
    def get_run_status() -> dict:
        return {**require()[0].snapshot(), "task": state["task"].snapshot()}

    @mcp.tool()
    def emergency_stop() -> dict:
        s, game = require()
        s.stop("MCP_emergency_stop")
        s.cleanup(game.release)
        return s.snapshot()

    try:
        mcp.run(transport="stdio")
    finally:
        if state:
            state["session"].stop("server_exit")
            state["session"].cleanup(state["game"].close, state["session"].journal.close)


if __name__ == "__main__":
    main()
