import os
import sqlite3
from fastapi import FastAPI, Depends, HTTPException, Body
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict

app = FastAPI()

# --- Modèles Pydantic (seulement ceux nécessaires pour /update-config) ---
class SMTPConfig(BaseModel):
    server: str = Field(..., env="SMTP_SERVER")
    port: int = Field(..., env="SMTP_PORT")
    user: EmailStr = Field(..., env="SMTP_USER")
    password: str = Field(..., env="SMTP_PASSWORD")

class ClinicConfigUpdate(BaseModel):
    api_key: str
    email: Optional[EmailStr] = None
    smtp: Optional[SMTPConfig] = None
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"

# --- Fonctions utilitaires (base de données) ---
DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'clinics', 'config.db')

def get_db_connection():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db(db: sqlite3.Connection):
    with db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS clinics (
                api_key TEXT PRIMARY KEY,
                email_clinique TEXT,
                pricing TEXT,
                analysis_quota INTEGER DEFAULT 0,
                default_quota INTEGER DEFAULT 0,
                subscription_start TEXT
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_api_key TEXT,
                client_email TEXT,
                result TEXT,
                timestamp TEXT,
                FOREIGN KEY(clinic_api_key) REFERENCES clinics(api_key)
            )
        ''')

async def get_db():
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()

def get_clinic_config(db: sqlite3.Connection, api_key: str):
    cursor = db.cursor()
    cursor.execute("SELECT email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics WHERE api_key = ?", (api_key,))
    row = cursor.fetchone()
    if row:
        email_clinique, pricing_str, analysis_quota, default_quota, subscription_start = row
        pricing = json.loads(pricing_str) if pricing_str else {}
        return {
            "email_clinique": email_clinique,
            "pricing": pricing,
            "analysis_quota": analysis_quota,
            "default_quota": default_quota,
            "subscription_start": subscription_start
        }
    return None


def update_clinic_quota(db: sqlite3.Connection, api_key: str, new_quota: int, new_subscription_start: str = None):
    with db: # with pour transaction
        if new_subscription_start:
            db.execute("UPDATE clinics SET analysis_quota = ?, subscription_start = ? WHERE api_key = ?", (new_quota, new_subscription_start, api_key))
        else:
            db.execute("UPDATE clinics SET analysis_quota = ? WHERE api_key = ?", (new_quota, api_key))


# --- Routes FastAPI ---

@app.get("/")  # Gardez cette route de test
async def root():
    return {"message": "Hello World"}

@app.post("/update-config") # On remet /update-config, mais simplifié
async def update_config(config_data: ClinicConfigUpdate = Body(...), db: sqlite3.Connection = Depends(get_db)):
    print("DEBUG: /update-config called")
    try:
        print("DEBUG: config_data:", config_data.dict())
    except Exception as e:
        print(f"DEBUG: Erreur Pydantic: {e}")

    existing_config = get_clinic_config(db, config_data.api_key)
    print("DEBUG: existing_config:", existing_config)

    try:
        if existing_config:
            print("DEBUG: Updating existing config")
            with db:
                db.execute(
                    "UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?",
                    (config_data.email, json.dumps(config_data.pricing), config_data.api_key)
                )
        else:
            print("DEBUG: Creating new config")
            with db:
                db.execute(
                    "INSERT INTO clinics (api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start) VALUES (?, ?, ?, ?, ?, ?)",
                    (config_data.api_key, config_data.email, json.dumps(config_data.pricing), 0, 0, None)
                )
        return {"status": "success"}

    except Exception as e:
        print("DEBUG: Exception in update_config:", e)
        raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))
    finally:
        db.close()

@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),  # <--- Form(...)
    client_email: str = Form(...),  # <--- Form(...)
    consent: bool = Form(...)  # <--- Form(...)
    , db: sqlite3.Connection = Depends(get_db)
):

# from admin import router as admin_router  # type: ignore  #<--- ON COMMENTE AUSSI
# app.include_router(admin_router, prefix="/admin")

# if __name__ == "__main__": #<-- PLUS BESOIN EN DEPLOIEMENT
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
