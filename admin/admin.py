from fastapi import APIRouter, Request, Form, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3  # Gardez sqlite3, MAIS...
import json
import os

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")  # Assurez-vous que ce chemin est correct

# Utilisez le MÊME chemin que dans app.py (chemin absolu, et pointant vers le BON fichier)
DATABASE_PATH = os.path.join(os.path.dirname(__file__), '..', 'clinics', 'config.db')

# Utilisez la fonction get_db_connection de app.py !  NE REDÉFINISSEZ PAS LA CONNEXION !
# from app import get_db_connection  # <- MAUVAISE PRATIQUE : dépendance circulaire
#
# async def get_db():
#    db = get_db_connection()
#    try:
#        yield db
#    finally:
#        db.close()

# Utilisez la fonction get_db de app.py, et psycopg2
def get_db_connection():
    """Crée une nouvelle connexion à la base de données PostgreSQL."""
    try:
        # Connexion en utilisant les variables d'environnement de Railway.
        conn = psycopg2.connect(
            host=os.getenv("PGHOST"),
            port=os.getenv("PGPORT", 5432),  # 5432 est le port par défaut
            database=os.getenv("PGDATABASE"),
            user=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD")
        )
        print("DEBUG: Successfully connected to PostgreSQL")  # DEBUG
        return conn  # Retourne la *connexion*, PAS un curseur
    except psycopg2.OperationalError as e:
        print(f"DEBUG: Erreur de connexion à PostgreSQL : {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {e}")
    except KeyError as e: #Ajout gestion erreur KeyError
        print(f"DEBUG: Manque une variable d'environement: {e}")
        raise HTTPException(status_code=500, detail=f"Missing environment variable: {e}")

async def get_db(): #Fonction pour FastAPI
  db = get_db_connection()
  try:
    yield db
  finally:
    db.close()

@router.get("/", response_class=HTMLResponse, name="admin_dashboard")
async def admin_dashboard(request: Request, db:  psycopg2.extensions.connection = Depends(get_db)): # type hinting
    try:
        with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor: #On utilise DictCursor
            cursor.execute("SELECT api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics")
            clinics = cursor.fetchall() #fetchall renvoi une liste de tuple.
        clinic_list = []
        for clinic in clinics: #On boucle sur la liste
            #  row est un dictionnaire grace a DictCursor
            clinic_list.append({
              "api_key": clinic['api_key'],
              "email_clinique": clinic['email_clinique'],
              'pricing': json.loads(clinic['pricing']) if clinic['pricing'] else {}, #On parse le json
              "analysis_quota": clinic['analysis_quota'],
              "default_quota": clinic['default_quota'],
              "subscription_start": clinic['subscription_start']
            })
        return templates.TemplateResponse("dashboard.html", {"request": request, "clinics": clinic_list})
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur dans le dashboard admin</h1><p>{str(e)}</p>", status_code=500)


@router.get("/edit/{api_key}", response_class=HTMLResponse)
async def edit_clinic(request: Request, api_key: str, db:  psycopg2.extensions.connection = Depends(get_db)):# type hinting
    try:
        with db.cursor(cursor_factory=DictCursor) as cursor: #On utilise DictCursor
          cursor.execute("SELECT api_key, email_clinique, pricing, analysis_quota, default_quota, subscription_start FROM clinics WHERE api_key = %s", (api_key,)) # syntaxe psycopg2
          row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clinique non trouvée")
        return templates.TemplateResponse("edit_clinic.html", {"request": request,
        "clinic": {
        "api_key": row['api_key'],
        "email_clinique": row['email_clinique'],
        "pricing": json.loads(row['pricing']) if row['pricing'] else {},
        "analysis_quota": row['analysis_quota'],
        "default_quota": row['default_quota'],
        "subscription_start": row['subscription_start']
        }}) #On passe tout au template
    except Exception as e:
        return HTMLResponse(f"<h1>Erreur lors de l'édition</h1><p>{str(e)}</p>", status_code=500)



@router.post("/edit/{api_key}")
async def update_clinic(api_key: str, request: Request, db:  psycopg2.extensions.connection = Depends(get_db)): # type hinting + Plus besoin de Form
    try:
      form_data = await request.form()
      email_clinique = form_data.get("email_clinique")
      pricing_json = form_data.get("pricing_json")
      try:
        json.loads(pricing_json) #On verifie que c'est bien du json
      except Exception as e:
        raise HTTPException(status_code=400, detail="Le champ Pricing doit être un JSON valide.")

      with db:  # with pour transaction
        with db.cursor() as cursor: #On ouvre un curseur
          cursor.execute("UPDATE clinics SET email_clinique = %s, pricing = %s WHERE api_key = %s", (email_clinique, pricing_json, api_key)) # syntaxe psycopg2

      url = request.url_for("admin_dashboard")  # Utilisez request.url_for
      return RedirectResponse(url=url, status_code=303)

    except HTTPException:
      raise
    except Exception as e:
        print(f"DEBUG: Exception in update_clinic: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")



@router.get("/analyses", response_class=HTMLResponse)
async def list_analyses(request: Request, db:  psycopg2.extensions.connection = Depends(get_db)):# type hinting
    try:
      with db.cursor(cursor_factory=DictCursor) as cursor: #On utilise DictCursor
        cursor.execute("SELECT id, clinic_api_key, client_email, result, timestamp FROM analyses ORDER BY timestamp DESC")
        analyses = cursor.fetchall() #fetchall renvoi une liste de tuple
      analysis_list = []
      for a in analyses: #On boucle sur la liste
        analysis_list.append({
            "id": a['id'],
            "clinic_api_key": a['clinic_api_key'],
            "client_email": a['client_email'],
            "result": json.loads(a['result']),
            "timestamp": a['timestamp']
        })
      return templates.TemplateResponse("analyses.html", {"request": request, "analyses": analysis_list})

    except Exception as e:
      return HTMLResponse(f"<h1>Erreur lors du chargement des analyses</h1><p>{str(e)}</p>", status_code=500)
