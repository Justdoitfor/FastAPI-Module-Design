from fastapi import FastAPI
from auth.database.session import engine
from auth.api import auth

app = FastAPI(
    title="Auth System",
    version="1.0.0",
)
app.include_router(auth.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "Server Running..."}


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        print("database connected!")
