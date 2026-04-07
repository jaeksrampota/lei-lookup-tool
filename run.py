"""Server entrypoint for the LEI Lookup Tool web application."""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("src.app:app", host="0.0.0.0", port=port)
