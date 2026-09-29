# paycheck-sentinel (bailiff-sentinel)

A self-hosted Flask web application that analyzes XML bank statements and flags suspicious transactions. Everything runs on your own server: no data is sent to the internet, and it is accessible from any device on your network (or through a VPN from outside).

Data is stored in PostgreSQL and web sessions in Redis, so multiple app instances can run behind a load balancer and serve any request.

## What it detects

- **Full refund**: a debtor paid to the wrong account and the bank returned the entire amount
- **Partial refund**: the bank returned only part of the amount
- **Duplicate order ID**: the same ID appears more than once
- **Duplicate payment**: the same debtor, amount and date repeated
- **Outlier amount**: an amount far above the median of all payments

The full history of uploads and analyses is kept in the database between restarts.

## Quick start (Docker)

Requirements: Docker with the Compose plugin.

```bash
git clone https://github.com/zeljkotr/bailiff-sentinel.git
cd bailiff-sentinel

cp .env.example .env
# edit .env: set DB_PASSWORD and FLASK_SECRET_KEY (generate with: openssl rand -hex 32)

docker compose up -d --build
```

Open http://localhost:5000. The database schema is created automatically on first start.

- Stop: `docker compose down` (data is kept in the `pgdata` volume)
- Wipe all data: `docker compose down -v`
- Logs: `docker compose logs -f app`

Compose starts three services: the app (Gunicorn), PostgreSQL and Redis. PostgreSQL and Redis are not published to the host; only the app port is.

## Configuration

All configuration is done through environment variables (in Compose and in systemd, through the `.env` file).

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

The app refuses to start if a required variable is missing. Never commit your `.env` file.

## Permanent installation (systemd)

Requirements: Ubuntu server with Python 3.12+, PostgreSQL and Redis.

**1. Install PostgreSQL and Redis**

```bash
sudo apt update
sudo apt install -y postgresql redis-server python3-venv

sudo -u postgres psql -c "CREATE USER bailiff WITH PASSWORD 'choose-a-strong-password';"
sudo -u postgres psql -c "CREATE DATABASE bailiff OWNER bailiff;"
```

**2. Get the project and install dependencies**

```bash
git clone https://github.com/zeljkotr/bailiff-sentinel.git
cd bailiff-sentinel

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

**3. Configure**

```bash
cp .env.example .env
chmod 600 .env
# edit .env: set FLASK_SECRET_KEY (openssl rand -hex 32) and DB_PASSWORD
# (the same password you used in the CREATE USER command above)
```

**4. Install the service**

Open `paycheck-sentinel.service` and change `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` and the path in `ExecStart` to match your server (the username and the folder where you cloned the project). Then:

```bash
sudo cp paycheck-sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now paycheck-sentinel
```

**5. Check that it works**

```bash
sudo systemctl status paycheck-sentinel
journalctl -u paycheck-sentinel -f
curl -sI http://localhost:5000/ | head -3
```

The service starts automatically on every boot and restarts if it crashes (`Restart=on-failure`). To apply an update:

```bash
cd ~/bailiff-sentinel
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart paycheck-sentinel
```

## Access

- The service listens on `127.0.0.1:5000` only. To reach it from your network, put a reverse proxy (for example Nginx) in front of it, or change `--bind` in the unit file to `0.0.0.0:5000` if you trust the network.
- From outside: through a VPN into your network, then via the reverse proxy.

## Project structure

```
bailiff-sentinel/
  app.py                     - Flask routes (upload, analysis, export, history)
  paycheck_sentinel/
    xmlparse.py              - automatic row/column detection in XML files
    checks.py                - the detection logic
    db.py                    - PostgreSQL schema and data access
  templates/index.html       - main page
  static/style.css           - dark ops-console theme
  static/app.js              - frontend logic (fetch calls to the API)
  Dockerfile
  docker-compose.yml         - app + PostgreSQL + Redis
  .env.example               - template for the required configuration
  paycheck-sentinel.service  - systemd unit file
  requirements.txt
```

## Security notes

- The application has **no login or authentication**. Anyone who can reach the port can open it. Run it only on a private network or behind a VPN, and do not expose it directly to the internet. If you need an extra layer, put a reverse proxy with HTTP basic auth in front of it.
- Use a strong random `FLASK_SECRET_KEY` and `DB_PASSWORD`, and keep `.env` readable only by the service user (`chmod 600 .env`).
- Real XML statements and databases contain financial data and must never be committed to the repository (see `.gitignore`).

---

Developed by Zeljko Tripcevski
