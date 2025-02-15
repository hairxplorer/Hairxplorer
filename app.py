@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    top: UploadFile = File(...),
    side: UploadFile = File(...),
    back: UploadFile = File(...),
    api_key: str = Form(...),
    client_email: str = Form(...),
    consent: bool = Form(...)
    , db: sqlite3.Connection = Depends(get_db)  # On remet la dépendance à la DB
):
    try:
        if not consent:
            raise HTTPException(status_code=400, detail="You must consent to the use of your data.")

        clinic_config = get_clinic_config(db, api_key)  # On remet l'appel
        if not clinic_config:
            raise HTTPException(status_code=404, detail="Clinic not found")

        reset_quota_if_needed(db, clinic_config, api_key) # On remet l'appel

        quota = clinic_config.get("analysis_quota")
        if quota is None:
            raise HTTPException(status_code=400, detail="Quota is not defined for this clinic")
        if isinstance(quota, int) and quota <= 0:
            raise HTTPException(status_code=403, detail="Analysis quota exhausted")

        # --- On commente TEMPORAIREMENT la partie images et OpenAI ---
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
        #        ...
        #     )
        #     ...
        # except Exception as e:
        #      ...

        # On simule un résultat pour l'instant.
        json_result = {"stade": "VII", "price_range": "1234", "details": "Test", "evaluation": "Test"}


        new_quota = quota - 1  # On décrémente le quota
        update_clinic_quota(db, api_key, new_quota) # On met à jour
        with db: #On utilise with
          save_analysis(db, api_key, client_email, json_result) #On sauvegarde

        # On commente TEMPORAIREMENT la partie email
        # if (clinic_config and clinic_config.get("email_clinique")):
        #     ...

        # background_tasks.add_task(...) # On commente l'envoi d'e-mail

        return {"status": "success", "message": "Analyze endpoint reached (DB logic reintroduced)."} # On renvoie un succès

    except Exception as e:
        print("DEBUG: Exception =", e)  # Gardez le print de debug
        raise HTTPException(status_code=500, detail=str(e))

    finally:  # Ferme la connexion dans tous les cas
        db.close()
