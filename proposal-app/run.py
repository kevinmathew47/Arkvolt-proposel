"""Start the Solar Proposal Generator.   python run.py   →   open http://localhost:8520
Other PCs on the office network: http://<this-PC's-IP>:8520"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8520")))
