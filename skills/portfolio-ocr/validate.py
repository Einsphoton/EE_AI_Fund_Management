#!/usr/bin/env python3
"""离线校验 portfolio-ocr Skill 产物 JSON 是否符合 App 导入要求。

用法：
    python validate.py path/to/portfolio.json
    python validate.py file1.json file2.json ...   # 批量校验
    cat portfolio.json | python validate.py        # 从 stdin 读

退出码：
    0  全部通过
    1  至少有一个文件不合法
    2  参数 / IO 错误

无第三方依赖（不用装 jsonschema），只用标准库；方便在任何机器上跑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Windows GBK 控制台无法输出 ✓/✗，统一用纯 ASCII；尽量在 Windows 上把 stdout 换成 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

OK = "[OK]"
FAIL = "[FAIL]"
ITEM_ERR = "  -"

VALID_ASSET_TYPES = {"fund", "stock", "etf", "money_fund", "wealth", "cash", "bond"}
DATE_PATTERN = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def _err(path: str, msg: str) -> str:
    return f"{ITEM_ERR} {path}: {msg}"


def _validate_item(idx: int, item: Any) -> list[str]:
    errs: list[str] = []
    p = f"items[{idx}]"

    if not isinstance(item, dict):
        return [_err(p, f"必须是对象，实际是 {type(item).__name__}")]

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        errs.append(_err(f"{p}.name", "必填，且必须是非空字符串"))

    at = item.get("asset_type")
    if at not in VALID_ASSET_TYPES:
        errs.append(_err(
            f"{p}.asset_type",
            f"必须是 {sorted(VALID_ASSET_TYPES)} 之一，实际 {at!r}",
        ))

    # 货基/理财/现金/债券：必须能给出金额
    if at in ("money_fund", "wealth", "cash", "bond"):
        amount = item.get("amount")
        mv = item.get("market_value")
        if not _is_pos_num(amount) and not _is_pos_num(mv):
            errs.append(_err(
                f"{p}.amount",
                f"{at} 类资产必须填 amount 或 market_value（>0），不然无法入库",
            ))

    # 行情类：必须有 code（或者至少要有 shares + avg_cost 才能建初始交易）
    if at in ("fund", "stock", "etf"):
        code = item.get("code")
        if code is not None and not isinstance(code, str):
            errs.append(_err(f"{p}.code", "必须是字符串或 null"))

    # 数值字段类型检查
    for f in ("shares", "amount", "avg_cost", "current_price", "market_value",
              "profit", "profit_pct", "yield_7d", "expected_apr"):
        v = item.get(f)
        if v is not None and not isinstance(v, (int, float)):
            errs.append(_err(f"{p}.{f}", f"必须是数字或 null，实际 {type(v).__name__}"))

    md = item.get("maturity_date")
    if md is not None:
        if not isinstance(md, str) or not DATE_PATTERN.match(md):
            errs.append(_err(f"{p}.maturity_date", "必须是 YYYY-MM-DD 或 null"))

    return errs


def _is_pos_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and v > 0


def validate(data: Any) -> tuple[bool, list[str], int]:
    """返回 (ok, errs, items_count)。"""
    errs: list[str] = []

    if not isinstance(data, dict):
        return False, [_err("$", f"根必须是 JSON 对象，实际 {type(data).__name__}")], 0

    schema = data.get("schema")
    if schema is not None and schema != "ee-fund-mgr/portfolio-ocr@1":
        errs.append(_err("$.schema", f"未知 schema 版本：{schema!r}（期望 ee-fund-mgr/portfolio-ocr@1，或省略）"))

    sd = data.get("screenshot_date")
    if sd is not None:
        if not isinstance(sd, str) or not DATE_PATTERN.match(sd):
            errs.append(_err("$.screenshot_date", "必须是 YYYY-MM-DD 或 null"))

    items = data.get("items")
    if not isinstance(items, list):
        return False, errs + [_err("$.items", f"必须是数组，实际 {type(items).__name__}")], 0

    for i, it in enumerate(items):
        errs.extend(_validate_item(i, it))

    return len(errs) == 0, errs, len(items)


def _validate_file(path: str | None) -> bool:
    label = path or "<stdin>"
    try:
        if path is None:
            text = sys.stdin.read()
        else:
            text = Path(path).read_text(encoding="utf-8")
    except Exception as e:
        print(f"{FAIL} {label}: read failed - {type(e).__name__}: {e}")
        return False

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"{FAIL} {label}: JSON parse failed - {e}")
        return False

    ok, errs, n = validate(data)
    if ok:
        platform = data.get("platform") or "未知"
        print(f"{OK} {label}: valid, platform={platform}, items={n}")
        return True

    print(f"{FAIL} {label}: {len(errs)} issue(s)")
    for e in errs:
        print(e)
    return False


def main() -> int:
    args = sys.argv[1:]
    if not args:
        # 从 stdin 读
        return 0 if _validate_file(None) else 1

    all_ok = True
    for p in args:
        ok = _validate_file(p)
        if not ok:
            all_ok = False
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
