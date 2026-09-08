# Intelligent Network Assistant

Natural-language control for the Docker lab network (`block` / `unblock` / `limit bandwidth` / `status`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# optional: put OPENROUTER_API_KEY in a .env file
./network/setup.sh
python -m assistant.client
```

See `docs/INA_SETUP.md` for the full setup and troubleshooting guide.
