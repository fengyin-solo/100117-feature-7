"""航次管理接口：维护航次，覆盖确认开航、确认到港、结航航次、时间补录与状态重排。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.voyage import VoyageService

router = APIRouter(prefix="/api/voyage", tags=["航次管理"])

service = VoyageService()

LIST_FIELDS = ["航次编号", "关联船舶", "进口航次号", "出口航次号", "预计到港", "实际开航", "实际到港", "结航时间", "航线名称", "航次状态"]
STATUSES = ["待开航", "航行中", "已到港", "已结航"]


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


async def _extract_values(request: Request, payload: EntryPayload) -> dict[str, Any]:
    """兼容 {"values": {...}} 与平铺 {...} 两种请求体（前端历史页面用平铺）。"""
    values = dict(payload.values)
    if not values:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            values = {k: v for k, v in body.items() if k not in ("remark",)}
    return values


@router.post("/{entry_id}/actions", response_model=ActionResult)
async def run_action(entry_id: int, request: Request, payload: EntryPayload) -> ActionResult:
    """对单条航次执行确认开航、确认到港、结航航次。

    操作人必传；实际到港早于开航、时间缺失、重复结航、状态回退都会被拦下并说明原因。
    请求体兼容 {"values": {...}} 与平铺 {...} 两种信封。
    """
    values = await _extract_values(request, payload)
    action = str(values.pop("action", "") or "").strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/backfill", response_model=ActionResult)
async def backfill_times(entry_id: int, request: Request, payload: EntryPayload) -> ActionResult:
    """线下确认后补录业务时间，并按预计到港/实际到港把航次状态向前重排。"""
    values = await _extract_values(request, payload)
    entry, message = service.backfill_times(entry_id, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/reconcile", response_model=ActionResult)
async def reconcile_entries(request: Request, payload: EntryPayload) -> ActionResult:
    """按实际开航/到港时间对全量航次重排状态；时间缺失或倒挂的航次跳过并说明。"""
    values = await _extract_values(request, payload)
    result = service.reconcile(values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message", "")),
        entry={"items": result.get("items", [])} if result.get("ok") else None,
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出航次管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "voyage", "total": total, "items": items}
