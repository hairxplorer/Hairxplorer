import os
import base64
import sqlite3
import json
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from PIL import Image
from io import BytesIO

app = FastAPI()

# Configuration CORS
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

def init_db():
    os.makedirs('clinics', exist_ok=True)
    # Table clinics : avec configuration, quota mensuel et date de souscription
    with sqlite3.connect('clinics/config.db') as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clinics (
                api_key TEXT PRIMARY KEY,
                email_clinique TEXT,
                pricing TEXT,  -- Exemple: '{"7": 4000, "6": 3500, "5": 3000}'
                analysis_quota INTEGER,
                default_quota INTEGER,
                subscription_start TEXT
            )
        ''')
        # Table analyses : enregistrement de chaque analyse effectuée
        conn.execute('''
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_api_key TEXT,
                client_email TEXT,
                result TEXT,
                timestamp TEXT,
                FOREIGN KEY(clinic_api_key) REFERENCES clinics(api_key)
            )
        ''')
        conn.commit()

init_db()

def get_clinic_config(api_key: str):
    """Récupère la configuration d'une clinique depuis la base de données."""
    with sqlite3.connect('clinics/config.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
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

def save_analysis(clinic_api_key: str, client_email: str, result: dict):
    """Enregistre une analyse dans la base de données."""
    timestamp = datetime.utcnow().isoformat()
    with sqlite3.connect('clinics/config.db', check_same_thread=False) as conn:
        conn.execute(
            "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (?, ?, ?, ?)",
            (clinic_api_key, client_email, json.dumps(result), timestamp)
        )
        conn.commit()

def update_clinic_quota(api_key: str, new_quota: int, new_subscription_start: str = None):
    """Met à jour le quota d'analyses pour une clinique. Optionnellement, met à jour la date de souscription."""
    with sqlite3.connect('clinics/config.db', check_same_thread=False) as conn:
        if new_subscription_start:
            conn.execute("UPDATE clinics SET analysis_quota = ?, subscription_start = ? WHERE api_key = ?", (new_quota, new_subscription_start, api_key))
        else:
            conn.execute("UPDATE clinics SET analysis_quota = ? WHERE api_key = ?", (new_quota, api_key))
        conn.commit()

def send_email(to_email: str, subject: str, body: str):
    """Envoie un e-mail via SMTP (à adapter selon ton fournisseur)."""
    SMTP_SERVER = "smtp.example.com"   # Remplace par votre serveur SMTP
    SMTP_PORT = 587                    # Remplace par votre port SMTP
    SMTP_USER = "ton_email@example.com"  # Remplace par votre email
    SMTP_PASSWORD = "ton_mot_de_passe"     # Remplace par votre mot de passe

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())

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
    if not consent:
        raise HTTPException(status_code=400, detail="You must consent to the use of your data.")
    try:
        # Récupération de la configuration de la clinique
        clinic_config = get_clinic_config(api_key)
        if not clinic_config:
            raise HTTPException(status_code=404, detail="Clinic not found")
        
        # Gestion de la réinitialisation mensuelle
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
            update_clinic_quota(api_key, default_quota, now.isoformat())
            clinic_config["analysis_quota"] = default_quota
            clinic_config["subscription_start"] = now.isoformat()

        quota = clinic_config.get("analysis_quota")
        if quota is None:
            raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
        if isinstance(quota, int) and quota <= 0:
            raise HTTPException(status_code=403, detail="Analysis quota exhausted")

        # Traitement des images
        images = [
            Image.open(BytesIO(await front.read())).resize((512, 512)),
            Image.open(BytesIO(await top.read())).resize((512, 512)),
            Image.open(BytesIO(await side.read())).resize((512, 512)),
            Image.open(BytesIO(await back.read())).resize((512, 512))
        ]
        grid = Image.new('RGB', (1024, 1024))
        grid.paste(images[0], (0, 0))
        grid.paste(images[1], (512, 0))
        grid.paste(images[2], (0, 512))
        grid.paste(images[3], (512, 512))
        buffered = BytesIO()
        grid.save(buffered, format="JPEG", quality=100)
        b64_image = base64.b64encode(buffered.getvalue()).decode()

        openai_api_key = os.getenv("OPENAI_API_KEY")
        print("DEBUG: OpenAI API Key =", openai_api_key)
        if not openai_api_key:
            raise HTTPException(status_code=500, detail="OpenAI API key not found in environment variables.")

        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Provide a strictly JSON response without any extra commentary. "
                            "The response must be exactly in this format, without mentioning treatment or surgery:\n"
                            "{\"stade\": \"<Norwood stage number>\", "
                            "\"price_range\": \"<pricing based on configuration>\", "
                            "\"details\": \"<detailed analysis description>\", "
                            "\"evaluation\": \"<precise evaluation on the Norwood scale>\"}"
                        )}
                    ]
                }
            ],
            max_tokens=300
        )
        raw_response = response.choices[0].message.content
        print("DEBUG: OpenAI Response =", raw_response)
        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not match:
            raise Exception("No JSON found in the response.")
        json_str = match.group(0)
        print("DEBUG: Extracted JSON =", json_str)
        json_result = json.loads(json_str)

        # Ajustement tarifaire selon le stade
        if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]  # e.g. {"7":4000, "6":3500, "5":3000}
            stade = json_result.get("stade", "").strip()
            if stade and stade in pricing:
                json_result["price_range"] = f"{pricing[stade]}€"
        
        # Décrémentation du quota
        new_quota = quota - 1
        update_clinic_quota(api_key, new_quota)

        save_analysis(api_key, client_email, json_result)

        if clinic_config and clinic_config.get("email_clinique"):
            sujet = "New Analysis Result"
            corps = f"Here is the analysis result for a client ({client_email}):\n\n{json.dumps(json_result, indent=2)}"
            send_email(clinic_config["email_clinique"], sujet, corps)
        
        sujet_client = "Your Analysis Result"
        corps_client = f"Hello,\n\nHere is your analysis result:\n\n{json.dumps(json_result, indent=2)}\n\nThank you for your trust."
        send_email(client_email, sujet_client, corps_client)

        return json_result

    except Exception as e:
        print("DEBUG: Exception =", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}

@app.post("/update-config")
async def update_config(api_key: str = Form(...), config: str = Form(...)):
    try:
        config_data = json.loads(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON configuration: " + str(e))
    try:
        with sqlite3.connect('clinics/config.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            # On met à jour l'email_clinique et le pricing dans la table clinics
            cursor.execute("UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?",
                           (config_data.get("email"), json.dumps(config_data.get("pricing", {})), api_key))
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error updating configuration: " + str(e))

# Endpoint d'administration via /admin sera inclus par le routeur
from admin import router as admin_router
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
