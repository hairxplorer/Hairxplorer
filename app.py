from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/analyze")
async def analyze():
    return {"message": "Analyze endpoint reached"}

@app.post("/update-config")
async def update_config():
    return {"message": "Update config endpoint reached"}
