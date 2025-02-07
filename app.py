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

class ClinicConfig(BaseModel):  #Inutile pour /update-config
    email: Optional[EmailStr] = None
    smtp: Optional[SMTPConfig] = None
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"

class AnalysisRequest(BaseModel): #Inutile pour /update-config
    api_key: str
    client_email: EmailStr
    consent: bool

class AnalysisResult(BaseModel):  #Inutile pour /update-config
    stade: str
    price_range: Optional[str] = None
    details: str
    evaluation: str

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


# --- Routes FastAPI ---
@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    request_data: AnalysisRequest = Depends()
    , db: sqlite3.Connection = Depends(get_db)  #Utilisation de get_db
):
    try:
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

            try:
                json_result = AnalysisResult.parse_raw(json_str)
                json_result = json_result.dict()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Invalid response from OpenAI: {e}")

            if clinic_config and "pricing" in clinic_config:
                pricing = clinic_config["pricing"]
                stade = json_result.get("stade", "").strip()
                if stade and stade in pricing:
                    json_result["price_range"] = f"{pricing[stade]}€"

            new_quota = quota - 1
            update_clinic_quota(db, request_data.api_key, new_quota)
            with db:
                save_analysis(db, request_data.api_key, request_data.client_email, json_result)

            if (clinic_config and clinic_config.get("email_clinique")):
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
            return json_result

        except Exception as e:
            print("DEBUG: Exception =", e)
            raise HTTPException(status_code=500, detail=str(e))

    finally:  # Ferme la connexion dans tous les cas
        db.close()

@app.get("/")
def health_check():
    return {"status": "online"}

@app.post("/update-config")
async def update_config(config_data: ClinicConfigUpdate = Body(...), db: sqlite3.Connection = Depends(get_db)): #Utilisation de get_db
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
            with db: # with pour transaction
                db.execute(
                    "UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?",
                    (config_data.email, json.dumps(config_data.pricing), config_data.api_key)
                )
        else:
            print("DEBUG: Creating new config")
            with db: # with pour transaction
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



from admin import router as admin_router  # type: ignore
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))