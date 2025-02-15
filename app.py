import os
import base64
import sqlite3
import json
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from PIL import Image
from io import BytesIO
from pydantic import BaseModel, EmailStr, validator, Field
from typing import Optional, Dict
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Content-Length"]
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Modèles Pydantic ---
class SMTPConfig(BaseModel):
    server: str = Field(..., env="SMTP_SERVER")
    port: int = Field(..., env="SMTP_PORT")
    user: EmailStr = Field(..., env="SMTP_USER")
    password: str = Field(..., env="SMTP_PASSWORD")

class ClinicConfigUpdate(BaseModel):  # Utiliser pour /update-config
    api_key: str
    email: Optional[EmailStr] = None
    smtp: Optional[SMTPConfig] = None
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"

# --- Fonctions utilitaires ---

DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'clinics', 'config.db') # Chemin absolu

def get_db_connection():
    """Crée une nouvelle connexion à la base de données *fichier*, thread-safe."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)  # Crée le répertoire!
    db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)  # IMPORTANT: check_same_thread=False
    db.execute("PRAGMA journal_mode=WAL")  # Amélioration pour la concurrence
    return db

def init_db(db: sqlite3.Connection):
    """Initialise la base de données (en mémoire)."""
    with db: # with pour transaction
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

async def get_db(): #Fonction pour FastAPI
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()


def get_clinic_config(db: sqlite3.Connection, api_key: str):
    """Récupère la configuration d'une clinique."""
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
    """Met à jour le quota et/ou la date de souscription."""
    with db: # with pour transaction
        if new_subscription_start:
            db.execute("UPDATE clinics SET analysis_quota = ?, subscription_start = ? WHERE api_key = ?", (new_quota, new_subscription_start, api_key))
        else:
            db.execute("UPDATE clinics SET analysis_quota = ? WHERE api_key = ?", (new_quota, api_key))


def _send_email(to_email: str, subject: str, body: str):
    """Fonction interne pour envoyer un e-mail (ne pas exposer directement)."""
    try:
      smtp_config = SMTPConfig()
      msg = MIMEText(body, "plain", "utf-8")
      msg["Subject"] = subject
      msg["From"] = smtp_config.user
      msg["To"] = to_email
      with smtplib.SMTP(smtp_config.server, smtp_config.port) as server:
          server.starttls()
          server.login(smtp_config.user, smtp_config.password)
          server.sendmail(smtp_config.user, [to_email], msg.as_string())
    except Exception as e:
        print(f"Error sending email: {e}")
        # Gérer l'erreur (journaliser, réessayer, etc.)

def send_email_task(to_email: str, subject: str, body: str):
    """Tâche en arrière-plan pour envoyer un e-mail."""
    _send_email(to_email, subject, body)

def reset_quota_if_needed(db: sqlite3.Connection, clinic_config: dict, api_key: str):
    """Réinitialise le quota si nécessaire."""
    subscription_start = clinic_config.get("subscription_start")
    now = datetime.utcnow()
    reset_quota = False

    if subscription_start:
        start_dt = datetime.fromisoformat(subscription_start)
        if now - start_dt >= timedelta(days=30):
            reset_quota = True
    else:
        reset_quota = True

    if reset_quota:
        default_quota = clinic_config.get("default_quota")
        if default_quota is None:
            raise HTTPException(status_code=400, detail="Default quota is not defined for this clinic.")
        with db:
            update_clinic_quota(db, api_key, default_quota, now.isoformat())
        clinic_config["analysis_quota"] = default_quota
        clinic_config["subscription_start"] = now.isoformat()

def save_analysis(db: sqlite3.Connection, api_key: str, client_email: str, result: dict):
    """Enregistre une analyse."""
    timestamp = datetime.utcnow().isoformat()
    with db:
        db.execute(
            "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (?, ?, ?, ?)",
            (api_key, client_email, json.dumps(result), timestamp)
        )

# --- Routes FastAPI ---
@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    consent: bool = Form(...)
    , db: sqlite3.Connection = Depends(get_db)
):
    try:
        print("DEBUG: /analyze called")  # Ajouté
        print("DEBUG: api_key =", api_key)        # Ajouté
        print("DEBUG: client_email =", client_email)  # Ajouté
        print("DEBUG: consent =", consent)          # Ajouté

        if not consent:
            raise HTTPException(status_code=400, detail="You must consent to the use of your data.")

        clinic_config = get_clinic_config(db, api_key)
        print("DEBUG: clinic_config =", clinic_config)  # Ajouté
        if not clinic_config:
            raise HTTPException(status_code=404, detail="Clinic not found")

        reset_quota_if_needed(db, clinic_config, api_key)

        quota = clinic_config.get("analysis_quota")
        if quota is None:
            raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
        if isinstance(quota, int) and quota <= 0:
            raise HTTPException(status_code=403, detail="Analysis quota exhausted")

        # --- On commente TOUJOURS la partie images, OpenAI, et email ---
        # images = [
        #     ...
        # ]
        # ...
        # client = AsyncOpenAI(...)
        # ...
        # json_result = ...
        # ...

        # --- On simule un résultat pour l'instant ---
        # new_quota = quota - 1  # On ne décrémente PAS encore le quota
        # update_clinic_quota(db, api_key, new_quota) # Ni ici
        # with db:
        #     save_analysis(db, api_key, client_email, {"stade": "VII", "price_range": "1234", "details": "Test", "evaluation": "Test"})  # On ne sauvegarde RIEN

        # --- On renvoie une réponse de succès, avec les données reçues ---
        return {
            "status": "success",
            "message": "Analyze endpoint reached (DB logic partially reintroduced).",
            "api_key": api_key,          # On renvoie les données reçues
            "client_email": client_email,  # pour vérification
            "consent": consent
        }

    except Exception as e:
        print("DEBUG: Exception in analyze:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        db.close()

@app.get("/")
def health_check():
    return {"status": "online"}

@app.post("/update-config")
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

# Initialisation de la base de données au démarrage
@app.on_event("startup")
async def startup_event():
    db = get_db_connection()
    init_db(db)
    db.close()
    print("DEBUG: Database initialized on startup.")

from admin import router as admin_router  # type: ignore
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
