"""航次管理业务规则：状态流转、时间补录重排、操作留痕与船舶港态同步都收在这里。

状态序列只能单向前进：待开航 → 航行中 → 已到港 → 已结航（归档）。
线下先确认、事后补录的开航/到港/结航时间通过 backfill_times 一次性重排，
每次状态变更都会写入流转记录（操作人、时间、来源），并同步关联船舶的在港状态。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "voyage"
VESSEL_MODULE = "vessel"
REQUIRED_FIELDS = ["航次编号", "关联船舶", "进口航次号"]
OPTIONAL_FIELDS = ["出口航次号", "预计到港", "实际开航", "实际到港", "结航时间", "航线名称"]

STATUS_PENDING = "待开航"
STATUS_SAILING = "航行中"
STATUS_ARRIVED = "已到港"
STATUS_CLOSED = "已结航"
STATUS_ORDER = [STATUS_PENDING, STATUS_SAILING, STATUS_ARRIVED, STATUS_CLOSED]

# 各动作对应的目标档位；是否允许执行还要看当前档位与时间校验。
ACTION_RULES = {"确认开航": STATUS_SAILING, "确认到港": STATUS_ARRIVED, "结航航次": STATUS_CLOSED}
NEGATIVE_ACTIONS = []

TIME_FIELDS = ["预计到港", "实际开航", "实际到港", "结航时间"]
DEFAULT_OPERATOR = "值班管理员"
_TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _parse_time(raw: Any) -> datetime | None:
    """把页面补录的时间解析成 datetime；空串按缺失处理，无法识别返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


class VoyageService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("航次编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if str(values.get(field) or "").strip():
                entry[field] = str(values[field]).strip()
        entry["status"] = STATUS_ORDER[0]
        entry["航次状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["流转记录"] = []
        rows.append(entry)
        return entry, []

    # ---- 状态动作 --------------------------------------------------------

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """执行单个状态动作。

        返回 (航次, 说明, 是否生效)；重复结航时 ok=True 但 changed=False，不再写记录。
        """
        values = values or {}
        operator = str(values.get("操作人") or DEFAULT_OPERATOR).strip() or DEFAULT_OPERATOR
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航次管理可执行范围", False

        target = ACTION_RULES[action]
        current = str(entry.get("status") or STATUS_PENDING)
        current_rank = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
        target_rank = STATUS_ORDER.index(target)

        # 已结航幂等：同一航次重复结航只首次生效，重复调用只回读说明。
        if action == "结航航次" and current == STATUS_CLOSED:
            last = self._last_close_record(entry)
            who = f"，首次结航由{last['操作人']}于{last['时间']}操作" if last else ""
            return entry, f"航次已结航并归档{who}，重复结航不再生效", False

        if target_rank <= current_rank:
            return None, f"航次当前为「{current}」，状态只能前进不能退回，无法执行{action}", False

        if action == "确认开航":
            error = self._fill_stage_time(entry, "实际开航", values)
            if error:
                return None, error, False
            error = self._validate_order(entry)
            if error:
                return None, error, False
            note = self._advance(entry, STATUS_SAILING, "确认开航", entry["实际开航"], operator, "动作确认")
            return entry, f"航次已确认开航，进入航行中{note}", True

        if action == "确认到港":
            if current != STATUS_SAILING:
                return None, f"航次当前为「{current}」，请先确认开航后再确认到港", False
            error = self._fill_stage_time(entry, "实际到港", values)
            if error:
                return None, error, False
            error = self._validate_order(entry)
            if error:
                return None, error, False
            note = self._advance(entry, STATUS_ARRIVED, "确认到港", entry["实际到港"], operator, "动作确认")
            return entry, f"航次已确认到港{note}", True

        # 结航航次：结航时间未补录时按当前操作时间归档，操作人与时间照常留痕。
        supplied = str(values.get("结航时间") or entry.get("结航时间") or "").strip()
        parsed = _parse_time(supplied) if supplied else None
        if supplied and parsed is None:
            return None, f"「结航时间」的时间无法识别（{supplied}），应为 YYYY-MM-DD HH:MM 格式"
        if parsed is None:
            close_str = _now_str()
            close_defaulted = True
        else:
            close_str = _format_time(parsed)
            close_defaulted = False
        tentative = dict(entry)
        tentative["结航时间"] = close_str
        error = self._validate_order(tentative)
        if error:
            reason = f"按当前时间结航（{close_str}）早于实际到港，请在「补录时间」里补录不早于到港的结航时间后再结航" if close_defaulted else error
            return None, reason, False
        entry["结航时间"] = close_str
        note = self._advance(entry, STATUS_CLOSED, "结航航次", close_str, operator, "动作确认")
        return entry, f"航次已结航归档{note}", True

    def backfill_times(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """线下确认后补录时间：按开航/到港/结航时间把状态一次性重排到应有档位。"""
        operator = str(values.get("操作人") or DEFAULT_OPERATOR).strip() or DEFAULT_OPERATOR
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档"
        if str(entry.get("status") or "") == STATUS_CLOSED:
            return None, "航次已结航归档，时间与状态均不可再修改"

        # 先把「补录后」的完整时间链路组装出来，再统一校验，避免已存在的开/到港时间被当成缺失。
        staged: dict[str, str] = {}
        for field in TIME_FIELDS:
            raw = values.get(field, entry.get(field))
            parsed = _parse_time(raw)
            if str(raw or "").strip() and parsed is None:
                return None, f"「{field}」的时间无法识别（{str(raw).strip()}），应为 YYYY-MM-DD HH:MM 格式"
            if parsed is not None:
                staged[field] = _format_time(parsed)

        tentative = dict(entry)
        tentative.update(staged)

        # 时间链路缺段时说明原因并拒绝，不允许跳过开航直接到港。
        if tentative.get("结航时间") and not tentative.get("实际到港"):
            return None, "已补录结航时间但缺少实际到港时间，无法重排，请先补齐到港时间"
        if tentative.get("实际到港") and not tentative.get("实际开航"):
            return None, "已补录实际到港时间但缺少实际开航时间，无法判断到港是否早于开航，请先补齐"

        error = self._validate_order(tentative)
        if error:
            return None, error

        if "结航时间" in staged:
            target_rank = 3
        elif "实际到港" in staged:
            target_rank = 2
        elif "实际开航" in staged:
            target_rank = 1
        else:
            target_rank = 0

        current = str(entry.get("status") or STATUS_PENDING)
        current_rank = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
        if target_rank < current_rank:
            return (
                None,
                f"按补录时间重算应为「{STATUS_ORDER[target_rank]}」，早于当前状态「{current}」，状态不能退回上一档",
            )

        # 预计到港等字段无论是否触发流转都落库，保证补录内容不丢。
        entry.update(staged)

        changed = False
        if target_rank >= 1 and current_rank < 1:
            self._advance(entry, STATUS_SAILING, "确认开航", staged["实际开航"], operator, "时间补录")
            changed = True
        if target_rank >= 2 and current_rank < 2:
            self._advance(entry, STATUS_ARRIVED, "确认到港", staged["实际到港"], operator, "时间补录")
            changed = True
        if target_rank >= 3 and current_rank < 3:
            self._advance(entry, STATUS_CLOSED, "结航航次", staged["结航时间"], operator, "时间补录")
            changed = True

        if changed:
            return entry, f"时间补录完成，航次状态已重排为「{entry['status']}」"
        return entry, "时间补录已保存，航次状态未发生变化（状态不能退回上一档）"

    # ---- 内部规则 --------------------------------------------------------

    def _fill_stage_time(self, entry: dict[str, Any], field: str, values: dict[str, Any]) -> str | None:
        """取本次提交或已登记的时间写入航次；缺失或不可识别时返回拒绝原因。"""
        raw = values.get(field, entry.get(field))
        parsed = _parse_time(raw)
        if parsed is None:
            if not str(raw or "").strip():
                return f"{field}缺失，请先在「补录时间」里补齐后再操作"
            return f"「{field}」的时间无法识别（{str(raw).strip()}），应为 YYYY-MM-DD HH:MM 格式"
        entry[field] = _format_time(parsed)
        return None

    @staticmethod
    def _validate_order(data: dict[str, Any]) -> str | None:
        """校验开航 → 到港 → 结航的时间先后；实际到港早于开航直接拒绝。"""
        departure = _parse_time(data.get("实际开航"))
        arrival = _parse_time(data.get("实际到港"))
        closing = _parse_time(data.get("结航时间"))
        if departure and arrival and arrival < departure:
            return (
                f"实际到港（{_format_time(arrival)}）早于实际开航（{_format_time(departure)}），"
                "时间顺序不合法，已拒绝该次变更"
            )
        if arrival and closing and closing < arrival:
            return (
                f"结航时间（{_format_time(closing)}）早于实际到港（{_format_time(arrival)}），"
                "时间顺序不合法，已拒绝该次变更"
            )
        if departure and closing and closing < departure:
            return (
                f"结航时间（{_format_time(closing)}）早于实际开航（{_format_time(departure)}），"
                "时间顺序不合法，已拒绝该次变更"
            )
        return None

    def _advance(
        self,
        entry: dict[str, Any],
        target: str,
        action: str,
        occurred_at: str,
        operator: str,
        source: str,
    ) -> str:
        """推进状态、写流转记录，并同步关联船舶在港状态。"""
        previous = str(entry.get("status") or STATUS_PENDING)
        entry["status"] = target
        entry["航次状态"] = target
        entry["pending"] = target != STATUS_CLOSED
        entry["abnormal"] = False
        history = entry.setdefault("流转记录", [])
        history.append({
            "动作": action,
            "原状态": previous,
            "新状态": target,
            "时间": occurred_at,
            "操作人": operator,
            "来源": source,
        })
        return self._sync_vessel(entry, in_port=target == STATUS_ARRIVED, operator=operator, occurred_at=occurred_at)

    @staticmethod
    def _sync_vessel(entry: dict[str, Any], *, in_port: bool, operator: str, occurred_at: str) -> str:
        """到港置在港、开航/结航置离港；停用或未登记的船舶只记录港态，不强改其业务状态。"""
        name = str(entry.get("关联船舶") or "").strip()
        if not name:
            return "；航次未关联船舶，港态未同步"
        vessel = next(
            (row for row in store.rows(VESSEL_MODULE) if str(row.get("船舶名称") or "").strip() == name),
            None,
        )
        if vessel is None:
            return f"；未找到船舶「{name}」档案，在港状态未能同步"
        vessel["在港"] = in_port
        vessel["港态同步航次"] = entry.get("航次编号")
        vessel["港态同步人"] = operator
        vessel["港态同步时间"] = occurred_at
        if vessel.get("status") not in ("待登记", "已停用"):
            vessel["status"] = "在港作业" if in_port else "在册可用"
        return f"；船舶「{name}」已同步为{'在港' if in_port else '离港'}"

    @staticmethod
    def _last_close_record(entry: dict[str, Any]) -> dict[str, Any] | None:
        for record in reversed(entry.get("流转记录", [])):
            if record.get("动作") == "结航航次":
                return record
        return None
