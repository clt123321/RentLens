@echo off
chcp 65001 >nul 2>&1
echo ============================================================
echo   高端公寓租房筛选器 - 海淀区
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] 未找到虚拟环境，正在创建...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    call .venv\Scripts\activate.bat
)

if not exist ".env" (
    echo [!] 未找到 .env 配置文件
    echo     请复制 .env.example 为 .env 并填入你的 Cookie 和 API Key
    copy .env.example .env
    echo     已创建 .env 文件，请编辑后重新运行
    pause
    exit /b 1
)

python main.py %*

echo.
echo 完成！结果保存在 output\ 目录下
pause
