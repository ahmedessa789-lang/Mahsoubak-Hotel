@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run RUN_SYSTEM.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -c "from app.database import SessionLocal; from app.models import User; from app.main import hash_password; db=SessionLocal(); u=db.query(User).filter(User.username=='admin').first(); u.password_hash=hash_password('admin123') if u else None; db.commit(); db.close(); print('Admin password reset to admin123')"
pause
