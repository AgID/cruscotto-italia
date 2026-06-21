#!/usr/bin/env bash
# Accessibilità WCAG2AA del ChatBot (stato iniziale + conversazione).
# pa11y NON installato globalmente -> via npx. Config: pa11y-conf-interactive.json (--no-sandbox + actions).
# Prerequisito: servizio attivo su 127.0.0.1:3011. Uso: bash pa11y-chatlab.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
npx --yes pa11y --config "$DIR/pa11y-conf-interactive.json" \
  --standard WCAG2AA http://127.0.0.1:3011/ 2>&1 | tee /tmp/pa11y_chatlab.log
