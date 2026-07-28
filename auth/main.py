from fastapi import FastAPI
from auth.database.session import engine

app = FastAPI(
    title="Auth System",
    version="1.0.0",
)


@app.get("/")
async def root():
    return {"message": "Server Running..."}

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        print("database connected!")