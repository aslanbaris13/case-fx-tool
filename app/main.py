"""FastAPI application entry point for the FX conversion tool.

The /tools/convert endpoint is built in the next step. This scaffold only
brings up a runnable app, so run.sh and test.sh have something real to
exercise from the very first commit.
"""

from fastapi import FastAPI

app = FastAPI(title="fx-tool", version="0.1.0")


@app.get("/health")
def health() -> dict:
    """Liveness check — used to confirm the service is up."""
    return {"status": "ok"}
