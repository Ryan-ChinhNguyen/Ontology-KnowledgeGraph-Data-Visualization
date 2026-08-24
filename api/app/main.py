from fastapi import FastAPI

from app.routers import upload

app = FastAPI(title="Ontology KG — API Service")

app.include_router(upload.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
