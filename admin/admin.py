from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import json
import psycopg2
from psycopg2.extras import DictCursor

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL non définie")
    try:
        conn = psycopg2.connect(database_url)
        print("DEBUG: Admin connected using DATABASE_URL")
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {e}")

async def get_db():
    db = get_db_connection()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_class=HTMLResponse, name="admin_dashboard")
async def admin_dashboard(request: Request, db=Depends(get_db)):
    try:
        cursor = db.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics")
        clinics = cursor.fetchall()
        clinic_list = []
        for clinic in clinics:
            pricing = json.loads(clinic['pricing']) if clinic['pricing'] else {}
            clinic_list.append({
                "api_key": clinic['api_key'],
                "email_clinique": clinic['email_clinique'],
                "pricing": pricing,
                "analysis_quota": clinic['analysis_quota'],
                "default_quota": clinic['default_quota'],
                "subscription_start": clinic['subscription_start']
            })
        return templates.TemplateResponse("dashboard.html", {"request": request, "clinics": clinic_list})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur dans le dashboard admin</h1><p>{str(e)}</p>", status_code=500)

@router.get("/edit/{api_key}", response_class=HTMLResponse)
async def edit_clinic(request: Request, api_key: str, db=Depends(get_db)):
    try:
        cursor = db.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics WHERE api_key = %s", (api_key,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clinique non trouvée")
        try:
            pricing_dict = json.loads(row['pricing']) if row['pricing'] else {}
        except Exception:
            pricing_dict = {}
        return templates.TemplateResponse("edit_clinic.html", {"request": request, "clinic": {
            "api_key": row['api_key'],
            "email_clinique": row['email_clinique'],
            "pricing": pricing_dict,
            "analysis_quota": row['analysis_quota'],
            "default_quota": row['default_quota'],
            "subscription_start": row['subscription_start']
        }})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur lors de l'édition</h1><p>{str(e)}</p>", status_code=500)

@router.post("/edit/{api_key}")
async def update_clinic(api_key: str, request: Request, db=Depends(get_db)):
    try:
        form_data = await request.form()
        email_clinique = form_data.get("email_clinique")
        pricing_json = form_data.get("pricing_json")
        try:
            json.loads(pricing_json)
        except Exception as e:
            raise HTTPException(status_code=400, detail="Le champ Pricing doit être un JSON valide.")
        try:
            cursor = db.cursor()
            with db:
                cursor.execute("UPDATE clinics SET email_clinique = %s, pricing = %s WHERE api_key = %s", (email_clinique, pricing_json, api_key))
            url = request.url_for("admin_dashboard")
            return RedirectResponse(url=url, status_code=303)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur lors de la mise à jour : {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"DEBUG: Exception in update_config: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/analyses", response_class=HTMLResponse)
async def list_analyses(request: Request, db=Depends(get_db)):
    try:
        cursor = db.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT id, clinic_api_key, client_email, result, timestamp FROM analyses ORDER BY timestamp DESC")
        analyses = cursor.fetchall()
        analysis_list = []
        for a in analyses:
            try:
                result_dict = json.loads(a['result'])
            except Exception:
                result_dict = a['result']
            analysis_list.append({
                "id": a['id'],
                "clinic_api_key": a['clinic_api_key'],
                "client_email": a['client_email'],
                "result": result_dict,
                "timestamp": a['timestamp']
            })
        return templates.TemplateResponse("analyses.html", {"request": request, "analyses": analysis_list})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur lors du chargement des analyses</h1><p>{str(e)}</p>", status_code=500)
