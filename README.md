# CLK — Lightning Countdown Clock Controller

Bridges Perry Weather lightning alerts to an RS-485-controlled LED countdown clock,
with a web UI for configuration.

## Architecture

```
Perry Weather  ──(webhook)──>  Flask app  ──(queue)──>  RS-485 worker  ──>  Clock
                                   │
                                   └──>  SQLite (config, event log)
                                   └──>  Web UI (config, dashboard)
```

## Hardware

- Raspberry Pi Zero 2W running Pi OS Lite 64-bit
- USB-to-RS485 adapter (typically appears as `/dev/ttyUSB0`)
- LED clock supporting the custom RS-485 protocol (9600 8N1)

## Project layout

```
clk-app/
├── app/
│   ├── __init__.py          Flask app factory
│   ├── config.py            App configuration
│   ├── db.py                SQLite helpers + schema
│   ├── auth.py              Login / session handling
│   ├── rs485.py             Protocol implementation (framing, checksums)
│   ├── worker.py            Background thread: queue → RS-485 commands
│   ├── perry.py             Webhook payload validation + event mapping
│   ├── blueprints/
│   │   ├── webhook.py       /webhook/perry endpoint
│   │   ├── api.py           /api/* REST endpoints for the UI
│   │   ├── ui.py            HTML page routes
│   │   └── auth.py          /login, /logout
│   ├── templates/           Jinja2 templates (HTMX-powered)
│   └── static/              CSS, JS
├── scripts/
│   ├── init_db.py           Create/migrate the database
│   ├── set_admin_password.py
│   └── send_test_command.py CLI tool for testing RS-485 from shell
├── systemd/
│   └── clk-app.service      Service file to run on boot
├── requirements.txt
├── wsgi.py                  Gunicorn entrypoint
├── .env.example
└── README.md
```

## Setup (on a fresh Pi OS Lite install)

```bash
# System dependencies
sudo apt update && sudo apt install -y python3-venv python3-pip git

# Add user to dialout so we can access /dev/ttyUSB0 without sudo
sudo usermod -a -G dialout $USER
# Log out and back in for the group change to take effect

# Get the code
git clone <your-repo> clk-app
cd clk-app

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configuration
cp .env.example .env
nano .env   # set SECRET_KEY, PERRY_WEBHOOK_SECRET, etc.

# Database
python scripts/init_db.py
python scripts/set_admin_password.py   # prompts for password

# Run it (dev)
python wsgi.py

# Run it (production via systemd)
sudo cp systemd/clk-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clk-app
sudo systemctl status clk-app
```

## Webhook URL

Once running, Perry Weather should POST to:

- Local network only: `http://<pi-ip>:8080/webhook/perry`
- Via Cloudflare Tunnel: `https://clk.yourdomain.com/webhook/perry`

The endpoint validates the `X-Perry-Signature` header against `PERRY_WEBHOOK_SECRET`
before processing. Requests without a valid signature are rejected.
