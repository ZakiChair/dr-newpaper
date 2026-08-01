#!/usr/bin/env python3
"""
Script de restart propre du bot Dr_NewPaper.
Résout le Conflict: terminated by other getUpdates request
en forçant Telegram à fermer les connexions zombies.
"""
import os, time, json, urllib.request, urllib.error
from pathlib import Path

import config  # noqa: F401 — importing it reads .env, which the token below needs

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
BASE_URL = f'https://api.telegram.org/bot{BOT_TOKEN}'

# The bot id, not a slice of the token: this script's output is exactly what an
# operator pastes into an issue when a restart misbehaves, and [:20] carried the
# id plus the first characters of the secret.
print(f"[1/4] Bot id: {BOT_TOKEN.split(':')[0]}")

# Step 1: delete webhook (assure clean state)
print("[2/4] Suppression webhook...")
req = urllib.request.Request(f'{BASE_URL}/deleteWebhook')
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"  -> {json.loads(r.read())}")
except Exception as e:
    print(f"  -> Erreur: {e}")

time.sleep(3)

# Step 2: getUpdates avec un offset négatif pour fermer les connexions zombies
print("[3/4] Fermeture connexions zombies via getUpdates(offset=-1)...")
for i in range(3):
    try:
        # Utiliser un timeout court pour forcer Telegram à répondre
        req = urllib.request.Request(
            f'{BASE_URL}/getUpdates?offset=-1&timeout=1',
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            result = json.loads(r.read())
            print(f"  -> Tentative {i+1}: {result}")
    except urllib.error.HTTPError as e:
        print(f"  -> Tentative {i+1}: HTTP {e.code} - {e.reason}")
        if e.code == 409:
            print("     Conflit persistant, Telegram pense toujous qu'une connexion est active.")
            print("     On continue quand même...")
    except Exception as e:
        print(f"  -> Tentative {i+1}: {type(e).__name__}: {e}")
    time.sleep(1)

# Step 3: verifier et wait pour le gap de securite
print("[4/4] Attente 60s pour expiration du long poll chez Telegram...")
print("  (Les connexions long-polling Telegram timeout en ~50s)")
time.sleep(60)

# Step 4: check final state
print("\n[CHECK] État final du bot:")
try:
    req = urllib.request.Request(f'{BASE_URL}/getWebhookInfo')
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"  Webhook: {json.loads(r.read())}")

    req = urllib.request.Request(f'{BASE_URL}/getMe')
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"  Bot: {json.loads(r.read())['result']['username']}")
except Exception as e:
    print(f"  -> Erreur: {e}")

print("\nMaintenant tu peux démarrer le bot avec:")
print(f"cd {Path(__file__).resolve().parent} && source .env && python3 bot.py")
