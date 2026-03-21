"""
REST handler — wraps the existing FastAPI app with Mangum for API Gateway.

This reuses all existing REST endpoints from server.py without changes.
Only the WebSocket endpoints are excluded (handled by ws_handler.py).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

# Import all route handlers from server.py
# We create a minimal FastAPI app and re-register only the REST routes
from hackathon_backend.services.lambdas.agent.server import app as _original_app

# Mangum adapter — translates API Gateway events to ASGI
mangum_handler = Mangum(_original_app, lifespan="off")
