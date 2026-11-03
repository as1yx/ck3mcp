"""Shared validation and side-effect-free interpretation before durable success."""
import copy


class TargetMismatch(ValueError):
    pass


def validate_facts(value):
    if not isinstance(value, dict):
        raise ValueError("facts 必须是对象")
    facts = dict(value)
    for key in ("panel", "panel_evidence"):
        if not isinstance(facts.get(key), str) or not facts[key].strip():
            raise ValueError(key + " 必须是非空字符串")
    for key in ("council_visible", "candidates_visible", "vacant", "selection_confirmed", "eligible", "confirmation_visible"):
        if key not in facts or (facts[key] is not None and type(facts[key]) is not bool):
            raise ValueError(key + " 必须是布尔值或 null")
    for key in ("slot", "vacancy_evidence", "list_position", "inspected_candidate", "selected_candidate",
                "candidate_evidence", "appointed_slot", "appointed_candidate", "appointment_evidence"):
        if key not in facts or (facts[key] is not None and (not isinstance(facts[key], str) or not facts[key].strip())):
            raise ValueError(key + " 必须是非空字符串或 null")
    if "visible_candidates" not in facts or "inspected_details" not in facts:
        raise ValueError("缺少列表或查看详情字段")
    people = facts.get("visible_candidates")
    if people is None and facts["candidates_visible"] is not True:
        people = []
    if not isinstance(people, list) or not all(isinstance(p, str) and p.strip() for p in people):
        raise ValueError("候选列表可见时 visible_candidates 必须是字符串数组")
    facts["visible_candidates"] = people
    details = facts.get("inspected_details")
    if details is None:
        details = {}
    if not isinstance(details, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in details.items()):
        raise ValueError("inspected_details 必须是字符串映射或 null")
    facts["inspected_details"] = details
    return facts


def interpret(task, verdict, intent):
    """Never mutate the live task. Caller persists result before applying next_task."""
    result = {"status": "unknown", "evidence": None, "facts": None, "advanced": False,
              "next_task": None, "error": None}
    try:
        if not isinstance(verdict, dict) or verdict.get("status") not in ("success", "failed", "unknown"):
            raise ValueError("核验状态无效")
        evidence = verdict.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("核验证据必须是非空字符串")
        facts = validate_facts(verdict.get("facts"))
        result.update(status=verdict["status"], evidence=evidence, facts=facts)
        # Known contradictions override success/failed labels before any phase
        # condition or early return. Unknown fields do not count as matches.
        task.check_targets(facts, intent)
        if result["status"] != "success":
            return result
        proposed = copy.copy(task)
        advanced = proposed.accept({"status": "success", "evidence": evidence, "facts": facts}, intent)
        if task.snapshot()["stage"] in ("submit_assignment", "verify_assignment") and not advanced:
            raise ValueError("未满足任职结果后置条件")
        result.update(advanced=advanced, next_task=proposed)
    except (ValueError, TypeError) as exc:
        result.update(status="unknown", next_task=None, advanced=False, error=str(exc))
    return result
