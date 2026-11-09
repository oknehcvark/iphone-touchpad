#!/bin/bash
# Двойной клик: если сервер уже работает (автозапуск), показывает QR-код и PIN.
# Если не работает, запускает его в этом окне.
cd "$(dirname "$0")"
if curl -s -o /dev/null --max-time 1 http://localhost:8899/; then
  open http://localhost:8899/qr
  exit 0
fi
exec python3 server.py --open --prompt
