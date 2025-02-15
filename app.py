import os
import sqlite3
import json
from fastapi import FastAPI,  HTTPException,  Depends, Body
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

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
    print("DEBUG : Clinique non trouvée")
    return None

# --- Route /update-config UNIQUEMENT ---

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
                    (config_data.api_key, config_data.email, json.dumps(config_data.pricing), 10, 10, str(datetime.utcnow().isoformat()))  # On met 10 par défaut
                )

        # AJOUTEZ CES LIGNES POUR VÉRIFIER LE CONTENU DE LA TABLE :
        print("DEBUG: Dumping clinics table after update/insert:")
        cursor = db.cursor()
        cursor.execute("SELECT * FROM clinics")
        rows = cursor.fetchall()
        for row in rows:
            print("DEBUG: Clinic Row:", row)

        print("DEBUG: Dumping analyses table after update/insert:")
        cursor.execute("SELECT * FROM analyses") #Table analyse pour verifié
        rows = cursor.fetchall()
        for row in rows:
            print("DEBUG: Analyses Row:", row)


        return {"status": "success"}

    except Exception as e:
        print("DEBUG: Exception in update_config:", e)
        raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))
    finally:
        db.close()


# --- Initialisation de la base de données au démarrage ---
@app.on_event("startup")
async def on_startup():
    db = get_db_connection()
    init_db(db)
    db.close()
    print("DEBUG: Database initialized on startup.")


# from admin import router as admin_router  # type: ignore # ON COMMENTE admin
# app.include_router(admin_router, prefix="/admin")

# if __name__ == "__main__": # PLUS BESOIN EN DEPLOIEMENT
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
