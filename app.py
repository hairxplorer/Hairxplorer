import os
import base64
import sqlite3
import json
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
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
    # Table clinics : on stocke aussi la configuration tarifaire et l'e-mail de la clinique
    with sqlite3.connect('clinics/config.db') as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clinics (
                api_key TEXT PRIMARY KEY,
                email_clinique TEXT,
                pricing TEXT  -- Exemple: '{"7": 4000, "6": 3500, "5": 3000}'
            )
        ''')
        # Table analyses : enregistrement de chaque analyse
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
    with sqlite3.connect('clinics/config.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email_clinique, pricing FROM clinics WHERE api_key = ?", (api_key,))
        row = cursor.fetchone()
        if row:
            email_clinique, pricing_str = row
            pricing = json.loads(pricing_str) if pricing_str else {}
            return {"email_clinique": email_clinique, "pricing": pricing}
    return None

def save_analysis(clinic_api_key: str, client_email: str, result: dict):
    """Enregistre une analyse dans la base de données."""
    timestamp = datetime.utcnow().isoformat()
    with sqlite3.connect('clinics/config.db') as conn:
        conn.execute(
            "INSERT INTO analyses (clinic_api_key, client_email, result, timestamp) VALUES (?, ?, ?, ?)",
            (clinic_api_key, client_email, json.dumps(result), timestamp)
        )
        conn.commit()

def send_email(to_email: str, subject: str, body: str):
    """Envoie un e-mail via SMTP (à adapter selon ton fournisseur)."""
    SMTP_SERVER = "smtp.example.com"   # Remplace par ton serveur SMTP
    SMTP_PORT = 587                    # Remplace par ton port SMTP
    SMTP_USER = "ton_email@example.com"  # Remplace par ton e-mail
    SMTP_PASSWORD = "ton_mot_de_passe"     # Remplace par ton mot de passe

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
        raise HTTPException(status_code=400, detail="Vous devez accepter que vos données soient utilisées pour vous recontacter.")
    try:
        # Traitement et redimensionnement des images
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
            raise HTTPException(status_code=500, detail="Clé OpenAI introuvable dans les variables d'environnement.")

        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4",  # Utilise un modèle textuel valide
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Donne-moi uniquement une réponse en JSON strict, sans aucun commentaire additionnel. "
                            "La réponse doit être exactement de la forme suivante, sans mentionner de traitement ou greffe :\n"
                            "{\"stade\": \"<numéro correspondant au stade sur l'échelle Norwood-Hamilton>\", "
                            "\"price_range\": \"<fourchette tarifaire spécifique à ma clinique>\", "
                            "\"details\": \"<description détaillée de l'analyse>\", "
                            "\"evaluation\": \"<évaluation précise sur l'échelle Norwood-Hamilton>\"}"
                        )}
                    ]
                }
            ],
            max_tokens=300
        )

        raw_response = response.choices[0].message.content
        print("DEBUG: Réponse OpenAI =", raw_response)

        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not match:
            raise Exception("Aucun JSON trouvé dans la réponse.")
        json_str = match.group(0)
        print("DEBUG: JSON extrait =", json_str)

        json_result = json.loads(json_str)

        # Ajustement du tarif en fonction du stade et de la configuration de la clinique
        clinic_config = get_clinic_config(api_key)
        if clinic_config and "pricing" in clinic_config:
            pricing = clinic_config["pricing"]  # Exemple: {"7": 4000, "6": 3500, "5": 3000}
            stade = json_result.get("stade", "").strip()
            if stade and stade in pricing:
                json_result["price_range"] = f"{pricing[stade]}€"
        
        save_analysis(api_key, client_email, json_result)

        if clinic_config and clinic_config.get("email_clinique"):
            sujet = "Nouvelle analyse de calvitie"
            corps = f"Voici le résultat de l'analyse effectuée pour un client ({client_email}) :\n\n{json.dumps(json_result, indent=2)}"
            send_email(clinic_config["email_clinique"], sujet, corps)
        
        sujet_client = "Votre analyse de calvitie"
        corps_client = f"Bonjour,\n\nVoici le résultat de votre analyse :\n\n{json.dumps(json_result, indent=2)}\n\nNous vous remercions de votre confiance."
        send_email(client_email, sujet_client, corps_client)

        return json_result

    except Exception as e:
        print("DEBUG: Exception =", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}

# Inclusion du routeur d'administration
from admin import router as admin_router
app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
