@echo off
chcp 65001 >nul
REM v2: 一键重新分析所有回放并打开报告（输出到 out-v2，不与原版数据混用）
set PY=C:\Users\Mr.Mouse\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=C:\Users\Mr.Mouse\.workbuddy\binaries\python\versions\3.13.12\python.exe

"%PY%" "%~dp0report.py" "C:\Users\Mr.Mouse\BrawlhallaReplays"
if errorlevel 1 (
  echo 分析失败，按任意键退出
  pause >nul
  exit /b 1
)
start "" "D:\bh-replay-lab\out-v2\report.html"
