"""Entrypoint for hosts that run `python main.py` (spawnpoint): serve the web app on 0.0.0.0:$PORT."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
