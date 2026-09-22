@echo off
setlocal
rem ============================================================
rem  皖美AI 视频生成调试工具 - 一键打包脚本（绿色免安装包）
rem  用法：改完 app.py / tunnel.py 后双击本脚本，产物在 dist\seedance-video-tool.zip
rem  换机器打包：修改下面 BASE_PYTHON 指向本机 uv 管理的 base Python 目录
rem ============================================================
cd /d "%~dp0"

rem ---- 可配置项 ----
set "BASE_PYTHON=D:\my_uv\python\cpython-3.14-windows-x86_64-none"
set "OUT=dist\seedance-video-tool"
set "ZIP=dist\seedance-video-tool.zip"

echo [1/8] 清理旧产物...
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%ZIP%" del /q "%ZIP%"
mkdir "%OUT%"

echo [2/8] 复制 Python runtime（base 自包含，约 63MB）...
robocopy "%BASE_PYTHON%" "%OUT%\runtime" /E /MT:16 /NFL /NDL /NJH /NP >nul

echo [3/8] 合并 site-packages（约 460MB，需等待）...
robocopy ".venv\Lib\site-packages" "%OUT%\runtime\Lib\site-packages" /E /MT:16 /NFL /NDL /NJH /NP >nul
del /q "%OUT%\runtime\Lib\site-packages\_virtualenv.pth" 2>nul

echo [4/8] 裁剪 Streamlit 图表组件依赖（工具用不到，省约 150MB）...
for /d %%D in ("%OUT%\runtime\Lib\site-packages\pyarrow*") do rmdir /s /q "%%D"
for /d %%D in ("%OUT%\runtime\Lib\site-packages\pandas*") do rmdir /s /q "%%D"
for /d %%D in ("%OUT%\runtime\Lib\site-packages\pydeck*") do rmdir /s /q "%%D"
for /d %%D in ("%OUT%\runtime\Lib\site-packages\altair*") do rmdir /s /q "%%D"

echo [5/8] 复制业务文件与二进制...
copy /y app.py "%OUT%\" >nul
copy /y tunnel.py "%OUT%\" >nul
xcopy /e /i /y bin\cloudflared-windows-amd64.exe "%OUT%\bin\" >nul

echo [6/8] 复制打包模板（bat / 使用说明 / Streamlit 主题）...
copy /y "package\启动视频工具.bat" "%OUT%\" >nul
copy /y "package\使用说明.txt" "%OUT%\" >nul
xcopy /e /i /y "package\.streamlit" "%OUT%\.streamlit" >nul

echo [7/8] 初始化运行时目录与空数据文件...
mkdir "%OUT%\videos" "%OUT%\upload_cache" "%OUT%\logs" 2>nul
echo [] > "%OUT%\api_keys.json"
echo [] > "%OUT%\tasks_history.json"

echo [8/8] 压缩 zip（约需 1-3 分钟）...
cd dist
tar -a -c -f seedance-video-tool.zip seedance-video-tool
cd ..

echo.
echo 打包完成：%ZIP%
pause
