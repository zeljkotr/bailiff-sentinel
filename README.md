# paycheck-sentinel

*Developed by Zeljko Tripcevski*

Flask + SQLite aplikacija koja analizira XML izvode i otkriva sumnjive
transakcije. Radi na tvom Ubuntu serveru, pristupaš joj preko browsera sa
bilo kog uređaja u mreži (ili preko VPN-a spolja) — nema slanja podataka
na internet, sve ostaje na tvom serveru.

- **Pun povrat** — dužnik platio na pogrešan račun, banka vratila ceo iznos
- **Delimičan povrat** — banka vratila deo iznosa
- **Duplikat broja naloga** — isti ID se pojavljuje više puta
- **Duplikat uplate** — isti dužnik + isti iznos + isti datum ponovljeni
- **Neuobičajen iznos (outlier)** — iznos mnogo veći od medijane svih uplata

Sve se čuva u SQLite bazi (`instance/paycheck_sentinel.db`) — istorija svih
upload-a i analiza ostaje sačuvana između pokretanja.

## Instalacija na serveru (Ubuntu, npr. 192.168.2.39)

**1. Prebaci projekat na server** (scp, git clone, ili kako već radiš):
```bash
scp -r paycheck-sentinel-flask zeljko@192.168.2.39:~/
```

**2. Uđi u folder i napravi venv:**
```bash
cd ~/paycheck-sentinel-flask
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. Pokreni test:**
```bash
python app.py
```
Trebalo bi da ispiše `Running on http://0.0.0.0:5000`. Testiraj sa drugog uređaja u mreži: `http://192.168.2.39:5000`

Prekini test sa Ctrl+C kad potvrdiš da radi, pa idi na trajno pokretanje ispod.

## Trajno pokretanje — opcija A: tmux (jednostavnije, kao MeshCore projekat)

```bash
tmux new -s paycheck-sentinel
cd ~/paycheck-sentinel-flask
source venv/bin/activate
python app.py
```
Detach sa `Ctrl+B` pa `D`. Aplikacija ostaje da radi u pozadini i posle
zatvaranja SSH sesije. Za povratak: `tmux attach -t paycheck-sentinel`.

**Nedostatak:** ako se server restartuje, moraš ručno ponovo da pokreneš tmux sesiju.

## Trajno pokretanje — opcija B: systemd (automatski restart posle reboot-a)

**1. Prilagodi `paycheck-sentinel.service`** — otvori fajl i izmeni `User` i
`WorkingDirectory` na tvoje stvarne vrednosti (npr. korisničko ime na serveru
i putanju gde si prebacio projekat).

**2. Instaliraj servis:**
```bash
sudo cp paycheck-sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable paycheck-sentinel
sudo systemctl start paycheck-sentinel
```

**3. Proveri status:**
```bash
sudo systemctl status paycheck-sentinel
```

**4. Logovi:**
```bash
journalctl -u paycheck-sentinel -f
```

Ovo automatski pokreće aplikaciju pri svakom boot-u servera i restartuje je
ako padne (`Restart=on-failure`).

## Pristup

- Iz lokalne mreže: `http://192.168.2.39:5000` (zameni IP-jem tvog servera)
- Spolja: preko VPN-a na tvoju mrežu, pa isti URL

## Bezbednosna napomena

Aplikacija nema login/autentifikaciju — bilo ko ko ima pristup mreži (ili
VPN-u) može da je otvori. Pošto je pristup ograničen na tvoju privatnu mrežu
+ VPN, to je razumna granica za privatnu upotrebu. Ako želiš dodatni sloj
zaštite (npr. da neko na istoj mreži slučajno ne otvori tvoje finansijske
podatke), javi — može se dodati jednostavan HTTP basic auth ili login sistem.

## Struktura projekta

```
paycheck-sentinel-flask/
  app.py                       - Flask rute (upload, analiza, export, istorija)
  paycheck_sentinel/
    xmlparse.py                - auto-detekcija redova/kolona u XML-u
    checks.py                  - logika 5 provera
    db.py                      - SQLite šema i pristup bazi
  templates/index.html         - glavna stranica
  static/style.css             - dark ops-console tema
  static/app.js                - frontend logika (fetch pozivi ka API-ju)
  instance/                    - SQLite baza (ne commit-uje se, pravi se automatski)
  paycheck-sentinel.service    - systemd unit fajl za automatsko pokretanje
  requirements.txt
```

## Napomena o podacima

XML fajlovi sa realnim finansijskim podacima se **ne commit-uju** u repo
(videti `.gitignore`). Baza sa realnim podacima (`instance/*.db`) takođe
ostaje samo lokalno na serveru.

---
Developed by Zeljko Tripcevski


## Quick start (Docker)

Requirements: Docker with the Compose plugin.

```bash
git clone https://github.com/zeljkotr/bailiff-sentinel.git
cd bailiff-sentinel

cp .env.example .env
# edit .env: set DB_PASSWORD and FLASK_SECRET_KEY (openssl rand -hex 32)

docker compose up -d --build
```

Open http://localhost:5000. The database schema is created automatically on first start.

To stop: `docker compose down` (data is kept in the `pgdata` volume).
To wipe all data: `docker compose down -v`.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | yes | - | Secret used to sign session cookies |
| `DB_PASSWORD` | yes | - | PostgreSQL password |
| `DB_HOST` | no | `localhost` | PostgreSQL host (`db` in Compose) |
| `DB_PORT` | no | `5432` | PostgreSQL port |
| `DB_NAME` | no | `bailiff` | Database name |
| `DB_USER` | no | `bailiff` | Database user |
| `REDIS_HOST` | no | `localhost` | Redis host (`redis` in Compose) |
| `REDIS_PORT` | no | `6379` | Redis port |
| `APP_PORT` | no | `5000` | Host port used by Compose |

The app fails at startup if a required variable is missing.
