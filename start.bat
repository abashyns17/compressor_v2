@echo off
echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Starting Sullair LS110 backend...
echo Swagger UI: http://localhost:8000/docs
echo Monitor:    Open monitor.html in browser
echo.

python -m uvicorn api.main:app --reload --port 8000
