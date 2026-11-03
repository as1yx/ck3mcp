from __future__ import annotations

import json

from .core import Stopped, Replan
from .model import VisionModel
from .tasks import CouncilTask, ProgressTracker, TASK_INSTRUCTIONS
from .verification import interpret

RULES = """只通过正常 CK3 UI 任命一个空缺内阁职位。禁止替换在任人物、宣战、控制台、退出、读档。
游戏必须已暂停。无法确认、未知弹窗、人物死亡或继承时 stop。
每次一个原子动作：click(x,y,button=left/right)、hover(x,y)、scroll(x,y,direction=up/down,amount=1..5)、key(key=escape)、wait(seconds<=2)。
JSON 输出 {decision:act/stop,paused:true/false/unknown,pause_evidence:可见证据,
reason:简短理由,expected:可观察结果,irreversible:布尔值,action:动作对象,task:阶段信息}。
坐标是截图客户区整数像素。不能用置信度代替具体证据。
"""


class Runner:
    def __init__(self, session, game):
        self.s, self.game = session, game
        self.model = VisionModel(session)
        self.history = []
        self.task = CouncilTask()

    def run(self):
        self.s.status = "running"
        progress = ProgressTracker()
        pending, lease, final_lease, phase = None, None, None, "startup"
        submission = None
        steps = 0
        try:
            self.s.ensure_reconciled()
            while True:
                self.s.check()
                # Final verification needs no planner and is allowed after the
                # last input step. Its call was reserved before submission.
                if self.task.snapshot()["stage"] == "verify_assignment":
                    phase = "completion_verification"
                    if final_lease is None:
                        raise RuntimeError("缺少提交前预留的最终核验额度")
                    if submission is None:
                        raise RuntimeError("缺少待最终核验的提交记录")
                    fresh = self.game.capture()
                    confirmation = self.model.ask(self.task.verification_prompt(), [fresh], lease=final_lease)
                    final_lease = None
                    self.s.log("task_verification", result=confirmation)
                    final = interpret(self.task, confirmation, None)
                    self.s.journal.record_final_verification(submission["action_id"], self.s.id,
                                                            submission, confirmation, final, fresh)
                    self.s.log("task_verification_validation", status=final["status"], error=final["error"])
                    self.s.stop("task_completed_visual_claim" if final["status"] == "success" and final["advanced"]
                                else "task_completion_unconfirmed")
                    break
                if steps >= self.s.cfg.max_steps:
                    self.s.stop("step_budget")
                    break
                phase = "observe"
                before = self.game.capture()
                phase = "plan"
                decision = self.model.ask(RULES + TASK_INSTRUCTIONS + "\n用户偏好：" + self.s.cfg.task +
                    "\n任务状态：" + json.dumps(self.task.snapshot(), ensure_ascii=False) +
                    "\n最近操作：" + json.dumps(self.history[-6:], ensure_ascii=False), [before])
                self.s.log("decision", step=steps, observation_id=before["observation_id"], decision=decision)
                mode = decision.get("decision")
                if mode == "stop":
                    self.s.stop("model_uncertain")
                    break
                if decision.get("paused") is not True or not decision.get("pause_evidence"):
                    self.s.stop("pause_unconfirmed_manual_pause_required")
                    break
                self.task.validate(decision)
                action = decision.get("action")
                if mode != "act" or not isinstance(action, dict) or not decision.get("expected"):
                    raise ValueError("决策结构不完整")
                action["contract"] = {"paused": True, "pause_evidence": decision["pause_evidence"],
                                      "expected": decision["expected"], "task": self.task.snapshot(),
                                      "intent": decision["task"]["intent"],
                                      "irreversible": decision.get("irreversible") is True}
                purposes = ["post_action_verification"]
                if self.task.snapshot()["stage"] == "submit_assignment":
                    purposes.append("final_task_verification")
                reserved = self.s.reserve_calls(*purposes)
                lease = reserved[0]
                final_lease = reserved[1] if len(reserved) == 2 else None
                phase = "preflight_and_dispatch"
                try:
                    pending = self.game.execute(before["observation_id"], action)
                except Replan as exc:
                    self.s.release_call(lease)
                    lease = None
                    self.s.release_call(final_lease)
                    final_lease = None
                    self.s.replanning.rejected(exc)
                    continue
                self.s.replanning.dispatched()
                steps += 1
                self.s.wait(0.4)
                after = self.game.capture()
                phase = "verify"
                verify = self.model.ask("比较前后截图，输入发出不代表成功。动作意图（仅作参考）：" + decision["expected"] +
                    "\n" + self.task.verification_prompt(),
                    [pending["execution_before"], after], lease=lease)
                lease = None
                outcome = interpret(self.task, verify, decision["task"]["intent"])
                status, advanced = outcome["status"], outcome["advanced"]
                pending.update(status=status, evidence=outcome["evidence"], after=after["observation_id"],
                               facts=outcome["facts"], validation_error=outcome["error"], raw_verdict=verify)
                requires_final = decision["task"]["intent"]["kind"] == "submit"
                self.s.journal.record_action_verification(pending["action_id"], self.s.id, pending,
                                                         requires_final=requires_final)
                if requires_final and status == "success":
                    submission = pending
                self.s.log("verification", **pending)
                self.history.append({"action": action, "expected": decision["expected"], "result": dict(pending)})
                pending = None
                if status != "success":
                    self.s.stop("action_" + status)
                    break
                self.task = outcome["next_task"]
                self.s.log("task_postcondition", met=advanced, task=self.task.snapshot())
                reason = progress.observe(self.task.snapshot(), outcome["facts"])
                if reason:
                    self.s.stop(reason)
                    break
        except KeyboardInterrupt:
            self.s.stop("Ctrl+C")
        except Stopped:
            pass
        except Exception as exc:
            self.s.cleanup(lambda: self.s.log("error", phase=phase, error_type=type(exc).__name__, message=str(exc)[:500]))
            self.s.stop("execution_error_" + type(exc).__name__)
        finally:
            self.s.cleanup(lambda: self.s.release_call(lease), lambda: self.s.release_call(final_lease))
            self.s.stop("runner_finished")
            self.s.cleanup(self.game.close)
            if pending is not None:
                self.s.cleanup(lambda: self.s.log("unresolved_action", **pending))
            report = {**self.s.snapshot(), "steps": steps, "task": self.task.snapshot(), "history": self.history,
                      "submission": submission,
                      "unresolved": self.s.journal.unresolved(), "validation": "v0.4.4 source only; untested; visual claims"}
            (self.s.path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
