import os
import base64
import sqlite3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# Initialisation base de données
def init_db():
    with sqlite3.connect('clinics/config.db') as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clinics (
                api_key TEXT PRIMARY KEY,
                email TEXT,
                privacy_policy_url TEXT
            )
        ''')

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
        # Traitement des images
        images = [
            Image.open(BytesIO(await front.read())).resize((512, 512)),
            Image.open(BytesIO(await top.read())).resize((512, 512)),
            Image.open(BytesIO(await side.read())).resize((512, 512)),
            Image.open(BytesIO(await back.read())).resize((512, 512))
        ]

        # Création de la grille
        grid = Image.new('RGB', (1024, 1024))
        grid.paste(images[0], (0, 0))
        grid.paste(images[1], (512, 0))
        grid.paste(images[2], (0, 512))
        grid.paste(images[3], (512, 512))

        # Conversion base64
        buffered = BytesIO()
        grid.save(buffered, format="JPEG", quality=100)
        b64_image = base64.b64encode(buffered.getvalue()).decode()

        # Analyse OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyse Norwood-Hamilton - Réponse JSON : {stade: 1-7, prix: 'XXXX-YYYY€'}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }
            ],
            max_tokens=300
        )

        return {"result": response.choices[0].message.content}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "online"}