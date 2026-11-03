"""Code-defined stage postconditions; visual facts remain model claims."""
import json
from collections import deque
from .verification import TargetMismatch


class CouncilTask:
    stages = ("open_council", "choose_vacancy", "choose_candidate", "submit_assignment", "verify_assignment")
    # These conditions are supplied to the verifier by code, never by the planner.
    conditions = (
        "内阁面板可见：council_visible=true，并给出 evidence。",
        "空缺职位的候选人列表可见：candidates_visible=true, vacant=true, slot 为具体职位，给出 vacancy_evidence。",
        "候选人已选中且仍未提交：selected_candidate 为具体人物, eligible=true, vacant=true, selection_confirmed=true, confirmation_visible=true, slot 与目标一致，给出 candidate_evidence。",
        "已任命：appointed_slot 和 appointed_candidate 与固定目标一致，并给出 appointment_evidence。",
        "已任命：appointed_slot 和 appointed_candidate 与固定目标一致，并给出 appointment_evidence。",
    )

    def __init__(self):
        self.index, self.slot, self.candidate = 0, None, None

    def snapshot(self):
        return {"stage": self.stages[self.index], "slot": self.slot, "candidate": self.candidate}

    def verification_prompt(self):
        return ("必须独立报告当前阶段的事实，不采纳规划者自报的 advance。固定任务状态：" +
                json.dumps(self.snapshot(), ensure_ascii=False) + "\n阶段后置条件：" + self.conditions[self.index] +
                '\n返回 JSON {"status":"success|failed|unknown", "evidence":"操作结果证据", "facts":{'
                '"panel":"面板名", "panel_evidence":"面板证据", "council_visible":true或false,'
                '"candidates_visible":true或false, "slot":"职位或null", "vacant":true或false或null,'
                '"vacancy_evidence":"证据或null", "visible_candidates":["按界面顺序的名字"],'
                '"list_position":"可见首尾人物/滚动位置或null", "inspected_candidate":"当前提示人物或null",'
                '"inspected_details":{"界面字段名":"按界面原样记录的值"}, "selected_candidate":"人物或null",'
                '"selection_confirmed":true或false, "eligible":true或false或null,'
                '"confirmation_visible":true或false, "candidate_evidence":"证据或null",'
                '"appointed_slot":"职位或null", "appointed_candidate":"人物或null",'
                '"appointment_evidence":"证据或null"}}。所有字段均须返回；未知使用 null，不补猜。'
                'selected_candidate 只表示已选中人物，不能填悬停查看的人；appointed 字段只记录本任务职位的任职结果，其他职位不用填。')

    def validate(self, decision):
        task = decision.get("task", {})
        if not isinstance(task, dict) or task.get("stage") != self.stages[self.index]:
            raise ValueError("任务阶段不匹配")
        if decision.get("decision") == "complete":
            if self.index != 4:
                raise ValueError("尚未到最终核验阶段")
            return
        action = decision.get("action", {})
        if not isinstance(action, dict) or self.index == 4:
            raise ValueError("提交后只能核验或停止")
        if not isinstance(task.get("panel"), str) or not task["panel"]:
            raise ValueError("缺少当前面板")
        intent = task.get("intent")
        if not isinstance(intent, dict) or intent.get("kind") not in ("navigate", "browse", "select_slot", "select_candidate", "submit"):
            raise ValueError("必须在输入前声明 task.intent")
        kind = intent["kind"]
        allowed = ({"navigate", "browse"}, {"navigate", "browse", "select_slot"},
                   {"navigate", "browse", "select_candidate"}, {"submit"})
        if kind not in allowed[self.index]:
            raise ValueError("本阶段不允许该选择意图")
        if kind in ("select_slot", "select_candidate", "submit"):
            if action.get("type") != "click" or not isinstance(intent.get("slot"), str) or not intent["slot"].strip():
                raise ValueError("选择/提交必须是带预期职位的点击")
        if kind in ("select_candidate", "submit"):
            if not isinstance(intent.get("candidate"), str) or not intent["candidate"].strip():
                raise ValueError("选择/提交必须声明预期人物")
        if self.slot and kind in ("select_candidate", "submit") and intent["slot"] != self.slot:
            raise ValueError("选择意图中的职位与固定职位不一致")
        # Browsing a person or position never fixes the target. Only verified
        # stage postconditions below set self.slot/self.candidate.
        if self.slot and task.get("slot") != self.slot:
            raise ValueError("目标职位不匹配")
        if self.index == 3:
            if task.get("candidate") != self.candidate or not self.slot or not self.candidate:
                raise ValueError("提交目标不匹配")
            if intent.get("candidate") != self.candidate:
                raise ValueError("提交意图与固定候选人不一致")
            if action.get("type") != "click" or decision.get("irreversible") is not True:
                raise ValueError("提交必须是明确的单次点击")
        elif decision.get("irreversible") is True:
            raise ValueError("尚未进入提交阶段")

    def check_targets(self, facts, intent):
        """Check known contradictions even if other postconditions are false/unknown."""
        kind = intent.get("kind") if isinstance(intent, dict) else None
        expected_slot = intent.get("slot") if kind in ("select_slot", "select_candidate", "submit") else self.slot
        if expected_slot and facts.get("slot") is not None and facts["slot"] != expected_slot:
            raise TargetMismatch("实际职位与固定/本次预期职位不一致")
        expected_candidate = intent.get("candidate") if kind in ("select_candidate", "submit") else self.candidate
        if expected_candidate and facts.get("selected_candidate") is not None and facts["selected_candidate"] != expected_candidate:
            raise TargetMismatch("实际选中人物与本次预期人物不一致")
        if self.index in (3, 4):
            if facts.get("appointed_slot") is not None and facts["appointed_slot"] != self.slot:
                raise TargetMismatch("实际任职职位不一致")
            if facts.get("appointed_candidate") is not None and facts["appointed_candidate"] != self.candidate:
                raise TargetMismatch("实际任职人物不一致")
        elif self.index in (1, 2) and facts.get("appointed_slot") is not None and facts.get("appointed_candidate") is not None:
            raise TargetMismatch("正式提交前出现任职结果，需人工核查，不能重试选择")

    def accept(self, verdict, intent=None):
        """Return whether a code-defined postcondition passed. No advance flag used."""
        if verdict.get("status") != "success" or not verdict.get("evidence"):
            return False
        f = verdict.get("facts")
        if not isinstance(f, dict):
            return False
        self.check_targets(f, intent)
        if self.index == 0:
            met = f.get("council_visible") is True and bool(f.get("panel_evidence"))
        elif self.index == 1:
            met = (f.get("candidates_visible") is True and f.get("vacant") is True and
                   isinstance(f.get("slot"), str) and bool(f["slot"].strip()) and bool(f.get("vacancy_evidence")))
            if met:
                if not intent or intent.get("kind") != "select_slot" or f["slot"] != intent.get("slot"):
                    raise TargetMismatch("实际打开的职位与本次选择意图不一致")
                self.slot = f["slot"]
        elif self.index == 2:
            met = (f.get("slot") == self.slot and f.get("vacant") is True and f.get("selection_confirmed") is True and
                   f.get("confirmation_visible") is True and f.get("eligible") is True and
                   isinstance(f.get("selected_candidate"), str) and bool(f["selected_candidate"].strip()) and
                   bool(f.get("candidate_evidence")))
            if met:
                if not intent or intent.get("kind") != "select_candidate" or f["selected_candidate"] != intent.get("candidate") or f["slot"] != intent.get("slot"):
                    raise TargetMismatch("实际选中人物/职位与本次选择意图不一致")
                self.candidate = f["selected_candidate"]
        else:
            met = (f.get("appointed_slot") == self.slot and f.get("appointed_candidate") == self.candidate and
                   bool(f.get("appointment_evidence")))
        kind = intent.get("kind") if isinstance(intent, dict) else None
        if kind in ("select_slot", "select_candidate", "submit") and not met:
            raise ValueError("明确选择/提交动作未满足后置条件，不能作为成功导航继续")
        if met:
            self.index = min(4, self.index + 1)
        return bool(met)


class ProgressTracker:
    """Observe POST-action semantic states, not planned input coordinates or pixels."""
    def __init__(self):
        self.states = deque(maxlen=6)

    def observe(self, task, facts):
        if not isinstance(facts, dict) or not isinstance(facts.get("panel"), str) or not facts.get("panel_evidence"):
            raise ValueError("缺少可用的执行后进度事实")
        people = facts.get("visible_candidates", [])
        if not isinstance(people, list) or not all(isinstance(p, str) for p in people):
            raise ValueError("候选人列表格式无效")
        state = {"task": task, "panel": facts["panel"], "people": people,
                 "position": facts.get("list_position"), "selected": facts.get("selected_candidate"),
                 "inspected": facts.get("inspected_candidate"), "details": facts.get("inspected_details"),
                 "confirmation": facts.get("confirmation_visible"), "appointed_slot": facts.get("appointed_slot"),
                 "appointed_candidate": facts.get("appointed_candidate")}
        self.states.append(json.dumps(state, sort_keys=True, ensure_ascii=False))
        values = list(self.states)
        if len(values) >= 3 and len(set(values[-3:])) == 1:
            return "no_task_progress"
        if len(values) == 6 and values[:2] * 3 == values:
            return "navigation_cycle"
        return None


TASK_INSTRUCTIONS = """
严格遵守 stage：open_council 打开内阁；choose_vacancy 查看空缺职位并打开候选人列表；
choose_candidate 可以逐个悬停、比较人选，最终选择一个人并停在尚未提交的确认界面；
submit_assignment 仅点击一次最终任命按钮，irreversible=true；verify_assignment 仅核验结果。
输出 task={stage,panel,slot,candidate,intent:{kind,slot,candidate}}。
intent.kind 是 navigate/browse/select_slot/select_candidate/submit；选择动作必须在点击前声明预期职位和人物。
浏览不锁定目标；实际选中目标与本次选择意图不一致时程序会停止，不能将点错的人变为新目标。
不输出或依赖 advance，阶段推进由执行后的事实核验决定。
若界面选择人物会立即任命、没有独立确认步骤，必须 stop，不猜测或试点提交。
"""
