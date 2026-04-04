"""Server entrypoint for the LEI Lookup Tool web application."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.app:app", host="0.0.0.0", port=8000, reload=True)
