"""航次管理接口：维护航次，覆盖确认开航、确认到港、结航航次与时间补录重排等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.voyage import STATUS_ORDER, VoyageService

router = APIRouter(prefix="/api/voyage", tags=["航次管理"])

service = VoyageService()

LIST_FIELDS = ["航次编号", "关联船舶", "进口航次号", "出口航次号", "预计到港", "实际开航", "实际到港", "结航时间", "航线名称", "航次状态"]
STATUSES = STATUS_ORDER


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按航次编号检索"),
    status: str | None = Query(default=None, description="待开航、航行中、已到港、已结航"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按航次编号与状态过滤航次管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/statuses")
def list_statuses() -> dict[str, Any]:
    """提供状态序列与各档位可执行动作，供前端按当前状态收窄操作入口。"""
    return {
        "statuses": STATUSES,
        "actions": {
            "待开航": ["确认开航"],
            "航行中": ["确认到港"],
            "已到港": ["结航航次"],
            "已结航": [],
        },
    }


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出航次管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "voyage", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条航次明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"航次 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条航次，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="航次已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条航次执行确认开航、确认到港、结航航次；不允许的动作会被拦下并说明原因。

    重复结航只首次生效，会返回 ok=True 并说明已归档；其余校验不过返回 ok=False。
    """
    values = dict(payload.values)
    action = str(values.pop("action") or "").strip()
    entry, message, changed = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/backfill", response_model=ActionResult)
def backfill_times(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补录开航/到港/结航时间并按时间链路重排状态；时间缺失或逆序时说明原因并拒绝。"""
    entry, message = service.backfill_times(entry_id, dict(payload.values))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
