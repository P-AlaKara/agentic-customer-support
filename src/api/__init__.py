"""
API Package

FastAPI gateway for customer support system.

Run with:
    uvicorn src.api.gateway:app --reload
    
Or:
    python -m uvicorn src.api.gateway:app --reload
"""

from .gateway import app

__all__ = ['app']