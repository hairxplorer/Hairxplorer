from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import json
import os  # Ajouté pour os.path.join

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

# Utilisez le MÊME chemin que dans app.py
DATABASE_PATH = os.path.join(os.path.dirname(__file__), '..', 'clinics', 'config.db')  # Chemin absolu

def get_db_connection():
    """Crée une nouvelle connexion à la base de données *fichier*, thread-safe."""
    db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)  # IMPORTANT: check_same_thread=False
    db.execute("PRAGMA journal_mode=WAL")  # Amélioration pour la concurrence
    return db

async def get_db(): #Fonction pour FastAPI
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.cursor()
        with db:
            cursor.execute("SELECT api_key, email_clinique, pricing FROM clinics")
            clinics = cursor.fetchall()
        clinic_list = []
        for clinic in clinics:
            api_key, email, pricing = clinic
            try:
                pricing = json.loads(pricing) if pricing else {}
            except Exception as e:
                pricing = {}  # Ou une autre valeur par défaut
            clinic_list.append({
                "api_key": api_key,
                "email_clinique": email,
                "pricing": pricing
            })
        return templates.TemplateResponse("dashboard.html", {"request": request, "clinics": clinic_list})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur dans le dashboard admin</h1><p>{str(e)}</p>", status_code=500)

@router.get("/edit/{api_key}", response_class=HTMLResponse)
async def edit_clinic(request: Request, api_key: str, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.cursor()
        with db:
            cursor.execute("SELECT api_key, email_clinique, pricing FROM clinics WHERE api_key = ?", (api_key,))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clinique non trouvée")
        api_key_val, email, pricing = row
        try:
            pricing_dict = json.loads(pricing) if pricing else {}
        except Exception:
            pricing_dict = {}  # Ou une autre valeur par défaut/gestion d'erreur
        return templates.TemplateResponse("edit_clinic.html", {"request": request, "clinic": {"api_key": api_key_val, "email_clinique": email, "pricing": pricing_dict}})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur lors de l'édition</h1><p>{str(e)}</p>", status_code=500)

@router.post("/edit/{api_key}")
async def update_clinic(api_key: str,  request: Request, db: sqlite3.Connection = Depends(get_db)): #Plus besoin de Form + correction ordre arguments
    try:
        form_data = await request.form() #Récupère les données du formulaire
        email_clinique = form_data.get("email_clinique") #Récupère la valeur de la clé email_clinique
        pricing_json = form_data.get("pricing") #Récupère la valeur de la clé pricing
        try:
            json.loads(pricing_json)
        except Exception as e:
            raise HTTPException(status_code=400, detail="Le champ Pricing doit être un JSON valide.")
        try:
            cursor = db.cursor()
            with db: # with pour transaction
                cursor.execute("UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?", (email_clinique, pricing_json, api_key))
            # Utilisez request.url_for pour générer l'URL de redirection *correctement*
            url = request.url_for("admin_dashboard") #Redirection avec le nom de la fonction
            return RedirectResponse(url=url, status_code=303)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour : {str(e)}")
    except HTTPException:
        raise #Si y'a dejà une erreur on la renvoi
    except Exception as e:
        print(f"DEBUG: Exception in update_config: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/analyses", response_class=HTMLResponse)
async def list_analyses(request: Request, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.cursor()
        with db:
            cursor.execute("SELECT id, clinic_api_key, client_email, result, timestamp FROM analyses ORDER BY timestamp DESC")
            analyses = cursor.fetchall()
        analysis_list = []
        for a in analyses:
            id_val, clinic_api_key, client_email, result, timestamp = a
            try:
                result_dict = json.loads(result)
            except Exception:
                result_dict = result  # Ou une autre valeur par défaut/gestion d'erreur
            analysis_list.append({
                "id": id_val,
                "clinic_api_key": clinic_api_key,
                "client_email": client_email,
                "result": result_dict,
                "timestamp": timestamp
            })
        return templates.TemplateResponse("analyses.html", {"request": request, "analyses": analysis_list})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur lors du chargement des analyses</h1><p>{str(e)}</p>", status_code=500)
