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
from pydantic import BaseModel, EmailStr, Field
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

DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'clinics', 'config.db')
print(f"DEBUG: DATABASE_PATH = {DATABASE_PATH}")

def get_db_connection():
    clinic_dir = os.path.dirname(DATABASE_PATH)
    print(f"DEBUG: clinics directory: {clinic_dir}")
    if not os.path.exists(clinic_dir):
        print("DEBUG: clinics directory does NOT exist. Creating...")
        os.makedirs(clinic_dir, exist_ok=True)
        print("DEBUG: clinics directory created.")
    else:
        print("DEBUG: clinics directory exists.")
    if not os.path.exists(DATABASE_PATH):
        print("DEBUG: config.db does NOT exist. Creating...")
        open(DATABASE_PATH, 'a').close()
        print("DEBUG: config.db created.")
    else:
        print("DEBUG: config.db exists.")
    db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db(db: sqlite3.Connection):
    print("DEBUG: init_db called")
    try:
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
            print("DEBUG: Table 'clinics' created or verified.")
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
            print("DEBUG: Table 'analyses' created or verified.")
    except Exception as e:
        print(f"DEBUG: Erreur dans init_db: {e}")
        raise

async def get_db():
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()

def get_clinic_config(db: sqlite3.Connection, api_key: str):
    print(f"DEBUG: get_clinic_config called with api_key: {api_key}")
    cursor = db.cursor()
    cursor.execute("SELECT email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics WHERE api_key = ?", (api_key,))
    row = cursor.fetchone()
    print(f"DEBUG: get_clinic_config, row: {row}")
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
    print("DEBUG: Clinique non trouvée")
    return None

def update_clinic_quota(db: sqlite3.Connection, api_key: str, new_quota: int, new_subscription_start: str = None):
    print(f"DEBUG: update_clinic_quota called with api_key: {api_key}, new_quota: {new_quota}, new_subscription_start: {new_subscription_start}")
    with db:
        if new_subscription_start:
            db.execute("UPDATE clinics SET analysis_quota = ?, subscription_start = ? WHERE api_key = ?", (new_quota, new_subscription_start, api_key))
        else:
            db.execute("UPDATE clinics SET analysis_quota = ? WHERE api_key = ?", (new_quota, api_key))

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

def reset_quota_if_needed(db: sqlite3.Connection, clinic_config: dict, api_key: str):
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
        with db:
            update_clinic_quota(db, api_key, default_quota, now.isoformat())
        clinic_config["analysis_quota"] = default_quota
        clinic_config["subscription_start"] = now.isoformat()
    print("DEBUG: reset_quota_if_needed finished")

def save_analysis(db: sqlite3.Connection, api_key: str, client_email: str, result: dict):
    timestamp = datetime.utcnow().isoformat()
    print("DEBUG: save_analysis called")
    with db:
        try:
            db.execute(
                "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (?, ?, ?, ?)",
                (api_key, client_email, json.dumps(result), timestamp)
            )
        except Exception as e:
            print("DEBUG: Erreur dans save_analysis", e)
            raise

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
        grid.save(buffered, format="JPEG", quality=75)
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
          print("DEBUG: OpenAI Response =", raw_response)
          match = re.search(r'\{.*\}', raw_response, re.DOTALL)
          if not match:
            raise HTTPException(status_code=500, detail="Invalid response from OpenAI: No JSON found.")
          json_str = match.group(0)
          print("DEBUG: Extracted JSON =", json_str)
          json_result = json.loads(json_str)

          if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]
            stade = json_result.get("stade", "").strip()
            if stade and stade in pricing:
                json_result["price_range"] = f"{pricing[stade]}€"

          new_quota = quota - 1
          update_clinic_quota(db, api_key, new_quota)
          with db:
            save_analysis(db, api_key, client_email, json_result)

          if (clinic_config and clinic_config.get("email_clinique")):
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
        return  # On quitte si erreur Pydantic

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
        return {"status": "success"}

    except Exception as e:
        print("DEBUG: Exception in update_config:", e)
        raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))
    finally:
        db.close()

# Initialisation de la base de données au démarrage
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
