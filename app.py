import os
import base64
import sqlite3
import json
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
)

# Monter le dossier static pour servir les fichiers JS et CSS
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialisation de la base de données
def init_db():
    # Crée le dossier 'clinics' s'il n'existe pas
    os.makedirs('clinics', exist_ok=True)
    with sqlite3.connect('clinics/config.db') as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clinics (
                api_key TEXT PRIMARY KEY,
                email TEXT,
                privacy_policy_url TEXT
            )
        ''')
        conn.commit()

init_db()

@app.post("/analyze")
async def analyze(
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...)
):
    try:
        # Traitement et redimensionnement des images
        images = [
            Image.open(BytesIO(await front.read())).resize((512, 512)),
            Image.open(BytesIO(await top.read())).resize((512, 512)),
            Image.open(BytesIO(await side.read())).resize((512, 512)),
            Image.open(BytesIO(await back.read())).resize((512, 512))
        ]

        # Création d'une grille 1024x1024
        grid = Image.new('RGB', (1024, 1024))
        grid.paste(images[0], (0, 0))
        grid.paste(images[1], (512, 0))
        grid.paste(images[2], (0, 512))
        grid.paste(images[3], (512, 512))

        # Conversion de la grille en image JPEG en base64
        buffered = BytesIO()
        grid.save(buffered, format="JPEG", quality=100)
        b64_image = base64.b64encode(buffered.getvalue()).decode()

        # Debug : vérifier la clé OpenAI
        openai_api_key = os.getenv("OPENAI_API_KEY")
        print("DEBUG: OpenAI API Key =", openai_api_key)
        if not openai_api_key:
            raise HTTPException(status_code=500, detail="Clé OpenAI introuvable dans les variables d'environnement.")

        # Appel à l'API OpenAI
        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        # On force ici un format JSON connu pour faciliter le test.
                        {"type": "text", "text": "Analyse Norwood-Hamilton - Réponse JSON : {\"stade\": \"1-7\", \"price_range\": \"1500-2000€\", \"details\": \"Quelques détails d'analyse.\"}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }
            ],
            max_tokens=300
        )

        # Debug : afficher la réponse brute d'OpenAI
        print("DEBUG: Réponse OpenAI =", response.choices[0].message.content)

        # Décodage de la réponse OpenAI (on suppose que c'est une chaîne JSON valide)
        json_result = json.loads(response.choices[0].message.content)
        return json_result

    except Exception as e:
        print("DEBUG: Exception =", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
