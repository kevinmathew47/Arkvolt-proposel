"""Run the monitor locally for testing:  py -3.12 run.py  →  http://localhost:8530  (SQLite file monitor.db)"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8530)
