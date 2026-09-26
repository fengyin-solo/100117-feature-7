"""航次管理业务规则：状态流转、字段校验、时间补录与船舶在港同步都收在这里。

状态序列（只进不退）：待开航 -> 航行中 -> 已到港 -> 已结航（归档）。

每次状态变更都会在「流转记录」里追加一条操作留痕，包含动作、操作人与操作时间；
业务时间（实际开航/实际到港/结航时间）与留痕时间分开存放，补录时以业务时间为准。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "voyage"
VESSEL_MODULE = "vessel"
REQUIRED_FIELDS = ["航次编号", "关联船舶", "进口航次号"]
STATUS_ORDER = ["待开航", "航行中", "已到港", "已结航"]
# 动作 -> 目标状态：航次状态只能按顺序向前走，不允许跳档、不允许回退
ACTION_RULES = {"确认开航": "航行中", "确认到港": "已到港", "结航航次": "已结航"}
NEGATIVE_ACTIONS = []

# 可补录的业务时间字段
BACKFILL_FIELDS = ["预计到港", "实际开航", "实际到港", "结航时间"]
# 状态 -> 该状态应已具备的业务时间字段
STAGE_TIME_FIELD = {"航行中": "实际开航", "已到港": "实际到港", "已结航": "结航时间"}
STAGE_ACTION = {"航行中": "确认开航", "已到港": "确认到港", "已结航": "结航航次"}


def _now_text() -> str:
    """服务端记录留痕的统一时间口径。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _parse_time(raw: Any) -> datetime | None:
    """把页面/接口传来的时间解析成可比较的 datetime。

    兼容 datetime-local（2026-09-26T08:00）、带秒、纯日期等常见补录格式。
    解析不了时返回 None，由调用方按缺失/格式错误给出可读原因。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    normalized = text.replace("T", " ").replace("/", "-")
    patterns = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for pattern in patterns:
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


def _store_time(raw: Any) -> str:
    """补录时间统一成「YYYY-MM-DD HH:MM」落库；纯日期保留到日。"""
    parsed = _parse_time(raw)
    if parsed is None:
        return str(raw).strip()
    if len(str(raw).strip()) == 10:
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d %H:%M")


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
        # 必填字段与可补录字段都保留，登记后统一停在待开航
        allowed = REQUIRED_FIELDS + ["出口航次号", "预计到港", "实际开航", "实际到港", "结航时间", "航线名称"]
        entry.update({field: values.get(field) for field in allowed if str(values.get(field) or "").strip()})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["航次状态"] = entry["status"]
        entry["archived"] = False
        entry["流转记录"] = []
        rows.append(entry)
        return entry, []

    # ---- 时间校验 -------------------------------------------------------

    def _validate_times(self, values: dict[str, Any]) -> str | None:
        """校验一条航次业务时间的先后顺序，返回第一条可读原因（必填性由各入口自行判断）。"""
        departure = _parse_time(values.get("实际开航"))
        arrival = _parse_time(values.get("实际到港"))
        closing = _parse_time(values.get("结航时间"))

        raw_departure = str(values.get("实际开航") or "").strip()
        if raw_departure and departure is None:
            return f"实际开航时间「{raw_departure}」格式无法识别，请按 YYYY-MM-DD HH:MM 补录"
        raw_arrival = str(values.get("实际到港") or "").strip()
        if raw_arrival and arrival is None:
            return f"实际到港时间「{raw_arrival}」格式无法识别，请按 YYYY-MM-DD HH:MM 补录"

        if arrival is not None and departure is not None and arrival < departure:
            return f"实际到港时间（{_store_time(values.get('实际到港'))}）早于实际开航时间（{_store_time(values.get('实际开航'))}），时间倒挂，已拒绝"
        if closing is not None and arrival is not None and closing < arrival:
            return f"结航时间（{_store_time(values.get('结航时间'))}）早于实际到港时间（{_store_time(values.get('实际到港'))}），时间倒挂，已拒绝"
        return None

    # ---- 状态推进与留痕 -------------------------------------------------

    def _advance(
        self,
        entry: dict[str, Any],
        target: str,
        *,
        operator: str,
        action: str,
        occurred_at: str | None = None,
        source: str = "操作",
    ) -> None:
        """把航次推进到 target 状态并追加一条流转记录（调用方负责保证只进不退）。"""
        from_status = str(entry.get("_last_status") or entry.get("status") or STATUS_ORDER[0])
        entry["status"] = target
        entry["航次状态"] = target
        entry["archived"] = target == STATUS_ORDER[-1]
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = False
        log_time_field = STAGE_TIME_FIELD.get(target)
        if log_time_field and occurred_at and not str(entry.get(log_time_field) or "").strip():
            entry[log_time_field] = occurred_at
        record = {
            "action": action,
            "from": from_status,
            "to": target,
            "operator": operator,
            "operated_at": _now_text(),
            "business_time": occurred_at,
            "source": source,
        }
        entry.setdefault("流转记录", []).append(record)
        entry["_last_status"] = target

    def _sync_vessel(
        self,
        entry: dict[str, Any],
        *,
        in_port: bool,
        operator: str,
        reason: str,
    ) -> str | None:
        """按航次在港状态同步关联船舶；找不到船舶或船舶已停用时给出说明。"""
        vessel_name = str(entry.get("关联船舶") or "").strip()
        if not vessel_name:
            return "关联船舶为空，未能同步船舶在港状态"
        vessels = store.rows(VESSEL_MODULE)
        vessel = next(
            (row for row in vessels if str(row.get("船舶名称") or "").strip() == vessel_name
             or str(row.get("船舶编号") or "").strip() == vessel_name),
            None,
        )
        if vessel is None:
            return f"关联船舶「{vessel_name}」在船舶档案中不存在，在港状态未同步"
        if vessel.get("status") == "已停用":
            return f"关联船舶「{vessel_name}」已停用，跳过在港状态同步"
        vessel["在港"] = in_port
        vessel["船舶在港"] = "在港" if in_port else "离港"
        vessel["在港同步时间"] = _now_text()
        vessel["在港同步人"] = operator
        vessel["在港同步事由"] = f"航次 {entry.get('航次编号', '')} {reason}"
        return None

    # ---- 动作入口 -------------------------------------------------------

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = dict(values or {})
        operator = str(values.pop("operator", "") or values.pop("操作人", "")).strip()
        if not operator:
            return None, "缺少操作人，无法记录本次状态变更"

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航次管理可执行范围"

        current = str(entry.get("status") or STATUS_ORDER[0])
        target = ACTION_RULES[action]
        current_index = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
        target_index = STATUS_ORDER.index(target)

        # 同一航次重复结航（以及已结航后的任何动作）只生效一次，再次执行直接拒绝
        if target_index <= current_index:
            if current == STATUS_ORDER[-1]:
                return None, "该航次已结航归档，结航只生效一次，不能重复操作"
            return None, f"航次当前为「{current}」，不能回退或跳转到「{target}」"
        if target_index != current_index + 1:
            return None, f"航次状态需逐档流转，请先完成「{STATUS_ORDER[current_index + 1]}」"

        # 合并已有业务时间后做整体校验，避免补录/确认时放过倒挂时间
        merged = {
            "预计到港": entry.get("预计到港"),
            "实际开航": entry.get("实际开航"),
            "实际到港": entry.get("实际到港"),
            "结航时间": entry.get("结航时间"),
            **values,
        }

        if action == "确认开航":
            departure_raw = values.get("实际开航") or _now_text()
            merged["实际开航"] = departure_raw
            if _parse_time(departure_raw) is None:
                return None, f"实际开航时间「{departure_raw}」格式无法识别，请按 YYYY-MM-DD HH:MM 填写"
            business_time = _store_time(departure_raw)
            entry["实际开航"] = business_time
        elif action == "确认到港":
            # 没开过航不能到港；实际到港缺失或早于开航都拒绝并说明原因
            departure_raw = values.get("实际开航")
            if departure_raw and str(departure_raw).strip():
                if _parse_time(departure_raw) is None:
                    return None, f"实际开航时间「{departure_raw}」格式无法识别，请按 YYYY-MM-DD HH:MM 补录"
                merged["实际开航"] = departure_raw
            if _parse_time(merged.get("实际开航")) is None:
                return None, "缺少实际开航时间，无法确认到港，请先确认开航或补录开航时间"
            arrival_raw = values.get("实际到港")
            if not str(arrival_raw or "").strip():
                return None, "缺少实际到港时间，无法确认到港，请补录后再执行"
            merged["实际到港"] = arrival_raw
            reason = self._validate_times(merged)
            if reason:
                return None, reason
            business_time = _store_time(arrival_raw)
            entry["实际到港"] = business_time
            if departure_raw and str(departure_raw).strip():
                entry["实际开航"] = _store_time(departure_raw)
        else:  # 结航航次
            # 已到港才能结航；结航时间缺失时取当前时间，不阻断收尾
            closing_raw = values.get("结航时间") or _now_text()
            merged["结航时间"] = closing_raw
            reason = self._validate_times(merged)
            if reason:
                return None, reason
            business_time = _store_time(closing_raw)
            entry["结航时间"] = business_time

        reason = self._validate_times(merged)
        if reason:
            return None, reason

        self._advance(entry, target, operator=operator, action=action, occurred_at=business_time)

        # 在港状态同步：开航离港、到港/结航在港
        if target == "航行中":
            sync_note = self._sync_vessel(entry, in_port=False, operator=operator, reason="确认开航离港")
        else:
            sync_note = self._sync_vessel(entry, in_port=True, operator=operator, reason=action)
        message = f"航次已{action}，状态更新为「{target}」，操作人与时间已留痕"
        if sync_note:
            message += f"；{sync_note}"
        return entry, message

    # ---- 时间补录与状态重排 ---------------------------------------------

    def _stage_from_times(self, merged: dict[str, Any]) -> str:
        """依据实际开航/到港/结航时间推断航次应处档位（预计到港不参与）。"""
        stage = STATUS_ORDER[0]
        for status in ("航行中", "已到港", "已结航"):
            field = STAGE_TIME_FIELD[status]
            if _parse_time(merged.get(field)) is not None:
                stage = status
        return stage

    def backfill_times(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """线下确认后补录时间：写入时间并按实际时间把状态向前重排，绝不回退。"""
        operator = str(values.get("operator") or values.get("操作人") or "").strip()
        if not operator:
            return None, "缺少操作人，补录时间无法留痕"

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档"
        if entry.get("status") == STATUS_ORDER[-1] and any(
            str(values.get(field) or "").strip() for field in STAGE_TIME_FIELD.values()
        ):
            return None, "该航次已结航归档，业务时间不再允许补录修改"

        updates: dict[str, Any] = {}
        for field in BACKFILL_FIELDS:
            raw = values.get(field)
            if raw is not None and str(raw).strip():
                if _parse_time(raw) is None:
                    return None, f"「{field}」时间「{raw}」格式无法识别，请按 YYYY-MM-DD HH:MM 补录"
                updates[field] = _store_time(raw)

        if not updates:
            return None, "没有可补录的时间字段（预计到港、实际开航、实际到港、结航时间）"

        merged = {
            "预计到港": entry.get("预计到港"),
            "实际开航": entry.get("实际开航"),
            "实际到港": entry.get("实际到港"),
            "结航时间": entry.get("结航时间"),
            **updates,
        }
        reason = self._validate_times(merged)
        if reason:
            return None, reason

        target = self._stage_from_times(merged)
        current = str(entry.get("status") or STATUS_ORDER[0])
        current_index = STATUS_ORDER.index(current)
        target_index = STATUS_ORDER.index(target)

        # 业务时间链条必须完整：给到港就得有开航，给结航就得有开航和到港，
        # 否则属于“时间缺失”，拒绝并说明原因，避免生成没有业务时间的空留痕。
        stage_requirements = {
            "已结航": [("实际开航", "实际开航时间"), ("实际到港", "实际到港时间")],
            "已到港": [("实际开航", "实际开航时间")],
        }
        for field, label in stage_requirements.get(target, []):
            if _parse_time(merged.get(field)) is None:
                return None, f"缺少{label}，无法按补录时间重排到「{target}」，请补全后再提交"

        # 刷新/补录都不能让状态退回上一档：目标档位落后于现状时整体拒绝，不写入任何时间
        if target_index < current_index:
            return None, (
                f"补录时间对应状态为「{target}」，早于当前状态「{current}」，状态不可回退，本次补录已拒绝"
            )

        entry.update(updates)

        advanced: list[str] = []
        if target_index > current_index:
            for index in range(current_index + 1, target_index + 1):
                status = STATUS_ORDER[index]
                field = STAGE_TIME_FIELD[status]
                self._advance(
                    entry,
                    status,
                    operator=operator,
                    action=STAGE_ACTION[status],
                    occurred_at=str(merged.get(field) or ""),
                    source="时间补录",
                )
                advanced.append(status)
            # 补录若直接推过到港/结航，以最终在港状态为准同步一次船舶
            in_port = target in ("已到港", "已结航")
            sync_note = self._sync_vessel(
                entry,
                in_port=in_port,
                operator=operator,
                reason="按补录时间重排状态",
            )
        else:
            sync_note = None

        written = "、".join(updates.keys())
        if advanced:
            message = f"已补录{written}，航次状态重排为「{target}」，每档变更均已记录操作人与时间"
        else:
            message = f"已补录{written}，当前状态「{current}」无需调整"
        if sync_note:
            message += f"；{sync_note}"
        return entry, message

    def reconcile(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        """按预计/实际时间对全量航次重排状态（批量补录后的兜底），只进不退。

        时间缺失、格式无法识别或时间倒挂的航次跳过并在结果里说明，不影响其他航次。
        """
        values = dict(values or {})
        operator = str(values.get("operator") or values.get("操作人") or "").strip()
        if not operator:
            return {"ok": False, "message": "缺少操作人，批量重排无法留痕", "items": []}

        results: list[dict[str, Any]] = []
        advanced_count = 0
        for entry in store.rows(MODULE):
            current = str(entry.get("status") or STATUS_ORDER[0])
            merged = {
                "预计到港": entry.get("预计到港"),
                "实际开航": entry.get("实际开航"),
                "实际到港": entry.get("实际到港"),
                "结航时间": entry.get("结航时间"),
            }
            missing = [
                label
                for field, label in (
                    ("实际开航", "实际开航时间"),
                    ("实际到港", "实际到港时间"),
                )
                if not str(merged.get(field) or "").strip()
            ]
            if missing:
                # 时间缺失不猜测状态：停在待开航/现状并说明原因
                results.append({
                    "id": entry.get("id"),
                    "航次编号": entry.get("航次编号"),
                    "status": current,
                    "changed": False,
                    "reason": f"缺少{'、'.join(missing)}，未重排",
                })
                continue
            if any(_parse_time(merged.get(field)) is None for field in ("实际开航", "实际到港")):
                results.append({
                    "id": entry.get("id"),
                    "航次编号": entry.get("航次编号"),
                    "status": current,
                    "changed": False,
                    "reason": "开航或到港时间格式无法识别，未重排",
                })
                continue
            reason = self._validate_times(merged)
            if reason:
                results.append({
                    "id": entry.get("id"),
                    "航次编号": entry.get("航次编号"),
                    "status": current,
                    "changed": False,
                    "reason": reason,
                })
                continue

            target = self._stage_from_times(merged)
            current_index = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
            target_index = STATUS_ORDER.index(target)
            if target_index < current_index:
                results.append({
                    "id": entry.get("id"),
                    "航次编号": entry.get("航次编号"),
                    "status": current,
                    "changed": False,
                    "reason": f"实际时间对应「{target}」早于当前「{current}」，状态保持不变",
                })
                continue
            if target_index == current_index:
                results.append({
                    "id": entry.get("id"),
                    "航次编号": entry.get("航次编号"),
                    "status": current,
                    "changed": False,
                    "reason": "状态与实际时间一致",
                })
                continue

            for index in range(current_index + 1, target_index + 1):
                status = STATUS_ORDER[index]
                self._advance(
                    entry,
                    status,
                    operator=operator,
                    action=STAGE_ACTION[status],
                    occurred_at=str(merged.get(STAGE_TIME_FIELD[status]) or ""),
                    source="批量重排",
                )
            sync_note = self._sync_vessel(
                entry,
                in_port=target in ("已到港", "已结航"),
                operator=operator,
                reason="按实际时间批量重排",
            )
            advanced_count += 1
            results.append({
                "id": entry.get("id"),
                "航次编号": entry.get("航次编号"),
                "status": target,
                "changed": True,
                "reason": sync_note or f"重排为「{target}」",
            })

        return {
            "ok": True,
            "message": f"重排完成：{advanced_count} 条航次状态向前推进，其余维持现状",
            "items": results,
        }
