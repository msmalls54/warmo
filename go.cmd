@echo off
"%USERPROFILE%\hw-test\warmo-terse-runtime\.venv\Scripts\python.exe" "%USERPROFILE%\hw-test\terse-vm\patrol_launcher.py" go %*
exit /b %errorlevel%
