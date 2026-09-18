@echo off
setlocal
cd /d "%~dp0"

set "REVIEW_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%REVIEW_PYTHON%" goto run

where python >nul 2>nul
if errorlevel 1 goto missing
set "REVIEW_PYTHON=python"

:run
"%REVIEW_PYTHON%" grouping_reviewer.py --web "%~dp0..\wheresthatstock"
goto end

:missing
echo No se ha encontrado Python. Instala Python 3.10 o superior y vuelve a intentarlo.
pause

:end
endlocal
