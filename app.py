import os
# import base64  # Commenté
import sqlite3  # Commenté
import json
import re
import smtplib  # Commenté pour l'instant
from email.mime.text import MIMEText  # Commenté pour l'instant
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
# from openai import AsyncOpenAI # Commenté pour l'instant
from PIL import Image  # Commenté pour l'instant
from io import BytesIO  # Commenté pour l'instant
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
# On garde les modeles pour la validation des données entrantes
class SMTPConfig(BaseModel):
    server: str = Field(..., env="SMTP_SERVER")
    port: int = Field(..., env="SMTP_PORT")
    user: EmailStr = Field(..., env="SMTP_USER")
    password: str = Field(..., env="SMTP_PASSWORD")

# class AnalysisRequest(BaseModel):  # Plus besoin, on utilise Form(...)
#     api_key: str
#     client_email: EmailStr
#     consent: bool

# class AnalysisResult(BaseModel):  # Plus besoin pour l'instant
#     stade: str
#     price_range: Optional[str] = None
#     details: str
#     evaluation: str

class ClinicConfigUpdate(BaseModel):
    api_key: str
    email: Optional[EmailStr] = None
    smtp: Optional[SMTPConfig] = None
    pricing: Dict[str, int] = {}
    button_color: str = "#0000ff"

# --- Fonctions utilitaires : TOUT COMMENTER ---
# DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'clinics', 'config.db')

# def get_db_connection():
#    ...

# def init_db(db: sqlite3.Connection):
#     ...

# async def get_db():
#     ...

# def get_clinic_config(db: sqlite3.Connection, api_key: str):
#    ...

# def update_clinic_quota(db: sqlite3.Connection, api_key: str, new_quota: int, new_subscription_start: str = None):
#    ...

# def _send_email(to_email: str, subject: str, body: str):
#    ...

# def send_email_task(to_email: str, subject: str, body: str):
#     ...

# def reset_quota_if_needed(db: sqlite3.Connection, clinic_config: dict, api_key: str):
#     ...


# --- Routes FastAPI ---
@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks, # Gardez background_tasks
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    consent: bool = Form(...)
    #, db: sqlite3.Connection = Depends(get_db)  # On retire la dépendance à la DB
):
    # try:  # On commente le try...except...finally pour l'instant
    #     if not consent:
    #         raise HTTPException(status_code=400, detail="You must consent to the use of your data.")

        # clinic_config = get_clinic_config(db, api_key)
        # if not clinic_config:
        #     raise HTTPException(status_code=404, detail="Clinic not found")

        # reset_quota_if_needed(db, clinic_config, api_key)

        # quota = clinic_config.get("analysis_quota")
        # if quota is None:
        #     raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
        # if isinstance(quota, int) and quota <= 0:
        #     raise HTTPException(status_code=403, detail="Analysis quota exhausted")

        # images = [
        #     Image.open(BytesIO(await file.read())).resize((512, 512))
        #     for file in [front, top, side, back]
        # ]
        # grid = Image.new('RGB', (1024, 1024))
        # grid.paste(images[0], (0, 0))
        # grid.paste(images[1], (512, 0))
        # grid.paste(images[2], (0, 512))
        # grid.paste(images[3], (512, 512))
        # buffered = BytesIO()
        # grid.save(buffered, format="JPEG", quality=75)
        # b64_image = base64.b64encode(buffered.getvalue()).decode()

        # client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # try:
        #     response = await client.chat.completions.create(
        #         model="gpt-4-vision-preview",
        #         messages=[
        #             {
        #                 "role": "user",
        #                 "content": [
        #                     {"type": "text", "text": (
        #                         "Provide a strictly JSON response without any extra commentary. "
        #                         "The response must be exactly in the following format, without mentioning treatment or surgery:\n"
        #                         "{\"stade\": \"<Norwood stage number>\", "
        #                         "\"price_range\": \"<pricing based on configuration>\", "
        #                         "\"details\": \"<detailed analysis description>\", "
        #                         "\"evaluation\": \"<precise evaluation on the Norwood scale>\"}"
        #                     )},
        #                     {
        #                         "type": "image_url",
        #                         "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
        #                     }

        #                 ]
        #             }
        #         ],
        #         max_tokens=300
        #     )
        #     raw_response = response.choices[0].message.content
        #     print("DEBUG: OpenAI Response =", raw_response) # pour le debug
        #     match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        #     if not match:
        #         raise HTTPException(status_code=500, detail="Invalid response from OpenAI: No JSON found.")
        #     json_str = match.group(0)
        #     print("DEBUG: Extracted JSON =", json_str)

        #     try:
        #         json_result = AnalysisResult.parse_raw(json_str)
        #         json_result = json_result.dict()
        #     except Exception as e:
        #         raise HTTPException(status_code=500, detail=f"Invalid response from OpenAI: {e}")

        #     if clinic_config and "pricing" in clinic_config:
        #         pricing = clinic_config["pricing"]
        #         stade = json_result.get("stade", "").strip()
        #         if stade and stade in pricing:
        #             json_result["price_range"] = f"{pricing[stade]}€"

        #     new_quota = quota - 1
        #     update_clinic_quota(db, api_key, new_quota)
        #     with db:
        #         save_analysis(db, api_key, client_email, json_result)

        #     if (clinic_config and clinic_config.get("email_clinique")):
        #         background_tasks.add_task(
        #             send_email_task,
        #             clinic_config["email_clinique"],
        #             "New Analysis Result",
        #             f"Here is the analysis result for a client ({client_email}):\n\n{json.dumps(json_result, indent=2)}"
        #         )

        #     background_tasks.add_task(
        #         send_email_task,
        #         client_email,
        #         "Your Analysis Result",
        #         f"Hello,\n\nHere is your analysis result:\n\n{json.dumps(json_result, indent=2)}\n\nThank you for your trust."
        #     )
        #     return json_result

        # except Exception as e:
        #     print("DEBUG: Exception =", e)
        #     raise HTTPException(status_code=500, detail=str(e))

    # finally:  # Ferme la connexion dans tous les cas
    #     db.close()
    print("DEBUG: /analyze called")
    print("DEBUG: api_key =", api_key)
    print("DEBUG: client_email =", client_email)
    print("DEBUG: consent =", consent)
    return {"status": "success", "message": "Analyze endpoint reached (simplified)."} #Retourne un message de succès


@app.get("/")
def health_check():
    return {"status": "online"}

@app.post("/update-config")
async def update_config(config_data: ClinicConfigUpdate = Body(...)): # , db: sqlite3.Connection = Depends(get_db)  <-- On retire la dépendance
    print("DEBUG: /update-config called")
    try:
        print("DEBUG: config_data:", config_data.dict()) #On affiche les data reçues
    except Exception as e:
        print(f"DEBUG: Erreur Pydantic: {e}")

    # existing_config = get_clinic_config(db, config_data.api_key) #On commente tout le reste
    # print("DEBUG: existing_config:", existing_config)

    # try:
    #     if existing_config:
    #         print("DEBUG: Updating existing config")
    #         with db: # with pour transaction
    #             db.execute(
    #                 "UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?",
    #                 (config_data.email, json.dumps(config_data.pricing), config_data.api_key)
    #             )
    #     else:
    #         print("DEBUG: Creating new config")
    #         with db: # with pour transaction
    #             db.execute(
    #                 "INSERT INTO clinics (api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start) VALUES (?, ?, ?, ?, ?, ?)",
    #                 (config_data.api_key, config_data.email, json.dumps(config_data.pricing), 0, 0, None)
    #             )
    #     return {"status": "success"}

    # except Exception as e:
    #     print("DEBUG: Exception in update_config:", e)
    #     raise HTTPException(status_code=500, detail="Error updating/creating configuration: " + str(e))
    # finally:
    #     db.close()
    return {"status": "success", "message": "Update config endpoint reached (simplified)."} #On simplifie la réponse


# from admin import router as admin_router  # type: ignore
# app.include_router(admin_router, prefix="/admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
