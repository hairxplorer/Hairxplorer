import os
from dotenv import load_dotenv
load_dotenv()

import json
import re
import smtplib
import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import DictCursor
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from PIL import Image
from io import BytesIO
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict

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

# Fonction pour analyser une image individuelle via GPT-4o-mini
async def analyze_image_with_openai(file: UploadFile, label: str, client_instance: AsyncOpenAI) -> dict:
    # Redimensionner l'image à 512x512 pour conserver les détails
    img = Image.open(BytesIO(await file.read())).resize((512, 512))
    buffered = BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    b64_image = base64.b64encode(buffered.getvalue()).decode()

    # Prompt enrichi avec les informations de l'échelle Hamilton-Norwood
    prompt = (
        "Vous êtes un expert en restauration capillaire. Utilisez l'échelle de Hamilton-Norwood suivante pour évaluer la perte de cheveux chez un homme :\n\n"
        "Stade 1: Aucun signe visible de calvitie ou de dégarnissement.\n"
        "Stade 2: Légère perte sur la ligne frontale (ligne mature) et éventuellement une légère perte au vertex.\n"
        "Stade 3: Perte cliniquement significative avec dégarnissement des golfes temporaux et/ou du vertex (stade 3 vertex).\n"
        "Stade 4: Perte importante de la ligne frontale, avec une bande de cheveux reliant les côtés.\n"
        "Stade 5: Calvitie avancée, avec élargissement des zones dégarnies et amincissement de la bande.\n"
        "Stade 6: Perte très avancée, avec disparition quasi totale de la zone centrale (des tempes au vertex) et une fine couronne résiduelle.\n"
        "Stade 7: Calvitie totale sur le sommet, avec cheveux uniquement sur les côtés et l'arrière.\n\n"
        "Fournissez une réponse strictement au format JSON sans aucun commentaire supplémentaire, sous le format :\n"
        "{\"stade\": \"<numéro du stade Norwood>\", \"price_range\": \"<fourchette tarifaire>\", \"details\": \"<description détaillée>\", \"evaluation\": \"<évaluation précise>\"}\n\n"
        "Analysez l'image (vue : " + label + ") en vous basant sur la répartition et la densité des cheveux. "
        "Voici l'image encodée en base64 : " + b64_image
    )

    response = await client_instance.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.1
    )
    raw_content = response.choices[0].message.content.strip()
    print("DEBUG: Raw response for", label, ":", raw_content)
    # Nettoyage des éventuels marqueurs markdown
    raw_content = re.sub(r"^```(?:json)?\n", "", raw_content)
    raw_content = re.sub(r"\n```$", "", raw_content)
    if not raw_content:
        raise Exception("La réponse du modèle est vide pour " + label)
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError as e:
        raise Exception(f"Erreur lors du parsing JSON pour {label}: {e} -- Contenu brut: {raw_content}")

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
        
        # Analyse individuelle des images
        client_instance = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        views = {"front": front, "top": top, "side": side, "back": back}
        results = {}
        for view_label, file in views.items():
            result = await analyze_image_with_openai(file, view_label, client_instance)
            results[view_label] = result
            print(f"DEBUG: Résultat pour {view_label} :", result)
        
        final_result = results
        print("DEBUG: Final result =", final_result)
        
        if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]
            for view in final_result:
                stage = final_result[view].get("stade", "").strip()
                if stage and stage in pricing:
                    final_result[view]["price_range"] = f"{pricing[stage]}€"
        
        new_quota = quota - 1
        update_clinic_quota(db, api_key, new_quota)
        save_analysis(db, api_key, client_email, final_result)
        
        if clinic_config and clinic_config.get("email_clinique"):
            background_tasks.add_task(
                send_email_task,
                clinic_config["email_clinique"],
                "New Analysis Result",
                f"Here is the analysis result for a client ({client_email}):\n\n{json.dumps(final_result, indent=2)}"
            )
        background_tasks.add_task(
            send_email_task,
            client_email,
            "Your Analysis Result",
            f"Hello,\n\nHere is your analysis result:\n\n{json.dumps(final_result, indent=2)}\n\nThank you for your trust."
        )
        return final_result
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
