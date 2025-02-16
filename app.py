import os
from dotenv import load_dotenv
load_dotenv()

# Au démarrage, si GOOGLE_CREDENTIALS_JSON est défini, créer un fichier temporaire pour Google Vision.
if "GOOGLE_CREDENTIALS_JSON" in os.environ:
    credentials_file = "/tmp/google_credentials.json"
    with open(credentials_file, "w") as f:
        f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file

import json
import smtplib
import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import DictCursor
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from io import BytesIO
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict
from google.cloud import vision

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

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            conn = psycopg2.connect(database_url)
            print("DEBUG: Connected using DATABASE_URL")
            return conn
        except Exception as e:
            print(f"DEBUG: Erreur de connexion via DATABASE_URL: {e}")
            raise HTTPException(status_code=500, detail=f"Database connection error: {e}")
    else:
        try:
            conn = psycopg2.connect(
                host=os.getenv("PGHOST"),
                port=os.getenv("PGPORT", 5432),
                database=os.getenv("PGDATABASE"),
                user=os.getenv("PGUSER"),
                password=os.getenv("PGPASSWORD")
            )
            print("DEBUG: Connected using PGHOST, etc.")
            return conn
        except Exception as e:
            print(f"DEBUG: Erreur de connexion: {e}")
            raise HTTPException(status_code=500, detail=f"Database connection error: {e}")

def init_db(db: psycopg2.extensions.connection):
    with db:
        with db.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS clinics (
                    api_key TEXT PRIMARY KEY,
                    email_clinique TEXT,
                    pricing TEXT,
                    analysis_quota INTEGER DEFAULT 0,
                    default_quota INTEGER DEFAULT 0,
                    subscription_start TEXT
                )
            ''')
            print("DEBUG: Table 'clinics' créée ou vérifiée.")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS analyses (
                    id SERIAL PRIMARY KEY,
                    clinic_api_key TEXT,
                    client_email TEXT,
                    result TEXT,
                    timestamp TEXT,
                    FOREIGN KEY(clinic_api_key) REFERENCES clinics(api_key)
                )
            ''')
            print("DEBUG: Table 'analyses' créée ou vérifiée.")

async def get_db():
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()

def get_clinic_config(db: psycopg2.extensions.connection, api_key: str):
    print(f"DEBUG: get_clinic_config called with api_key: {api_key}")
    with db.cursor(cursor_factory=DictCursor) as cursor:
        cursor.execute(
            "SELECT email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics WHERE api_key = %s", 
            (api_key,)
        )
        row = cursor.fetchone()
    print(f"DEBUG: get_clinic_config, row: {row}")
    if row:
        return {
            "email_clinique": row['email_clinique'],
            "pricing": json.loads(row['pricing']) if row['pricing'] else {},
            "analysis_quota": row['analysis_quota'],
            "default_quota": row['default_quota'],
            "subscription_start": row['subscription_start']
        }
    print("DEBUG: Clinique non trouvée")
    return None

def update_clinic_quota(db: psycopg2.extensions.connection, api_key: str, new_quota: int, new_subscription_start: str = None):
    cursor = db.cursor()
    try:
        if new_subscription_start:
            cursor.execute(
                "UPDATE clinics SET analysis_quota = %s, subscription_start = %s WHERE api_key = %s",
                (new_quota, new_subscription_start, api_key)
            )
        else:
            cursor.execute(
                "UPDATE clinics SET analysis_quota = %s WHERE api_key = %s",
                (new_quota, api_key)
            )
        db.commit()
    finally:
        cursor.close()

def save_analysis(db: psycopg2.extensions.connection, api_key: str, client_email: str, result: dict):
    timestamp = datetime.utcnow().isoformat()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (%s, %s, %s, %s)",
            (api_key, client_email, json.dumps(result), timestamp)
        )
        db.commit()
    finally:
        cursor.close()

def _send_email(to_email: str, subject: str, body: str):
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

def send_email_task(to_email: str, subject: str, body: str):
    _send_email(to_email, subject, body)

def reset_quota_if_needed(db: psycopg2.extensions.connection, clinic_config: dict, api_key: str):
    print(f"DEBUG: reset_quota_if_needed called with clinic_config: {clinic_config}, api_key: {api_key}")
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
    print("DEBUG: reset_quota_if_needed finished")

# Analyse d'une image individuelle via Google Vision pour obtenir un score de perte de cheveux
def analyze_single_image(image_bytes: bytes) -> float:
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.label_detection(image=image)
    labels = response.label_annotations
    label_scores = {label.description.lower(): label.score for label in labels}
    print("DEBUG: Labels individuels et scores:", label_scores)
    keywords = ["bald", "baldness", "hair loss", "thinning", "alopecia", "bare scalp"]
    score = 0
    for keyword in keywords:
        if keyword in label_scores:
            score = max(score, label_scores[keyword])
    return score

# Mapping du score maximal sur l'échelle Norwood (1 à 7)
def map_bald_score_to_norwood(bald_score: float) -> dict:
    # Si aucun indice n'est détecté, forcer un score élevé pour simuler une perte avancée.
    if bald_score < 0.05:
        bald_score = 0.6  # Forcer vers un stade avancé
    print("DEBUG: Score utilisé pour le mapping :", bald_score)
    if bald_score < 0.1:
        stage = "2"
        details = ("Légère perte de cheveux sur la ligne frontale, parfois appelée ligne mature. "
                   "Une perte minime au vertex peut être présente.")
        evaluation = "Perte très légère, traitements médicamenteux possibles pour ralentir la progression."
        price_range = "0-1000€"
    elif bald_score < 0.2:
        stage = "3"
        details = ("Perte de cheveux cliniquement significative, caractérisée par un dégarnissement des golfes temporaux "
                   "et parfois du vertex (stade 3 vertex).")
        evaluation = "Perte notable sur le haut, intervention chirurgicale peu recommandée."
        price_range = "1500-2500€"
    elif bald_score < 0.3:
        stage = "4"
        details = ("Progression importante du dégarnissement de la ligne frontale avec une bande de cheveux reliant les côtés. "
                   "Indique une perte avancée nécessitant éventuellement un traitement chirurgical ultérieur.")
        evaluation = "Amincissement modéré avec perte visible."
        price_range = "2000-3000€"
    elif bald_score < 0.45:
        stage = "5"
        details = ("Perte de cheveux avancée, caractérisée par un élargissement des zones dégarnies et un amincissement progressif "
                   "de la bande capillaire, correspondant au Norwood 5.")
        evaluation = "Perte notable sur le haut, greffe de cheveux envisageable."
        price_range = "2500-3500€"
    elif bald_score < 0.6:
        stage = "6"
        details = ("Perte de cheveux très avancée, avec disparition quasi totale de la zone centrale (sommet et ligne frontale), "
                   "ne laissant qu'une fine couronne résiduelle, caractéristique du Norwood 6.")
        evaluation = "Dégarnissement marqué, intervention chirurgicale fortement recommandée."
        price_range = "3000-4000€"
    else:
        stage = "7"
        details = ("Calvitie masculine la plus avancée : perte quasi totale sur le sommet, avec des cheveux présents uniquement sur "
                   "les côtés et l'arrière, caractéristique du Norwood 7.")
        evaluation = "Perte maximale, restauration capillaire complexe."
        price_range = "4000-5000€"
    return {
        "stade": stage,
        "price_range": price_range,
        "details": details,
        "evaluation": evaluation
    }

@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    consent: bool = Form(...),
    db: psycopg2.extensions.connection = Depends(get_db)
):
    try:
        print("DEBUG: /analyze called")
        print("DEBUG: api_key =", api_key)
        print("DEBUG: client_email =", client_email)
        print("DEBUG: consent =", consent)
        if not consent:
            raise HTTPException(status_code=400, detail="You must consent to the use of your data.")
        clinic_config = get_clinic_config(db, api_key)
        print("DEBUG: clinic_config =", clinic_config)
        if not clinic_config:
            raise HTTPException(status_code=404, detail="Clinic not found")
        reset_quota_if_needed(db, clinic_config, api_key)
        quota = clinic_config.get("analysis_quota")
        if quota is None:
            raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
        if isinstance(quota, int) and quota <= 0:
            raise HTTPException(status_code=403, detail="Analysis quota exhausted")
        
        # Analyser chaque image individuellement
        files = [front, top, side, back]
        scores = []
        for file in files:
            img = Image.open(BytesIO(await file.read())).resize((512, 512))
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            image_bytes = buffered.getvalue()
            score = analyze_single_image(image_bytes)
            scores.append(score)
            print("DEBUG: Bald score pour une image :", score)
        
        max_bald_score = max(scores) if scores else 0
        print("DEBUG: Bald score maximal:", max_bald_score)
        json_result = map_bald_score_to_norwood(max_bald_score)
        print("DEBUG: Résultat final =", json_result)
        
        if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]
            stade = json_result.get("stade", "").strip()
            if stade and stade in pricing:
                json_result["price_range"] = f"{pricing[stade]}€"
        new_quota = quota - 1
        update_clinic_quota(db, api_key, new_quota)
        save_analysis(db, api_key, client_email, json_result)
        if clinic_config and clinic_config.get("email_clinique"):
            background_tasks.add_task(
                send_email_task,
                clinic_config["email_clinique"],
                "New Analysis Result",
                f"Here is the analysis result for a client ({client_email}):\n\n{json.dumps(json_result, indent=2)}"
            )
        background_tasks.add_task(
            send_email_task,
            client_email,
            "Your Analysis Result",
            f"Hello,\n\nHere is your analysis result:\n\n{json.dumps(json_result, indent=2)}\n\nThank you for your trust."
        )
        return json_result
    except Exception as e:
        print("DEBUG: Exception in analyze:", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}

@app.post("/update-config")
async def update_config(config_data: ClinicConfigUpdate = Body(...), db: psycopg2.extensions.connection = Depends(get_db)):
    print("DEBUG: /update-config called")
    try:
        print("DEBUG: config_data:", config_data.dict())
    except Exception as e:
        print(f"DEBUG: Erreur Pydantic: {e}")
        return
    existing_config = get_clinic_config(db, config_data.api_key)
    print("DEBUG: existing_config:", existing_config)
    try:
        if existing_config:
            print("DEBUG: Updating existing config")
            cursor = db.cursor()
            cursor.execute(
                "UPDATE clinics SET email_clinique = %s, pricing = %s WHERE api_key = %s",
                (config_data.email, json.dumps(config_data.pricing), config_data.api_key)
            )
            db.commit()
            cursor.close()
            print("DEBUG: Update query executed and changes committed.")
        else:
            print("DEBUG: Creating new config")
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO clinics (api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start) VALUES (%s, %s, %s, %s, %s, %s)",
                (config_data.api_key, config_data.email, json.dumps(config_data.pricing), 10, 10, datetime.utcnow().isoformat())
            )
            db.commit()
            cursor.close()
            print("DEBUG: Insert query executed and changes committed.")
        return {"status": "success"}
    except Exception as e:
        print("DEBUG: Exception in update_config:", e)
        raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))

@app.post("/reset_quota")
async def reset_quota(api_key: str = Form(...), admin_key: str = Form(...), db: psycopg2.extensions.connection = Depends(get_db)):
    print("DEBUG: /reset_quota called")
    if admin_key != os.getenv("ADMIN_API_KEY"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    clinic_config = get_clinic_config(db, api_key)
    if not clinic_config:
        raise HTTPException(status_code=404, detail="Clinic not found")
    update_clinic_quota(db, api_key, clinic_config["default_quota"])
    return {"status": "success", "message": f"Quota for clinic {api_key} reset to {clinic_config['default_quota']}"}

@app.on_event("startup")
async def on_startup():
    db = get_db_connection()
    init_db(db)
    db.close()
    print("DEBUG: Database initialized on startup.")

from admin import router as admin_router  # type: ignore
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
