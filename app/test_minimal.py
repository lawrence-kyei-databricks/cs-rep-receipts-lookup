"""Minimal FastAPI app to test deployment"""
from fastapi import FastAPI

app = FastAPI(title="Test App")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "UC-native auth deployed successfully!"}
