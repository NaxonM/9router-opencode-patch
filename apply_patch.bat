@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   9Router OpenCode Zen Dual-Gate Hotfix Launcher
echo ============================================================
echo.

python patch_9router.py %*

echo.
echo ============================================================
echo   Done.
echo ============================================================

@goto #_undefined_# 2>NUL
