import os
from dotenv import load_dotenv
load_dotenv()

import json
import re
import smtplib
import base64
import cv2
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from PIL import Image, ImageEnhance

import psycopg2
from psycopg2.extras import DictCursor
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, EmailStr, Field

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

app.mount("/static", StaticFiles(directory="static"), name="static")

# Modèles Pydantic
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

# Classe d'analyse capillaire
class HairLossAnalyzer:
    def __init__(self):
        self.norwood_classifications = {
            "1": "Aucune perte visible",
            "2A": "Légère récession frontotemporale",
            "2": "Récession triangulaire frontotemporale",
            "3": "Récession frontotemporale marquée",
            "3A": "Récession frontale avec amincissement du vertex",
            "3V": "Amincissement du vertex avec récession frontale limitée",
            "4": "Calvitie frontale et vertex sévère",
            "4A": "Calvitie frontale dominante",
            "5": "Motif en fer à cheval",
            "5A": "Perte du vertex étendue",
            "6": "Calvitie avancée avec bande latérale",
            "7": "Calvitie totale avec couronne résiduelle"
        }
        
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    def preprocess_image(self, image: Image.Image) -> Image.Image:
        """Améliore la qualité de l'image pour l'analyse"""
        if image.mode != 'RGB':
            image = image.convert('RGB')
            
        enhancers = [
            (ImageEnhance.Contrast, 1.2),
            (ImageEnhance.Brightness, 1.1),
            (ImageEnhance.Sharpness, 1.5)
        ]
        
        for enhancer_type, factor in enhancers:
            enhancer = enhancer_type(image)
            image = enhancer.enhance(factor)
            
        return image

    def analyze_anatomy(self, image: Image.Image) -> dict:
        """Détecte les points anatomiques clés"""
        img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        faces = self.face_cascade.detectMultiScale(img_cv, 1.1, 4)
        
        if not faces:
            return {}
        
        x, y, w, h = faces[0]
        return {
            'face_bbox': (x, y, w, h),
            'temporal_points': self._get_temporal_points(x, y, w, h),
            'vertex_position': (x + w//2, y + h//3)
        }

    def _get_temporal_points(self, x, y, w, h):
        return [
            (x + int(w*0.15), y + h//3),
            (x + int(w*0.85), y + h//3)
        ]

    def measure_density(self, image: Image.Image, region: Tuple[int, int, int, int]) -> float:
        """Mesure la densité capillaire sur une région spécifique"""
        img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        crop = img_cv[region[1]:region[3], region[0]:region[2]]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return np.sum(thresh == 255) / thresh.size

# Fonctions de base de données
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    try:
        return psycopg2.connect(database_url or "")
    except:
        return psycopg2.connect(
            host=os.getenv("PGHOST"),
            port=os.getenv("PGPORT", 5432),
            database=os.getenv("PGDATABASE"),
            user=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD")
        )

def init_db(db):
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analyses (
                id SERIAL PRIMARY KEY,
                clinic_api_key TEXT,
                client_email TEXT,
                result TEXT,
                timestamp TEXT,
                metadata TEXT
            )
        ''')
    db.commit()

# Endpoints FastAPI
@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    age: int = Form(30),
    family_history: bool = Form(False),
    consent: bool = Form(...),
    db=Depends(get_db)
):
    try:
        # Vérifications initiales
        if not consent:
            raise HTTPException(400, "Consentement requis")
        
        clinic_config = get_clinic_config(db, api_key)
        if not clinic_config:
            raise HTTPException(404, "Clinique non trouvée")
        
        # Gestion des quotas
        reset_quota_if_needed(db, clinic_config, api_key)
        if clinic_config['analysis_quota'] <= 0:
            raise HTTPException(403, "Quota épuisé")

        # Analyse des images
        analyzer = HairLossAnalyzer()
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        async def process_image(file: UploadFile, label: str):
            img = Image.open(BytesIO(await file.read())).resize((1024, 1024))
            img = analyzer.preprocess_image(img)
            anatomy = analyzer.analyze_anatomy(img)
            
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=90)
            b64_image = base64.b64encode(buffered.getvalue()).decode()

            prompt = f"""
            Analysez cette image ({label}) selon :
            1. Échelle de Norwood-Hamilton
            2. Densité capillaire (échelle Savin)
            3. Récession temporale
            4. Miniaturisation
            5. Motif de perte
            
            Points anatomiques : {anatomy}
            
            Réponse JSON :
            {{
                "stade": "string",
                "sous_type": "string",
                "densite": 0-100,
                "zones_affectees": ["liste"],
                "traitements": ["liste"],
                "confiance": 0-100
            }}
            """

            response = await client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": prompt + f"\nImage: {b64_image}"}],
                response_format={"type": "json_object"},
                max_tokens=1000
            )
            
            result = json.loads(response.choices[0].message.content)
            result.update({
                "anatomy": anatomy,
                "view": label,
                "timestamp": datetime.now().isoformat()
            })
            return result

        # Traitement parallèle des images
        results = {
            "front": await process_image(front, "front"),
            "top": await process_image(top, "top"),
            "side": await process_image(side, "side"),
            "back": await process_image(back, "back")
        }

        # Agrégation des résultats
        final_result = self.aggregate_results(results, age, family_history)
        
        # Mise à jour de la base de données
        update_clinic_quota(db, api_key, clinic_config['analysis_quota'] - 1)
        save_analysis(db, api_key, client_email, final_result)
        
        # Envoi des emails
        if clinic_config.get('email_clinique'):
            background_tasks.add_task(
                send_email,
                clinic_config['email_clinique'],
                "Nouvelle analyse capillaire",
                f"Résultats pour {client_email}:\n{json.dumps(final_result, indent=2)}"
            )
            
        background_tasks.add_task(
            send_email,
            client_email,
            "Vos résultats d'analyse",
            f"Bonjour,\n\nVos résultats :\n{json.dumps(final_result, indent=2)}\n\nCordialement"
        )

        return final_result

    except Exception as e:
        raise HTTPException(500, f"Erreur interne : {str(e)}")

# Fonctions utilitaires
def aggregate_results(results: dict, age: int, family_history: bool) -> dict:
    """Fusionne les résultats des différentes vues"""
    main_stages = [v['stade'] for v in results.values()]
    subtypes = [v['sous_type'] for v in results.values() if v.get('sous_type')]
    
    return {
        "stade_principal": max(main_stages, key=lambda x: int(x[0])),
        "sous_type_dominant": max(subtypes, key=subtypes.count) if subtypes else None,
        "densite_moyenne": np.mean([v['densite'] for v in results.values()]),
        "zones_affectees": list(set().union(*[v['zones_affectees'] for v in results.values()])),
        "traitements_recommandes": self.get_treatments(results),
        "risque_progression": self.predict_progression(results, age, family_history)
    }

def get_treatments(results: dict) -> list:
    treatments = []
    for v in results.values():
        treatments.extend(v['traitements'])
    return sorted(list(set(treatments)), key=lambda x: treatments.count(x), reverse=True)

def predict_progression(results: dict, age: int, history: bool) -> dict:
    base_risk = "Modéré" if age < 40 else "Élevé"
    if history and any(int(s['stade'][0]) > 3 for s in results.values()):
        base_risk = "Très élevé"
    return {"niveau": base_risk, "suivi_recommandé": "Trimestriel" if base_risk != "Modéré" else "Annuel"}

@app.on_event("startup")
async def startup():
    db = get_db_connection()
    init_db(db)
    db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
