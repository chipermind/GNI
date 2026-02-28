#!/usr/bin/env python3
"""
Send one test message to Telegram to verify flow and format.
Uses the same format as the pipeline (GNI — Análise de Inteligência).

Usage:
  # Dry-run (print what would be sent, no send)
  python scripts/send_test_telegram.py

  # Actually send to Telegram
  python scripts/send_test_telegram.py --send

On VM (from host):
  docker compose exec worker python scripts/send_test_telegram.py --send

Requires: TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID (or TELEGRAM_CHAT_ID) in env.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on path
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

# Formato oficial GNI (Desk de Inteligência Estratégica)
TEST_MESSAGE = """🌐 GLOBAL NEWS INTEL (GNI)

🧠 Desk de Inteligência Estratégica

Boa noite, operadores.

[TEST] Verificação de fluxo e formato — GNI Bot.

🔎 Radar Ativo
* Teste de envio
* Canal Telegram
* Formato correto

📊 Leitura GNI
Mensagem de teste. Se você vê isto, o fluxo está OK e o formato está correto.

📡 Atualizações estratégicas a qualquer momento.
Fiquem atentos.

— Equipe GNI"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Send one test message to Telegram (same format as pipeline)")
    ap.add_argument("--send", action="store_true", help="Actually send (default: dry-run)")
    args = ap.parse_args()

    from apps.publisher.gni_sender import gni_send

    dry_run = not args.send
    print("Mode:", "dry-run (no send)" if dry_run else "SEND to Telegram")
    print("Message: GNI Analise de Inteligencia format (test)")
    print()

    result = gni_send(TEST_MESSAGE, meta={"source": "send_test_telegram"}, dry_run=dry_run)

    if result.status == "sent":
        print("OK: Message sent to Telegram.")
        return 0
    if result.dry_run:
        print("OK: Dry-run — message was NOT sent. Use --send to send.")
        return 0
    print("Failed:", result.status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
