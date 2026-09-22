@echo off
chcp 936 >nul
setlocal
title 皖美AI 视频生成调试工具
cd /d "%~dp0"

rem ---- 环境自检（缺文件提示而不是闪退）----
if not exist "%~dp0runtime\python.exe" (
    echo.
    echo  [错误] 找不到 runtime\python.exe，请确认压缩包解压完整。
    echo         重新解压 seedance-video-tool.zip 后再试。
    echo.
    pause
    exit /b 1
)

rem ---- 端口检测：被占用则顺延 ----
set PORT=8501
:check_port
netstat -ano 2>nul | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    set /a PORT+=1
    goto check_port
)

echo.
echo  ====================================================
echo    皖美AI 视频生成调试工具
echo  ----------------------------------------------------
echo    浏览器地址 : http://127.0.0.1:%PORT%
echo    停止方法   : 直接关闭本窗口，服务会随之停止
echo    就绪标志   : 下方出现 "You can now view" 即可使用
echo  ====================================================
echo.

rem 延时 3 秒自动打开浏览器（等服务起来）
start "" cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:%PORT%"

rem 前台运行 Streamlit：关窗口 = 停止服务（不留后台进程）
"%~dp0runtime\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port %PORT% --server.headless true

echo.
echo 工具已停止（退出码 %errorlevel%），可关闭本窗口。
echo 若意外退出，请把上面最后几行报错发给管理员。
pause
