#!/usr/bin/env python3
"""Production entry: honor cloud PORT, disable reload."""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("demo.app:app", host="0.0.0.0", port=port, reload=False)
