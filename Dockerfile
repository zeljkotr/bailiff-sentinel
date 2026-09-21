# Bazni image - sluzbeni Python, "slim" varijanta je manja (manje nepotrebnih paketa)
FROM python:3.12-slim

# Radni direktorijum unutar kontejnera - sve sto radimo dalje je relativno na ovo
WORKDIR /app

# BITNO za layer caching: prvo kopiramo SAMO requirements.txt, ne ceo kod.
# Docker cuva ovaj sloj u kesu - ako menjas kod ali ne i requirements.txt,
# sledeci build preskace ponovnu instalaciju paketa (stedi minute pri svakom build-u).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Sada kopiramo ostatak koda - ovo se menja cesto, pa ide POSLE instalacije paketa
COPY . .

# Kreiramo instance/ direktorijum za SQLite bazu (app.py to inace radi sam,
# ali eksplicitno ovde da izbegnemo permission probleme)
RUN mkdir -p /app/instance

# Dokumentuje koji port app koristi (informativno, ne otvara port sam po sebi -
# to se radi u docker-compose.yml ili docker run -p)
EXPOSE 5000

# Komanda koja se izvrsava kad se kontejner pokrene
CMD ["python", "app.py"]
