import os
import base64
import sqlite3
import json
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI  # Utilisez la version asynchrone
from PIL import Image
from io import BytesIO
from pydantic import BaseModel, EmailStr, validator, Field  # Importez Pydantic
from typing import Optional, Dict
from dotenv import load_dotenv  # Pour charger les variables d'environnement

# Charger les variables d'environnement depuis un fichier .env (s'il existe)
load_dotenv()

app = FastAPI()

# Configuration CORS (laissez-la telle quelle)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Content-Length"]
)

# Monter le dossier static (pour le widget web)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Modèles Pydantic ---
class SMTPConfig(BaseModel):
    server: str = Field(..., env="SMTP_SERVER")  # Lit depuis la variable d'environnement
    port: int = Field(..., env="SMTP_PORT")
    user: EmailStr = Field(..., env="SMTP_USER")
    password: str = Field(..., env="SMTP_PASSWORD")

class ClinicConfig(BaseModel):
    email: Optional[EmailStr] = None  # Email de la clinique
    smtp: Optional[SMTPConfig] = None # config smtp
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"  # Valeur par défaut

class AnalysisRequest(BaseModel):
    api_key: str
    client_email: EmailStr
    consent: bool

class AnalysisResult(BaseModel):  # Modèle pour la réponse *attendue* de l'API
    stade: str
    price_range: Optional[str] = None # on met optional car on va le geerer nous même
    details: str
    evaluation: str

class ClinicConfigUpdate(BaseModel): # Model pour update-config
    api_key: str
    email: Optional[EmailStr] = None
    smtp: Optional[SMTPConfig] = None # config smtp
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"

# --- Fonctions utilitaires ---

def get_db_connection():
    """Crée une nouvelle connexion à la base de données en mémoire."""
    db = sqlite3.connect(':memory:')
    # Charger la base de données depuis le fichier
    if os.path.exists('clinics/config.db'):
        with sqlite3.connect('clinics/config.db') as disk_conn:
            disk_conn.backup(db)  # Copie le contenu du fichier dans la DB en mémoire
    return db

def init_db(db: sqlite3.Connection):
    """Initialise la base de données (en mémoire)."""
    # Table clinics
    db.execute('''
        CREATE TABLE IF NOT EXISTS clinics (
            api_key TEXT PRIMARY KEY,
            email_clinique TEXT,
            pricing TEXT,
            analysis_quota INTEGER,
            default_quota INTEGER,
            subscription_start TEXT
        )
    ''')
    # Table analyses
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
    db.commit()


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

def save_analysis(db: sqlite3.Connection, clinic_api_key: str, client_email: str, result: dict):
    """Enregistre une analyse."""
    timestamp = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (?, ?, ?, ?)",
        (clinic_api_key, client_email, json.dumps(result), timestamp)
    )
    db.commit()

def update_clinic_quota(db: sqlite3.Connection, api_key: str, new_quota: int, new_subscription_start: str = None):
    """Met à jour le quota et/ou la date de souscription."""
    if new_subscription_start:
        db.execute("UPDATE clinics SET analysis_quota = ?, subscription_start = ? WHERE api_key = ?", (new_quota, new_subscription_start, api_key))
    else:
        db.execute("UPDATE clinics SET analysis_quota = ? WHERE api_key = ?", (new_quota, api_key))
    db.commit()

def _send_email(to_email: str, subject: str, body: str):
    """Fonction interne pour envoyer un e-mail (ne pas exposer directement)."""
    try:
      smtp_config = SMTPConfig() # on recupere les information pour ce connecter au smtp
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
        update_clinic_quota(db, api_key, default_quota, now.isoformat())
        clinic_config["analysis_quota"] = default_quota
        clinic_config["subscription_start"] = now.isoformat()

def save_db(db: sqlite3.Connection):
    """Sauvegarde la base de données en mémoire sur disque."""
    with sqlite3.connect('clinics/config.db') as disk_conn:
        db.backup(disk_conn)

# --- Routes FastAPI ---
@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    request_data: AnalysisRequest = Depends()  # Utilisez Depends pour le modèle Pydantic
    , db: sqlite3.Connection = Depends(get_db_connection)
):
    if not request_data.consent:
        raise HTTPException(status_code=400, detail="You must consent to the use of your data.")

    clinic_config = get_clinic_config(db, request_data.api_key)
    if not clinic_config:
        raise HTTPException(status_code=404, detail="Clinic not found")

    reset_quota_if_needed(db, clinic_config, request_data.api_key)

    quota = clinic_config.get("analysis_quota")
    if quota is None:
        raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
    if isinstance(quota, int) and quota <= 0:
        raise HTTPException(status_code=403, detail="Analysis quota exhausted")

    # Traitement des images (réduction de la qualité)
    images = [
        Image.open(BytesIO(await file.read())).resize((512, 512))
        for file in [front, top, side, back]
    ]
    grid = Image.new('RGB', (1024, 1024))
    grid.paste(images[0], (0, 0))
    grid.paste(images[1], (512, 0))
    grid.paste(images[2], (0, 512))
    grid.paste(images[3], (512, 512))
    buffered = BytesIO()
    grid.save(buffered, format="JPEG", quality=75)  # Qualité réduite
    b64_image = base64.b64encode(buffered.getvalue()).decode()

    # Appel asynchrone à OpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        response = await client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Provide a strictly JSON response without any extra commentary. "
                            "The response must be exactly in the following format, without mentioning treatment or surgery:\n"
                            "{\"stade\": \"<Norwood stage number>\", "
                            "\"price_range\": \"<pricing based on configuration>\", "
                            "\"details\": \"<detailed analysis description>\", "
                            "\"evaluation\": \"<precise evaluation on the Norwood scale>\"}"
                        )},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                        }

                    ]
                }
            ],
            max_tokens=300
        )
        raw_response = response.choices[0].message.content
        print("DEBUG: OpenAI Response =", raw_response) # pour le debug
        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not match:
            raise HTTPException(status_code=500, detail="Invalid response from OpenAI: No JSON found.")
        json_str = match.group(0)
        print("DEBUG: Extracted JSON =", json_str)

         # Parsing et validation de la réponse avec Pydantic
        try:
            json_result = AnalysisResult.parse_raw(json_str)  # Utilisation de parse_raw
            json_result = json_result.dict() # on le converti en dict
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Invalid response from OpenAI: {e}")

        # Ajustement du tarif
        if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]
            stade = json_result.get("stade", "").strip()
            if stade and stade in pricing:
                json_result["price_range"] = f"{pricing[stade]}€"

        # Décrémentation du quota (après l'appel réussi à OpenAI)
        new_quota = quota - 1
        update_clinic_quota(db, request_data.api_key, new_quota)

        # Enregistrement de l'analyse
        save_analysis(db, request_data.api_key, request_data.client_email, json_result)


        # Envoi d'e-mails (en arrière-plan)
        if clinic_config and clinic_config.get("email_clinique"):
            background_tasks.add_task(
                send_email_task,
                clinic_config["email_clinique"],
                "New Analysis Result",
                f"Here is the analysis result for a client ({request_data.client_email}):\n\n{json.dumps(json_result, indent=2)}"
            )

        background_tasks.add_task(
            send_email_task,
            request_data.client_email,
            "Your Analysis Result",
            f"Hello,\n\nHere is your analysis result:\n\n{json.dumps(json_result, indent=2)}\n\nThank you for your trust."
        )
        save_db(db) # on sauvegarde
        return json_result

    except Exception as e:
        print("DEBUG: Exception =", e)  # Log pour le débogage
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def health_check():
    return {"status": "online"}


# --- Endpoint pour mettre à jour *ou créer* une configuration de clinique ---
@app.post("/update-config")
async def update_config(config_data: ClinicConfigUpdate = Body(...), db: sqlite3.Connection = Depends(get_db_connection)):

    existing_config = get_clinic_config(db, config_data.api_key)

    try:
        if existing_config:
            # Mise à jour de la configuration existante
            db.execute(
                "UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?",
                (config_data.email, json.dumps(config_data.pricing), config_data.api_key)
            )
        else:
            # Création d'une nouvelle configuration
            db.execute(
                "INSERT INTO clinics (api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start) VALUES (?, ?, ?, ?, ?, ?)",
                (config_data.api_key, config_data.email, json.dumps(config_data.pricing), 0, 0, None)  # Valeurs par défaut
            )
        db.commit()
        save_db(db) # on sauvegarde
        return {"status": "success"}

    except Exception as e:
        raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))


# Inclusion du routeur d'administration (assurez-vous qu'il est compatible)
from admin import router as admin_router  # type: ignore # si admin est optionnel
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))