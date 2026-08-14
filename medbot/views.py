# views.py
# ------------------------------------------------------
# FINAL STABLE VERSION — MULTILINGUAL + HOSPITAL + SESSION MEMORY
# ------------------------------------------------------

import os
import json
import requests
import math

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

# -----------------------
# Session memory config
# -----------------------
# Number of individual messages to keep in session history.
# Each message is either role=user or role=assistant. Tweak as needed.
MAX_HISTORY_TURNS = 20

# ===========================
# OLLAMA CONFIG
# ===========================
OLLAMA_URL = getattr(settings, "OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = getattr(settings, "DEFAULT_MODEL", "llama3.2:3b")

# ===========================
# BLOCK MEDICINE WORDS
# ===========================
MEDICATION_KEYWORDS = [
    "tablet","capsule","mg","dose","medicine","drug",
    "paracetamol","aspirin","ibuprofen","insulin",
    "दवा","ಔಷಧಿ","ଔଷଧ"
]

def healthcheck(request):
    return HttpResponse("OK")

def chat_page(request):
    return render(request, "medbot/chat.html")

# ===========================
# OLLAMA CALL
# ===========================
def call_ollama(messages, model=DEFAULT_MODEL, timeout=40):
    url = OLLAMA_URL.rstrip("/") + "/api/chat"

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": 4096,
            "temperature": 0.4,
            "top_p": 0.9,
            "num_predict": 300
        }
    }

    r = requests.post(url, json=payload, timeout=timeout)
    if not r.ok:
        raise RuntimeError(r.text)

    data = r.json()
    if "message" in data:
        return data["message"].get("content", "").strip()
    return data.get("response", "").strip()

# ===========================
# SYSTEM PROMPT
# ===========================
def build_system_prompt(lang):
    LANG_NAME = {
        "en": "English",
        "hi": "Hindi",
        "kn": "Kannada",
        "od": "Odia"
    }.get(lang, "English")

    return (
        f"You are a medical awareness assistant. "
        f"Always reply ONLY in {LANG_NAME}. "
        f"Never switch languages.\n\n"
        "- Do NOT prescribe medicines or doses.\n"
        "- Explain symptoms, prevention, first aid.\n"
        "- If user requests hospitals, DO NOT answer. Server will handle hospitals.\n"
        "- Keep responses short."
    )

# ===========================
# BLOCK MEDICINE REQUEST
# ===========================
def contains_medication_request(text):
    t = (text or "").lower()
    return any(k in t for k in MEDICATION_KEYWORDS)

# ===========================
# TRANSLATE USER → ENGLISH
# ===========================
def translate_to_english(text):
    try:
        return call_ollama([
            {"role": "system", "content": "Translate the following into natural English."},
            {"role": "user", "content": text},
        ], timeout=12)
    except:
        # fallback to original text if translation fails
        return text or ""

# ===========================
# TRIAGE LOGIC
# ===========================
SEVERE = [
    "accident","fracture","broken","bleeding",
    "chest pain","difficulty breathing","unconscious",
    "stroke","suicidal"
]

MODERATE = ["fever","infection","pain","swelling"]

def classify_severity(text):
    t = (text or "").lower()
    if any(k in t for k in SEVERE):
        return "severe"
    if any(k in t for k in MODERATE):
        return "moderate"
    return "mild"

# ===========================
# HOSPITAL SEARCH (OSM) — robust lat/lon extraction
# ===========================
def haversine(a, b, c, d):
    R = 6371
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a)
    dl = math.radians(d - b)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1-h))

def _get_element_coords(el):
    """
    Extract lat/lon robustly for node/way/relation results from Overpass.
    Node: 'lat' and 'lon' keys
    Way/relation: 'center' object with 'lat' and 'lon'
    Returns (lat, lon) as floats or (None, None)
    """
    if not isinstance(el, dict):
        return None, None

    # node case
    lat = el.get("lat")
    lon = el.get("lon")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except:
            return None, None

    # center case (ways/relations)
    center = el.get("center")
    if isinstance(center, dict):
        latc = center.get("lat")
        lonc = center.get("lon")
        if latc is not None and lonc is not None:
            try:
                return float(latc), float(lonc)
            except:
                return None, None

    return None, None

def search_hospitals_osm(lat, lon, radius=4000):
    """
    Query Overpass and return list of hospitals with robust coordinate handling.
    Each hospital entry contains:
      - name
      - distance_km
      - lat, lon
      - price_estimate
      - eta_minutes
      - google_maps (link to open in Google Maps)
    """
    # basic validation of lat/lon
    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return []

    query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="hospital"](around:{radius},{lat},{lon});
      node["amenity"="clinic"](around:{radius},{lat},{lon});
      node["healthcare"="hospital"](around:{radius},{lat},{lon});
      way["amenity"="hospital"](around:{radius},{lat},{lon});
      relation["amenity"="hospital"](around:{radius},{lat},{lon});
    );
    out center;
    """

    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        # On error (rate limit or network), return empty list gracefully
        return []

    hospitals = []

    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("official_name") or "Unnamed Hospital"

        hlat, hlon = _get_element_coords(el)
        if hlat is None or hlon is None:
            # skip elements without coords
            continue

        dist = round(haversine(lat, lon, hlat, hlon), 1)

        # price estimate heuristic
        op = (tags.get("operator") or "").lower()
        amen = tags.get("amenity") or ""
        if op.startswith("gov") or "municipal" in op or "government" in op:
            price = "₹0–₹300 (Govt Hospital)"
        elif amen == "clinic":
            price = "₹300–₹800 (Clinic)"
        else:
            price = "₹800–₹2000 (Private Hospital)"

        # build google maps link (openable from frontend)
        google_maps = f"https://www.google.com/maps/search/?api=1&query={hlat},{hlon}"

        hospitals.append({
            "name": name,
            "distance_km": dist,
            "lat": hlat,
            "lon": hlon,
            "price_estimate": price,
            "eta_minutes": max(5, int(dist * 2)),
            "google_maps": google_maps
        })

    # sort by distance
    hospitals.sort(key=lambda x: x["distance_km"])
    return hospitals

# ===========================
# FIXED HOSPITAL INTENT DETECTION — check original + translated text
# ===========================
def is_hospital_intent_any(text):
    # Normalize
    t = (text or "").lower()

    hosp_words = [
        # English
        "hospital", "hospitals", "clinic", "clinics",
        "near me", "nearby", "nearest", "show hospital", "show hospitals", "find hospital",
        "find hospitals", "nearest hospital",
        # Hindi (common forms)
        "अस्पताल", "हॉस्पिटल",
        # Kannada
        "ಅಸ್ಪತ್ರೆ", "ಆಸ್ಪತ್ರೆ", "ಅಸ್ಪತ್ರೆಗಳು",
        # Odia
        "ହସ୍ପିଟାଲ", "ଅସ୍ପତାଳ", "ହସ୍ପିଟାଲ୍",
    ]

    return any(w in t for w in hosp_words)

# =====================================================
# MAIN CHAT API (with session memory)
# =====================================================
@csrf_exempt
def chat_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    msg = (data.get("message") or "").strip()
    lang = data.get("language", "en")
    lat = data.get("lat")
    lon = data.get("lon")

    if not msg:
        return JsonResponse({"error": "Empty message"}, status=400)

    # 1. Medication request -> block immediately
    if contains_medication_request(msg):
        return JsonResponse({
            "reply": {
                "en": "I cannot prescribe medicines. Please consult a doctor.",
                "hi": "मैं दवाइयाँ नहीं लिख सकता। कृपया डॉक्टर से मिलें।",
                "kn": "ನಾನು ಔಷಧಿಗಳನ್ನು ಸೂಚಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ. ದಯವಿಟ್ಟು ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
                "od": "ମୁଁ ଔଷଧ ଦେଇପାରିବି ନାହିଁ। ଡାକ୍ତରଙ୍କୁ ପରାମର୍ଶ କରନ୍ତୁ।"
            }.get(lang, "I cannot prescribe medicines."),
            "severity": "mild",
            "hospitals": [],
            "showAmbulanceButton": False
        })

    # 2. Translate for intent detection (safe fallback if translation fails)
    msg_en = translate_to_english(msg)

    # 3. Severity detection (using english translation for keywords)
    severity = classify_severity(msg_en)

    # 4. Hospital detection: check both original language and translated text
    wants_hospitals = is_hospital_intent_any(msg) or is_hospital_intent_any(msg_en)

    # If user asked for hospitals and geolocation available -> return hospitals (do not call AI)
    if wants_hospitals and lat is not None and lon is not None:
        hospitals = search_hospitals_osm(lat, lon)
        return JsonResponse({
            "reply": {
                "en": "Here are nearby hospitals:",
                "hi": "यहाँ नज़दीकी अस्पताल हैं:",
                "kn": "ಇಲ್ಲಿ ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆಗಳು:",
                "od": "ନିକଟସ୍ଥ ହସ୍ପିଟାଲଗୁଡ଼ିକ:"
            }.get(lang, "Here are nearby hospitals:"),
            "severity": severity,
            "hospitals": hospitals,
            "showAmbulanceButton": True
        })

    # ---------------------------
    # 5. Normal AI reply (with session memory)
    # ---------------------------
    # Load conversation history from session (list of {"role":"user"/"assistant","content":...})
    hist_key = "conv_history"
    history = request.session.get(hist_key, [])
    if not isinstance(history, list):
        history = []

    # Build messages for model: system prompt + recent history + current user message
    # Keep history size bounded (we will trim after assistant reply)
    system_prompt = build_system_prompt(lang)
    messages = [{"role": "system", "content": system_prompt}]

    # Include recent history (most recent MAX_HISTORY_TURNS messages)
    if history:
        recent = history[-MAX_HISTORY_TURNS:]
        # ensure each entry has role/content
        for h in recent:
            if isinstance(h, dict) and "role" in h and "content" in h:
                messages.append({"role": h["role"], "content": h["content"]})

    # Add the current user message
    messages.append({"role": "user", "content": msg})

    try:
        ai_reply = call_ollama(messages)
    except Exception as e:
        ai_reply = f"Local AI unavailable: {e}"

    # Append both user (already stored?) and assistant to session history:
    # We store the user message (we might already have appended earlier in other flows; ensure no duplication)
    # For simplicity we append the user + assistant now.
    history.append({"role": "user", "content": msg})
    history.append({"role": "assistant", "content": ai_reply})

    # Trim history and persist
    history = history[-MAX_HISTORY_TURNS:]
    request.session[hist_key] = history
    request.session.modified = True

    # 6. Append severe warning (conservative)
    if severity == "severe":
        ai_reply += "\n\n" + {
            "en": "If symptoms are severe, seek emergency care immediately.",
            "hi": "लक्षण गंभीर हों तो तुरंत आपातकालीन उपचार लें।",
            "kn": "ಲક્ષણಗಳು ಗಂಭೀರವಾಗಿದ್ದರೆ ತಕ್ಷಣ ತುರ್ತು ಚಿಕಿತ್ಸೆಯನ್ನು ಪಡೆಯಿರಿ.",
            "od": "ଲକ୍ଷଣ ଗମ୍ଭୀର ହେଲେ ତୁରନ୍ତ ଆପାତକାଳୀନ ସେବାକୁ ଯାଆନ୍ତୁ।"
        }.get(lang, "")

    return JsonResponse({
        "reply": ai_reply,
        "severity": severity,
        "hospitals": [],
        "showAmbulanceButton": (severity == "severe")
    })

# Optional: clear session conversation (frontend can POST to this to start a new chat)
@csrf_exempt
def clear_history(request):
    try:
        request.session.pop("conv_history", None)
        request.session.modified = True
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=500)
