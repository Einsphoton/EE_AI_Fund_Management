@echo off
REM EE AI Fund Management - Windows 一键启动 (双击即可)
REM 该批处理会以 Bypass 执行策略调用 start-dev.ps1，绕开默认的脚本执行限制。

REM ---- 切到 UTF-8 (65001) 防止中文 / emoji 在控制台里变 ?? 乱码 ----
REM Windows 默认 cp936/GBK 控制台无法表示大多数 emoji 和部分中文符号；
REM 后端日志里的「📡」「🛑」「⏳」会全部变 ???，看似"程序乱码"。
chcp 65001 > nul

setlocal
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
    echo [X] 未找到 PowerShell，请使用 Windows 10/11 自带的 PowerShell。
    pause
    exit /b 1
)

echo === EE Fund Management: 启动中 ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1"
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo.
    echo 启动脚本返回错误 (code=%EXITCODE%)，请查看上方日志。
    pause
)

endlocal
exit /b %EXITCODE%
