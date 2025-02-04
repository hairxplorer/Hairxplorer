from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import json

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

def get_db():
    conn = sqlite3.connect('clinics/config.db')
    try:
        yield conn
    finally:
        conn.close()

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT api_key, email_clinique, pricing FROM clinics")
    clinics = cursor.fetchall()
    clinic_list = []
    for clinic in clinics:
        api_key, email, pricing = clinic
        try:
            pricing = json.loads(pricing) if pricing else {}
        except Exception:
            pricing = {}
        clinic_list.append({
            "api_key": api_key,
            "email_clinique": email,
            "pricing": pricing
        })
    return templates.TemplateResponse("dashboard.html", {"request": request, "clinics": clinic_list})

@router.get("/edit/{api_key}", response_class=HTMLResponse)
async def edit_clinic(request: Request, api_key: str, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT api_key, email_clinique, pricing FROM clinics WHERE api_key = ?", (api_key,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Clinique non trouvée")
    api_key_val, email, pricing = row
    try:
        pricing_dict = json.loads(pricing) if pricing else {}
    except Exception:
        pricing_dict = {}
    return templates.TemplateResponse("edit_clinic.html", {"request": request, "clinic": {"api_key": api_key_val, "email_clinique": email, "pricing": pricing_dict}})

@router.post("/edit/{api_key}")
async def update_clinic(api_key: str, email_clinique: str = Form(...), pricing_json: str = Form(...), db: sqlite3.Connection = Depends(get_db)):
    try:
        json.loads(pricing_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Le champ Pricing doit être un JSON valide.")
    cursor = db.cursor()
    cursor.execute("UPDATE clinics SET email_clinique = ?, pricing = ? WHERE api_key = ?", (email_clinique, pricing_json, api_key))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/analyses", response_class=HTMLResponse)
async def list_analyses(request: Request, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, clinic_api_key, client_email, result, timestamp FROM analyses ORDER BY timestamp DESC")
    analyses = cursor.fetchall()
    analysis_list = []
    for a in analyses:
        id_val, clinic_api_key, client_email, result, timestamp = a
        try:
            result_dict = json.loads(result)
        except Exception:
            result_dict = result
        analysis_list.append({
            "id": id_val,
            "clinic_api_key": clinic_api_key,
            "client_email": client_email,
            "result": result_dict,
            "timestamp": timestamp
        })
    return templates.TemplateResponse("analyses.html", {"request": request, "analyses": analysis_list})
