# setup.py
from setuptools import setup, find_packages

setup(
    name="customer-support-agents",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn",
        "pydantic",
        "python-dotenv",
        "faster-whisper",
        "edge-tts",
        "anthropic",
    ],
)
