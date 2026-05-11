"""OCR 导入 API：批量上传截图 → 视觉模型解析 → 候选匹配 → 用户确认 → 入库。

异步任务式接口（v2）：
  1) POST /api/import/ocr/start         上传图片 → 立即返回 job_id，后台跑视觉模型
  2) GET  /api/import/ocr/jobs/{id}/stream  SSE 推送思考过程 + 进度（支持重连/replay）
  3) GET  /api/import/ocr/jobs/{id}     拉取最终结果（用户回到页面时一次性取齐）
  4) GET  /api/import/ocr/jobs          最近任务列表
  5) POST /api/import/ocr/commit        提交用户确认后的清单（事务性入库）

兼容性：保留 /api/import/ocr/parse 同步路由，便于老调用方平滑过渡。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db, SessionLocal
from ..tz import now_local
from ..agent import vision as vision_agent
from ..services import snapshot_service
from ..services import ocr_jobs

router = APIRouter(prefix="/api/import", tags=["import"])


# ============================================================
# /parse: 解析阶段（不入库）
# ============================================================

def _match_candidates(db: Session, item: dict, platform_hint: str) -> list[dict]:
    """对一条 OCR 结果，找现有资产候选（用于前端下拉）。

    匹配规则（极度保守，宁可错杀 = 新建，不可错容 = 误绑污染历史）：

    1. **code 完全匹配（最强信号，唯一会进入 candidates 的情况之一）**
       - 同 code + 同 platform → 1.0
       - 同 code 不同 platform → 0.95（同一只基金在不同 App 重复登记的常见场景）
       - 注意：占位 code（`fund_xxxxxxxx` 形态）不作为 code 匹配信号；否则同名
         用户用同一 hash 生成的占位会互相撞上。

    2. **name 精确/近似匹配（仅当双方 code 都为空或都是占位时）**
       - 采用 enrichment._name_match_score（内置后缀归一：A/C 类等），阈值 0.95
       - 双方 code 都给出但不同 → 一定不是同一只（基金代码具唯一性）→ 直接拒绝
       - score < 0.95 → 直接拒绝进入 candidates（哪怕是 0.9+ 也不留），
         避免前端 UI 里把『南方红利低波 50ETF 联接 A』列成『南方 XX 基金』的候选，
         用户一时没看清就点了『追加买入』。

    3. **所有进入 candidates 的项都保证 match_score ≥ 0.95**。
       调用方（_suggest_action）可以放心把 top_candidate 当成"可信同一只"处理。

    输出还会打印一份诊断日志（`[ocr-match]` 前缀），便于事后排查
    "为啥某条 OCR 被判成追加买入"。
    """
    name = (item.get("name") or "").strip()
    code = (item.get("code") or "").strip()
    asset_type = (item.get("asset_type") or "").strip().lower()
    candidates: list[tuple[float, models.Asset]] = []

    from ..services.enrichment import _is_placeholder_code, _name_match_score

    ocr_code_real = bool(code) and not _is_placeholder_code(code)

    # ---- 第 1 关：真实 code 强匹配 ----
    if ocr_code_real:
        for a in db.query(models.Asset).filter(models.Asset.code == code).all():
            same_platform = (a.platform or "") == (platform_hint or "")
            score = 1.0 if same_platform else 0.95
            candidates.append((score, a))

    # ---- 第 2 关：名称精确匹配（阈值 0.95） ----
    if name:
        NAME_THRESHOLD = 0.95
        # 同类型（fund/etf/stock 等）优先；跨类型直接跳过，避免"理财"撞到"基金"
        query = db.query(models.Asset)
        if asset_type:
            try:
                at_enum = models.AssetType(asset_type)
                query = query.filter(models.Asset.asset_type == at_enum)
            except ValueError:
                pass
        for a in query.all():
            # 去重：已经被 code 匹配到的不再评估
            if any(c[1].id == a.id for c in candidates):
                continue
            existing_code = (a.code or "").strip()
            existing_code_real = bool(existing_code) and not _is_placeholder_code(existing_code)
            # 双方真 code 都给出且不同 → 一定不是同一只（唯一性）
            if ocr_code_real and existing_code_real and existing_code != code:
                continue
            s = _name_match_score(name, a.name)
            if s >= NAME_THRESHOLD:
                candidates.append((s, a))

    # 按分数倒排 + 过滤一次（保险：防止上面 code 分支给了 < 0.95 的分）
    candidates = [(s, a) for s, a in candidates if s >= 0.95]
    candidates.sort(key=lambda x: x[0], reverse=True)

    # 诊断日志：便于你抓"为啥某条被错判"
    if name or code:
        if candidates:
            top = candidates[0]
            print(
                f"[ocr-match] 「{name}」 code={code or '<无>'} type={asset_type} "
                f"→ 候选 {len(candidates)} 项，top=「{top[1].name}」(code={top[1].code}) "
                f"score={top[0]:.2f}"
            )
        else:
            print(
                f"[ocr-match] 「{name}」 code={code or '<无>'} type={asset_type} "
                f"→ 无候选（将建议新建）"
            )

    return [
        {
            "asset_id": a.id,
            "name": a.name,
            "code": a.code,
            "asset_type": a.asset_type.value,
            "platform": a.platform,
            "match_score": round(score, 2),
        }
        for score, a in candidates[:5]
    ]


def _suggest_action(item: dict, top_candidate: Optional[dict], db: Session) -> dict:
    """根据 OCR 结果与候选资产，给一个建议动作。

    前提：top_candidate 只会在 score ≥ 0.95 时传进来（由 _match_candidates 保证）。
    所以这里不需要再做"弱匹配降级"，直接按 top_candidate 存在与否二分：
      - 无 candidate → create（新建）
      - 有 candidate → 按份额/金额差推断 append_buy / append_sell / update_field / skip
    """
    if not top_candidate:
        return {"action": "create", "reason": "未匹配到现有资产，建议新建"}

    asset_id = top_candidate["asset_id"]
    asset = db.get(models.Asset, asset_id)
    if not asset:
        return {"action": "create", "reason": "候选已不存在，建议新建"}

    asset_type = (item.get("asset_type") or "").lower()

    # 货基/理财/现金：用 amount 比对
    if asset_type in ("money_fund", "wealth", "cash", "bond"):
        ocr_amount = float(item.get("amount") or item.get("market_value") or 0.0)
        cur_amount = float(asset.principal_amount or 0.0)
        diff = ocr_amount - cur_amount
        if abs(diff) < 1.0:  # 1 元以内当无变化
            return {"action": "skip", "reason": f"金额无变化（{cur_amount:.2f}）"}
        return {
            "action": "update_field",
            "delta_amount": round(diff, 2),
            "reason": f"本金从 {cur_amount:.2f} 变为 {ocr_amount:.2f}（差 {diff:+.2f}）",
        }

    # 基金/股票/ETF：用份额比对最近 snapshot 或当前持仓
    last_snap = snapshot_service.latest_snapshot(db, asset_id)
    if last_snap and last_snap.shares is not None:
        baseline = last_snap.shares
    else:
        # 没有快照，用 transactions 算当前份额
        from ..services import holdings as holding_service
        baseline = holding_service.summarize(asset, current_price=None).get("total_shares") or 0.0

    ocr_shares = float(item.get("shares") or 0.0)
    diff = ocr_shares - baseline

    # 0.001 份以内视为无变化
    if abs(diff) < 0.001:
        return {"action": "skip", "reason": f"份额无变化（{baseline:.4f}）"}
    if diff > 0:
        return {
            "action": "append_buy",
            "delta_shares": round(diff, 4),
            "reason": f"份额从 {baseline:.4f} → {ocr_shares:.4f}（追加 {diff:+.4f}）",
        }
    return {
        "action": "append_sell",
        "delta_shares": round(-diff, 4),
        "reason": f"份额从 {baseline:.4f} → {ocr_shares:.4f}（减仓 {-diff:+.4f}）",
    }


@router.post("/ocr/parse")
async def parse_screenshots(
    files: list[UploadFile] = File(..., description="持仓页截图（支持多张）"),
    platform_hint: str = Form("", description="平台提示，例如 微信理财通 / 招商银行 / 富途"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """上传 N 张截图，逐张走视觉模型解析，返回每张图的 items + 匹配候选 + 建议动作。

    不入库；前端拿这份结果做对账，再调 /commit 真正写入。
    """
    if not files:
        raise HTTPException(400, "至少上传一张截图")

    images: list[tuple[bytes, str, str]] = []
    file_names: list[str] = []
    for f in files:
        b = await f.read()
        if not b:
            continue
        mime = f.content_type or "image/jpeg"
        images.append((b, mime, platform_hint))
        file_names.append(f.filename or "unknown.jpg")

    raw_results = await vision_agent.parse_images_concurrently(db, images)

    # 给每条 item 附上候选与建议
    out: list[dict] = []
    for i, r in enumerate(raw_results):
        items = r.get("items") or []
        for it in items:
            cands = _match_candidates(db, it, r.get("platform") or platform_hint)
            top = cands[0] if cands else None
            suggestion = _suggest_action(it, top, db)
            it["_candidates"] = cands
            it["_suggestion"] = suggestion
        out.append({
            "file": file_names[i] if i < len(file_names) else "",
            "platform": r.get("platform"),
            "screenshot_date": r.get("screenshot_date"),
            "items": items,
            "error": r.get("error"),
        })
    return {"results": out, "total": sum(len(r["items"]) for r in out)}


# ============================================================
# /ocr/start + /jobs/{id}/stream + /jobs/{id} : 异步任务模式
# ============================================================

@router.post("/ocr/start")
async def start_ocr_job(
    files: list[UploadFile] = File(..., description="持仓页截图（支持多张）"),
    platform_hint: str = Form("", description="平台提示"),
) -> dict[str, Any]:
    """上传 N 张截图 → 立即返回 job_id，后台异步跑视觉模型。

    前端拿到 job_id 后用 /jobs/{id}/stream 订阅进度；切换路由再回来用 /jobs/{id}
    拉取最终结果。
    """
    if not files:
        raise HTTPException(400, "至少上传一张截图")

    images: list[tuple[bytes, str, str]] = []
    file_names: list[str] = []
    for f in files:
        b = await f.read()
        if not b:
            continue
        mime = f.content_type or "image/jpeg"
        images.append((b, mime, platform_hint))
        file_names.append(f.filename or "unknown.jpg")

    if not images:
        raise HTTPException(400, "上传文件为空")

    job = ocr_jobs.manager.create(
        total=len(images),
        platform_hint=platform_hint,
        file_names=file_names,
    )

    # 后台跑：注入 match/suggest 函数 + db_factory（每张图独立 session）
    asyncio.create_task(ocr_jobs.run_parse_job(
        job, images,
        db_factory=SessionLocal,
        match_fn=_match_candidates,
        suggest_fn=_suggest_action,
    ))

    return {"job_id": job.job_id, "snapshot": job.snapshot()}


@router.get("/ocr/jobs/{job_id}/stream")
async def stream_ocr_job(job_id: str):
    """SSE 推送某个 OCR 任务的思考过程 + 进度。

    重连友好：连上时先 replay 全部历史事件，让前端 UI 跳到当前状态。
    """
    job = ocr_jobs.manager.get(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} 不存在或已过期")

    queue = await ocr_jobs.manager.subscribe(job)

    async def gen():
        try:
            # 心跳：客户端切到后台后浏览器可能丢连接，每 15s 发一次注释帧保活
            last_beat = asyncio.get_event_loop().time()
            while True:
                # 如果 job 已结束且队列空 → 推 [DONE] 然后退出
                if job.status in ("done", "error", "cancelled") and queue.empty():
                    yield "data: [DONE]\n\n"
                    return

                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳：SSE 注释行（以 `:` 开头）保持连接
                    now = asyncio.get_event_loop().time()
                    if now - last_beat > 14:
                        yield ": ping\n\n"
                        last_beat = now
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    # 不让 SSE generator 异常把 chunked 响应直接打断成 500。
                    yield f"data: {json.dumps({'type': 'fatal', 'error': str(e)}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
        finally:
            ocr_jobs.manager.unsubscribe(job, queue)


    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx 关 buffer
            "Connection": "keep-alive",
        },
    )


@router.get("/ocr/jobs/{job_id}")
def get_ocr_job(job_id: str) -> dict[str, Any]:
    """拉取某个 OCR 任务的快照 + 最终结果（如果已完成）。

    用于：用户切走再回来，先调这个一次性挂回 UI。
    """
    job = ocr_jobs.manager.get(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} 不存在或已过期")
    return {
        "snapshot": job.snapshot(),
        "events": job.events,
        "result": job.result,
    }


@router.get("/ocr/jobs")
def list_ocr_jobs(limit: int = 10) -> dict[str, Any]:
    """最近 OCR 任务列表（用于前端启动时探测是否有进行中的任务可挂回）。"""
    return {"items": ocr_jobs.manager.list_recent(limit=limit)}


@router.post("/ocr/jobs/{job_id}/cancel")
async def cancel_ocr_job(job_id: str) -> dict[str, Any]:
    """请求取消某个 OCR 任务。

    - 立即设置 cancel_event：未开始的图片直接跳过、正在跑的图主动 close 流式连接
    - 已识别的部分仍会保留在 result 里，前端能看到/确认这些
    - 任务最终状态变为 `cancelled`，并发出 `cancelled` 事件

    注意：必须是 async def，让 FastAPI 在主事件循环里执行，cancel_event 与
    OCR 任务在同一个事件循环上 set，行为最确定。
    """
    job = ocr_jobs.manager.get(job_id)
    if not job:
        print(f"[ocr-cancel] job {job_id} 不存在或已过期")
        raise HTTPException(404, f"job {job_id} 不存在或已过期")
    if job.status in ("done", "error", "cancelled"):
        print(f"[ocr-cancel] job {job_id} 已是终态 status={job.status}，无需取消")
        return {"ok": True, "already_finished": True, "status": job.status}
    print(f"[ocr-cancel] 收到取消请求 job={job_id} status={job.status} "
          f"finished={job.finished}/{job.total}")
    job.cancel_event.set()
    print(f"[ocr-cancel] cancel_event.set() done is_set={job.cancel_event.is_set()}")
    return {"ok": True, "status": "cancelling"}


# ============================================================
# /ocr/import-json: 直接吃 Skill 产物的 JSON 文件
# ============================================================
#
# 设计动机：用户可以用 skills/portfolio-ocr/ Skill 在任何多模态 ChatBot
# 上手工跑 OCR，把产物保存为 JSON 文件再扔到这里。本接口把 JSON 转成与
# /ocr/parse 完全一致的 results 结构，让前端能复用同一份对账表。
#
# 接受三种输入：
#   1) 一个或多个文件 file=...（multipart/form-data）；每个文件可以是
#      Skill 单图产物（顶层是 OcrParseResult-like），或 wrap 过的多图产物
#      （顶层是 {"results":[...]}）；
#   2) 文件内容是 JSON Lines（每行一个 result），也支持；
#   3) 兼容裸 items 数组（顶层就是 [...]），当成一份没有 platform 的结果处理。

VALID_ASSET_TYPES = {"fund", "stock", "etf", "money_fund", "wealth", "cash", "bond"}


def _normalize_one_result(raw: Any, fallback_file: str, platform_hint: str) -> dict:
    """把一份"任意形态的 Skill 产物"规整成与 OCR parse 一致的 result dict。

    支持的输入形态：
      - {"platform":..., "items":[...]}              → 标准 Skill 单图产物
      - {"items":[...]}                              → 缺 platform 也行
      - [{"name":...}, ...]                          → 裸 items 数组
      - 其他 → 当成空（带 error 提示）
    """
    if isinstance(raw, list):
        items = raw
        platform = ""
        screenshot_date = None
    elif isinstance(raw, dict):
        items = raw.get("items") or []
        platform = raw.get("platform") or ""
        screenshot_date = raw.get("screenshot_date")
    else:
        return {
            "file": fallback_file,
            "platform": "解析失败",
            "screenshot_date": None,
            "items": [],
            "error": f"JSON 顶层不是 object/array（实际：{type(raw).__name__}），不是合法的 Skill 产物",
        }

    if not isinstance(items, list):
        return {
            "file": fallback_file,
            "platform": platform or "解析失败",
            "screenshot_date": screenshot_date,
            "items": [],
            "error": "items 字段不是数组",
        }

    # 逐项做最低限度的字段规整 —— 只补必要默认值，**不**改用户给的数字
    cleaned: list[dict] = []
    item_errs: list[str] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            item_errs.append(f"第 {i + 1} 项不是对象，已丢弃")
            continue
        name = (it.get("name") or "").strip()
        if not name:
            item_errs.append(f"第 {i + 1} 项缺 name，已丢弃")
            continue
        at = it.get("asset_type")
        if at not in VALID_ASSET_TYPES:
            item_errs.append(f"#{i + 1}「{name}」asset_type=「{at}」非法，已兜底为 fund")
            at = "fund"
        cleaned.append({
            "name": name,
            "code": it.get("code"),
            "asset_type": at,
            "shares": _safe_num(it.get("shares")),
            "amount": _safe_num(it.get("amount")),
            "avg_cost": _safe_num(it.get("avg_cost")),
            "current_price": _safe_num(it.get("current_price")),
            "market_value": _safe_num(it.get("market_value")),
            "profit": _safe_num(it.get("profit")),
            "profit_pct": _safe_num(it.get("profit_pct")),
            "yield_7d": _safe_num(it.get("yield_7d")),
            "expected_apr": _safe_num(it.get("expected_apr")),
            "maturity_date": it.get("maturity_date"),
        })

    return {
        "file": fallback_file,
        "platform": platform or platform_hint or "未知",
        "screenshot_date": screenshot_date,
        "items": cleaned,
        # 仅当真有 item 级问题时才挂 error，不至于把 5 项里有 1 项瑕疵的整张图标红
        "error": "; ".join(item_errs) if item_errs else None,
    }


def _safe_num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    # 字符串数字也尝试转一下（用户可能误粘成 "1234.5"）
    try:
        return float(str(v).replace(",", "").replace("¥", "").replace("元", "").strip())
    except (ValueError, TypeError):
        return None


def _try_parse_json_payload(text: str) -> tuple[Any, str | None]:
    """允许 JSON / JSON Lines / 简单的代码围栏。返回 (parsed, error_msg)。"""
    s = text.strip()
    if not s:
        return None, "文件为空"
    # 剥 markdown 代码围栏
    if s.startswith("```"):
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    # 优先一次性解析
    try:
        return json.loads(s), None
    except json.JSONDecodeError:
        pass
    # 退化 JSON Lines
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) > 1:
        try:
            arr = [json.loads(ln) for ln in lines]
            return arr, None
        except json.JSONDecodeError as e:
            return None, f"JSON Lines 解析失败：{e}"
    return None, "JSON 解析失败（既不是合法 JSON，也不是 JSON Lines）"


@router.post("/ocr/import-json")
async def import_skill_json(
    files: list[UploadFile] = File(..., description="Skill 产物 JSON 文件，可多选"),
    platform_hint: str = Form("", description="平台提示，仅当 JSON 里没填 platform 时兜底"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """吃 portfolio-ocr Skill 产物的 JSON 文件，返回 /ocr/parse 同结构。

    与 /ocr/parse 唯一的差别：跳过视觉模型环节。其余的候选匹配 + 建议动作完全一致。
    """
    if not files:
        raise HTTPException(400, "至少上传一份 JSON 文件")

    raw_results: list[dict] = []
    for f in files:
        b = await f.read()
        if not b:
            continue
        try:
            text = b.decode("utf-8-sig")  # 容忍 BOM
        except UnicodeDecodeError:
            try:
                text = b.decode("gbk")
            except UnicodeDecodeError:
                raw_results.append({
                    "file": f.filename or "unknown.json",
                    "platform": "解析失败",
                    "items": [],
                    "error": "文件不是 UTF-8/GBK 文本，无法解析",
                })
                continue

        parsed, err = _try_parse_json_payload(text)
        if err:
            raw_results.append({
                "file": f.filename or "unknown.json",
                "platform": "解析失败",
                "items": [],
                "error": err,
            })
            continue

        # parsed 形态可能是：{"results":[...]} / 单个 result-like / 裸 items 数组 / 或一份 result 列表
        fallback_name = f.filename or "skill.json"
        if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            # 多图 wrap：把每条 result 都过一遍归一化
            for i, r in enumerate(parsed["results"]):
                raw_results.append(_normalize_one_result(
                    r,
                    (r.get("file") if isinstance(r, dict) else None) or f"{fallback_name}#{i + 1}",
                    platform_hint,
                ))
        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "items" in parsed[0]:
            # 一份数组，每个元素都是 result-like
            for i, r in enumerate(parsed):
                raw_results.append(_normalize_one_result(
                    r,
                    (r.get("file") if isinstance(r, dict) else None) or f"{fallback_name}#{i + 1}",
                    platform_hint,
                ))
        else:
            # 单图产物 / 裸 items 数组
            raw_results.append(_normalize_one_result(parsed, fallback_name, platform_hint))

    if not raw_results:
        raise HTTPException(400, "上传的 JSON 都解析失败或为空")

    # 自动补码（默认开，可配置关）：与 OCR 路径一致的严格超时 (单条 3s/整批 5s)；
    # 用户不想自动查码可以在"设置 → 视觉模型"里把 auto_fill_code 关掉。
    from ..services import settings_service
    vcfg = settings_service.get(db, "vision") or {}
    if vcfg.get("auto_fill_code", True):
        from ..services.ocr_jobs import _auto_fill_fund_codes
        async def _silent_log(msg: str):  # JSON 导入路径没有 SSE，日志直接吞掉
            return
        for r in raw_results:
            await _auto_fill_fund_codes(r.get("items") or [], _silent_log)

    # 给每条 item 附上候选与建议（完全复用 OCR 的逻辑）
    out: list[dict] = []
    for r in raw_results:
        items = r.get("items") or []
        for it in items:
            cands = _match_candidates(db, it, r.get("platform") or platform_hint)
            top = cands[0] if cands else None
            suggestion = _suggest_action(it, top, db)
            it["_candidates"] = cands
            it["_suggestion"] = suggestion
        out.append({
            "file": r.get("file", ""),
            "platform": r.get("platform"),
            "screenshot_date": r.get("screenshot_date"),
            "items": items,
            "error": r.get("error"),
        })
    return {"results": out, "total": sum(len(r["items"]) for r in out)}


# ============================================================
# /commit: 提交阶段（事务性入库）
# ============================================================

def _to_datetime(v: Any) -> Optional[datetime]:
    """把前端 / OCR 模型给的"日期-ish"值转 datetime。

    历史教训：CommitItem.maturity_date 之前直接声明为 Optional[datetime]，
    Pydantic 严格解析时遇到 "2026年8月15日" / "2026-08" / "Q3 2026" 等模型自由
    发挥的格式会 422，整批提交全失败。改成 Any + 这里容错转换：
      - None / 空字符串 / 非字符串非 datetime → None（不算错）
      - "YYYY-MM-DD" / ISO datetime → datetime
      - 其他无法识别的形态 → None（静默丢弃，不阻塞入库；只在控制台日志）
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    # 优先 ISO 8601（Python 3.11+ fromisoformat 接受日期 / datetime / 带 Z）
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    # 退化：常见格式
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    print(f"[ocr-commit] 无法识别的日期格式 「{v}」，已忽略")
    return None


class CommitItem(BaseModel):
    """前端提交的单条决策（已经过用户编辑）。"""
    action: str                          # create / append_buy / append_sell / update_field / skip
    asset_id: Optional[int] = None       # 追加/减仓/更新时必填
    # 资产元信息（创建时必填；追加时可选，会更新现有 asset 的可选字段）
    name: Optional[str] = None
    code: Optional[str] = None
    asset_type: Optional[str] = None
    market: Optional[str] = "OTC"
    exchange: Optional[str] = None
    platform: Optional[str] = ""

    note: Optional[str] = ""
    # 新建/扩展字段
    yield_7d: Optional[float] = None
    expected_apr: Optional[float] = None
    # 日期字段：前端可能传 "YYYY-MM-DD" / ISO datetime / null。
    # 用 Any 而不是 datetime，是因为 OCR 路径下日期来自模型自由发挥
    # （甚至可能是 "2026-08" / "2026年8月15日"），用严格 datetime 会 422 整批失败。
    # 真正的转换在 commit 路由内用 _to_datetime() 容错处理；
    # 解析失败就丢弃这个字段，不影响其它字段入库。
    start_date: Optional[Any] = None
    maturity_date: Optional[Any] = None
    principal_amount: Optional[float] = None
    is_principal_guaranteed: Optional[bool] = True
    # 交易/快照数据
    shares: Optional[float] = None       # OCR 当前持有份额
    delta_shares: Optional[float] = None # 追加/减仓的份额差
    delta_amount: Optional[float] = None # 货基/理财本金差
    avg_cost: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    profit: Optional[float] = None
    profit_pct: Optional[float] = None
    snapshot_date: Optional[Any] = None
    raw: Optional[dict] = None


class CommitRequest(BaseModel):
    items: list[CommitItem]


@router.post("/ocr/commit")
def commit_decisions(
    payload: CommitRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """事务性写入用户确认后的导入决策。"""
    created = 0
    appended = 0
    skipped = 0
    errors: list[str] = []

    print(f"[ocr-commit] 收到 {len(payload.items)} 条决策")

    try:
        for idx, it in enumerate(payload.items):
            # 每轮重置 asset 局部变量，避免跨 iteration 污染（之前的 bug：
            # 'asset' in locals() 永远为 True，会让 snapshot 关联到上一项的 asset.id）
            asset = None
            try:
                if it.action == "skip":
                    skipped += 1
                    continue

                if it.action == "create":
                    # name 是必须的；code 对货基/理财/现金/债券允许缺省（用名字哈希生成占位）
                    if not it.name or not it.asset_type:
                        errors.append(f"#{idx} 创建失败：缺少 name/asset_type "
                                      f"（name=「{it.name}」type=「{it.asset_type}」）")
                        continue
                    try:
                        a_enum = models.AssetType(it.asset_type)
                    except ValueError:
                        errors.append(f"#{idx} 未知 asset_type: {it.asset_type}")
                        continue

                    # 处理 code：所有类型都允许缺省 code。
                    # 历史教训：腾讯理财通持仓页**不显示基金代码**（只在详情页里有），
                    # 但用户最常截的就是这个页面。如果硬性要求 fund/stock/etf 必须有 code，
                    # 用户唯一的解法就是手动逐项查代码——体验极差。
                    # 改为：缺 code 时用 hash(name+platform) 生成占位 code，并在 note 里
                    # 标记『需要补全代码』，前端可以显示警告引导用户事后补全。
                    code = (it.code or "").strip()
                    note = it.note or ""
                    code_was_auto = False
                    if not code:
                        import hashlib
                        h = hashlib.md5(
                            f"{a_enum.value}|{it.name}|{it.platform or ''}".encode("utf-8")
                        ).hexdigest()[:8]
                        code = f"{a_enum.value}_{h}"
                        code_was_auto = True
                        # 在 note 里做个机器可读的标记，方便后续 UI 提示用户补全
                        marker = "⚠️ OCR 未识别到代码，已生成占位；建议补全真实代码以启用行情自动同步"
                        if marker not in note:
                            note = (note + " | " + marker).strip(" |") if note else marker
                        print(f"[ocr-commit] #{idx} 自动生成 code={code}（{a_enum.value}/{it.name}）"
                              f" — OCR 未识别到代码")

                    try:
                        m_enum = models.Market(it.market or "OTC")
                    except ValueError:
                        m_enum = models.Market.otc
                    exchange = (it.exchange or "").strip().upper()
                    if exchange and exchange not in note:
                        note = (note + " | " if note else "") + f"交易所:{exchange}"
                    asset = models.Asset(

                        name=it.name, code=code, asset_type=a_enum, market=m_enum,
                        platform=it.platform or "", note=note,
                        yield_7d=it.yield_7d, expected_apr=it.expected_apr,
                        start_date=_to_datetime(it.start_date),
                        maturity_date=_to_datetime(it.maturity_date),
                        principal_amount=it.principal_amount,
                        is_principal_guaranteed=it.is_principal_guaranteed if it.is_principal_guaranteed is not None else True,
                    )
                    db.add(asset)
                    db.flush()
                    asset_id = asset.id
                    print(f"[ocr-commit] #{idx} CREATE asset_id={asset_id} name=「{it.name}」 "
                          f"code=「{code}」{'(auto)' if code_was_auto else ''} type={a_enum.value} "
                          f"platform=「{it.platform or ''}」")

                    # 行情类资产：如果有 shares + avg_cost，建一笔初始买入交易
                    if a_enum.value in ("fund", "stock", "etf") and it.shares and it.avg_cost:
                        db.add(models.Transaction(
                            asset_id=asset_id, txn_type=models.TxnType.buy,
                            shares=it.shares, price=it.avg_cost,
                            amount=(it.shares or 0) * (it.avg_cost or 0),
                            fee=0.0,
                            trade_date=_to_datetime(it.snapshot_date) or now_local(),
                            note="OCR 导入·初始买入",
                        ))
                        print(f"[ocr-commit] #{idx} +Transaction buy {it.shares}×{it.avg_cost}")
                    created += 1

                elif it.action in ("append_buy", "append_sell"):
                    if not it.asset_id:
                        errors.append(f"#{idx} 追加失败：缺少 asset_id")
                        continue
                    asset = db.get(models.Asset, it.asset_id)
                    if not asset:
                        errors.append(f"#{idx} 资产 #{it.asset_id} 不存在")
                        continue
                    delta = abs(it.delta_shares or 0)
                    if delta <= 0:
                        skipped += 1
                        print(f"[ocr-commit] #{idx} {it.action} delta=0 → skip")
                        continue
                    txn_type = models.TxnType.buy if it.action == "append_buy" else models.TxnType.sell
                    price = it.current_price or it.avg_cost or 0.0
                    db.add(models.Transaction(
                        asset_id=asset.id, txn_type=txn_type,
                        shares=delta, price=price,
                        amount=delta * price,
                        fee=0.0,
                        trade_date=_to_datetime(it.snapshot_date) or now_local(),
                        note=f"OCR 导入·{'追加' if txn_type == models.TxnType.buy else '减仓'}",
                    ))
                    if asset.watch_only:
                        asset.watch_only = False
                    appended += 1
                    print(f"[ocr-commit] #{idx} {it.action} asset_id={asset.id} "
                          f"+{delta}×{price}")

                elif it.action == "update_field":
                    # 仅货基/理财/现金/债券：直接更新 principal_amount + yield/apr 等
                    if not it.asset_id:
                        errors.append(f"#{idx} 更新失败：缺少 asset_id")
                        continue
                    asset = db.get(models.Asset, it.asset_id)
                    if not asset:
                        errors.append(f"#{idx} 资产 #{it.asset_id} 不存在")
                        continue
                    if it.principal_amount is not None:
                        asset.principal_amount = it.principal_amount
                    elif it.delta_amount is not None:
                        asset.principal_amount = float(asset.principal_amount or 0) + it.delta_amount
                    if it.yield_7d is not None:
                        asset.yield_7d = it.yield_7d
                    if it.expected_apr is not None:
                        asset.expected_apr = it.expected_apr
                    if it.maturity_date is not None:
                        md = _to_datetime(it.maturity_date)
                        if md is not None:
                            asset.maturity_date = md
                    appended += 1
                    print(f"[ocr-commit] #{idx} update_field asset_id={asset.id} "
                          f"principal={asset.principal_amount}")

                else:
                    errors.append(f"#{idx} 未知 action: {it.action}")
                    continue

                # 任何写入操作后都打一份 snapshot（追溯用）
                target_asset_id = it.asset_id or (asset.id if asset is not None else None)
                if target_asset_id:
                    snapshot_service.create_snapshot(
                        db, target_asset_id,
                        shares=it.shares or 0.0,
                        avg_cost=it.avg_cost,
                        market_value=it.market_value,
                        profit=it.profit,
                        profit_pct=it.profit_pct,
                        source="ocr",
                        snapshot_date=_to_datetime(it.snapshot_date),
                        raw=it.raw or {},
                        note=f"OCR 导入·{it.action}",
                    )
            except Exception as e:
                err_str = f"#{idx} 处理异常：{type(e).__name__}: {str(e)[:200]}"
                errors.append(err_str)
                print(f"[ocr-commit] {err_str}")
                import traceback
                traceback.print_exc()

        db.commit()
        print(f"[ocr-commit] 完成 created={created} appended={appended} "
              f"skipped={skipped} errors={len(errors)}")
    except Exception as e:
        db.rollback()
        print(f"[ocr-commit] 整体失败：{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"提交失败：{e}")

    return {"created": created, "appended": appended, "skipped": skipped, "errors": errors}
