@echo off
"%USERPROFILE%\hw-test\warmo-terse-runtime\.venv\Scripts\python.exe" "%USERPROFILE%\hw-test\terse-vm\patrol_launcher.py" new %*
exit /b %errorlevel%
