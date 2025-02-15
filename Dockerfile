FROM python:3.9-slim

WORKDIR /app

# Installer les dépendances système pour Pillow
RUN apt-get update && apt-get install -y libjpeg-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "${PORT}"]
