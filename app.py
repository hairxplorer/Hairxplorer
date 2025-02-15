from fastapi import FastAPI, Form, UploadFile, File, HTTPException
import os

app = FastAPI()

@app.post("/analyze")
async def analyze(
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    consent: bool = Form(...)
):
    print("DEBUG: /analyze called")
    print("DEBUG: api_key =", api_key)
    print("DEBUG: client_email =", client_email)
    print("DEBUG: consent =", consent)

    # Vérification basique des types (pour débogage)
    if not isinstance(api_key, str):
        print("DEBUG: api_key is not a string")
    if not isinstance(client_email, str):
        print("DEBUG: client_email is not a string")
    if not isinstance(consent, bool):
        print("DEBUG: consent is not a boolean")

    return {"status": "success", "message": "Analyze endpoint reached (ultra-simplified).", "api_key": api_key, "client_email": client_email, "consent": consent}


@app.get("/")  # Gardez la route root pour le health check
async def root():
    return {"message": "Hello World"}
