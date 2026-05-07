"""时区工具：项目统一用北京时间（Asia/Shanghai, UTC+8）。

DB 里存 naive datetime（无 tzinfo），语义上认为就是北京时间——
这样 FastAPI 序列化出去的 ISO 字符串不带 Z/+08:00 后缀，
前端 JS 的 `new Date(s)` 会按本地时区解析，Windows/Mac 本地时区
通常就是 +08:00，两端一致不会再差 8 小时。

之所以不切到带时区的 aware datetime + +08:00 后缀：
- 会和历史 naive 数据混在一起，SQLAlchemy 查询时易报 TypeError；
- 前端 JS 面对带 +08:00 的 ISO 字符串渲染依然是对的，所以无必要。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """返回当前北京时间（naive datetime，无 tzinfo）。

    用于所有写入数据库的默认时间戳，保证 DB 里存的就是人眼看到的本地时间。
    """
    return datetime.now(_BEIJING).replace(tzinfo=None)
