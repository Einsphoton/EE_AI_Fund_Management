"""Backend package marker.

模块加载最早期就把 stdout / stderr 重配成 UTF-8，根治 Windows PowerShell
（默认 cp936 / GBK）控制台中文打印乱码问题。

为什么放这里：
- Python 3 的 print() 写到 stdout 时按 stdout.encoding 编码；
  Windows 控制台默认 codepage 是 cp936/GBK，无法表示大多数中文符号 + emoji，
  导致 print("视觉模型调用失败：...") 在 PS 终端显示成 "?????"
  或者干脆抛 UnicodeEncodeError。
- `python -X utf8` 也行，但要求用户记得加；放代码里一劳永逸。
- 仅在 Windows 上做 reconfigure，避免改坏 Linux/Mac 的 systemd journal 行为。
- reconfigure 在 Python 3.7+ 可用；3.6 以下做静默 fallback。

注意：reconfigure 不会更改控制台 codepage（chcp 65001 由启动脚本负责）；
它只是告诉 Python 解释器输出时按 UTF-8 字节流写。所以最理想的组合是：
  - 启动脚本 chcp 65001
  - Python 端 stdout.reconfigure(encoding="utf-8", errors="replace")
两层叠加才能让 print 中文 + emoji 完美显示。
"""
from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    if sys.platform != "win32":
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            # Python 3.7+: 把流的编码层换成 utf-8，无法编码的字符替换成 ?
            # errors="replace" 比 "strict" 更稳健 —— 即使输入混进了
            # surrogate / 控制字符也不会让一条 print 直接抛 UnicodeEncodeError 把日志中断。
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            # 老 Python / 已被 wrap 过的 stream：忽略
            pass


_force_utf8_stdio()
