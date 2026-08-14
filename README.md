# HealthAware — Local Ollama AI Chatbot (Django)

This project is a self-contained Django application that connects to a local Ollama model
(running on `http://localhost:11434`) to provide an AI-powered, multilingual medical-awareness chatbot.
The app **never prescribes medicines** and includes a triage step to flag severe cases and show nearby hospitals.

## Quick start

1. Install Python 3.10+ and create a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Install Ollama (https://ollama.com/download) and start it:
   ```
   # follow instructions at https://ollama.com/download
   ollama pull llama3.1
   ollama serve
   ```
   By default Ollama listens at http://localhost:11434

3. Copy `.env.example` to `.env` and adjust if needed:
   ```
   cp .env.example .env        # Windows: copy .env.example .env
   ```

4. Run Django:
   ```
   python manage.py migrate
   python manage.py runserver
   ```

5. Open http://127.0.0.1:8000 in your browser.

## Notes
- If Ollama is not running or the model is not available, the app will show an explanatory message.
- To change model, edit `DEFAULT_MODEL` in `.env`.
- Nearby hospitals are fetched live from the OpenStreetMap Overpass API, based on the
  browser's geolocation. See `search_hospitals_osm` in `medbot/views.py` to adjust the
  search radius or result handling.
- Chat history is stored per-session (up to 20 turns) for conversational context.
- A `/healthcheck/` endpoint returns `OK` and is handy for uptime monitoring.

## License
MIT
