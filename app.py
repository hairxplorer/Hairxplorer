import os
import base64
import sqlite3
import json
import re
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

# Monter le dossier static (pour le widget web, par exemple)
app.mount("/static", StaticFiles(directory="static"), name="static")

def init_db():
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

        # Conversion de la grille en image JPEG et encodage en base64
        buffered = BytesIO()
        grid.save(buffered, format="JPEG", quality=100)
        b64_image = base64.b64encode(buffered.getvalue()).decode()

        # Récupération de la clé OpenAI depuis les variables d'environnement
        openai_api_key = os.getenv("OPENAI_API_KEY")
        print("DEBUG: OpenAI API Key =", openai_api_key)
        if not openai_api_key:
            raise HTTPException(status_code=500, detail="Clé OpenAI introuvable dans les variables d'environnement.")

        client = OpenAI(api_key=openai_api_key)
        # Demande d'une réponse JSON strict incluant un champ "evaluation"
        response = client.chat.completions.create(
            model="gpt-4",  # Utilise un modèle textuel valide
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "Donne-moi uniquement une réponse en JSON strict, sans commentaires additionnels. "
                            "La réponse doit être exactement de la forme: "
                            "{\"stade\": \"<valeur sur l'échelle Norwood-Hamilton>\", "
                            "\"price_range\": \"<fourchette tarifaire>\", "
                            "\"details\": \"<description détaillée de l'analyse>\", "
                            "\"evaluation\": \"<résultat de l'évaluation sur l'échelle Norwood-Hamilton>\"}"
                        )}
                    ]
                }
            ],
            max_tokens=300
        )

        raw_response = response.choices[0].message.content
        print("DEBUG: Réponse OpenAI =", raw_response)

        # Extraction du JSON à partir de la réponse brute
        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not match:
            raise Exception("Aucun JSON trouvé dans la réponse.")
        json_str = match.group(0)
        print("DEBUG: JSON extrait =", json_str)

        json_result = json.loads(json_str)
        return json_result

    except Exception as e:
        print("DEBUG: Exception =", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
