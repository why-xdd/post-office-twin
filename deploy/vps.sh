#!/usr/bin/env bash
# Выкладка на свой сервер без Docker. Клиент собирается здесь, на сервер уходят только нужные файлы,
# зависимости ставит uv строго по uv.lock, сервис работает службой systemd на порту 8080.
#
#   deploy/vps.sh root@сервер
#
# SSH_OPTS — дополнительные ключи ssh, например "-o HostName=1.2.3.4".
# Порт 8080 в брандмауэре открывается один раз вручную: ufw allow 8080/tcp.
set -euo pipefail

HOST=${1:?Укажите сервер: deploy/vps.sh root@сервер}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC2086
remote() { ssh ${SSH_OPTS:-} "$HOST" "$@"; }

echo "Сборка клиента"
(cd "$ROOT/apps/web" && VITE_API_URL="" npm run build >/dev/null)

echo "Отправка файлов"
tar -C "$ROOT" --exclude=__pycache__ -czf - \
  services/api/app services/api/pyproject.toml services/api/uv.lock \
  apps/web/dist data/demo_plan.json data/example_plan.json data/network deploy/ops-twin.service |
  remote 'rm -rf /opt/ops-twin.new && mkdir -p /opt/ops-twin.new && tar -xzf - -C /opt/ops-twin.new'

if ! remote 'command -v uv >/dev/null'; then
  # Сервер скачивает uv сам из релизов на GitHub и сверяет контрольную сумму: так быстрее и надёжнее,
  # чем гнать 60 МБ со своего компьютера.
  echo "Установка uv"
  remote 'set -e
    cd /tmp
    F=uv-x86_64-unknown-linux-gnu.tar.gz
    U=https://github.com/astral-sh/uv/releases/download/0.12.5/$F
    curl -fsSL -o "$F" "$U" && curl -fsSL -o "$F.sha256" "$U.sha256"
    sha256sum -c "$F.sha256"
    tar -xzf "$F" && install -m 755 uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv
    rm -rf "$F" "$F.sha256" uv-x86_64-unknown-linux-gnu'
fi

echo "Зависимости и перезапуск"
remote 'set -e
  cd /opt/ops-twin.new/services/api
  UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --python python3.12 -q
  install -m 644 /opt/ops-twin.new/deploy/ops-twin.service /etc/systemd/system/ops-twin.service
  rm -rf /opt/ops-twin.old
  if [ -d /opt/ops-twin ]; then mv /opt/ops-twin /opt/ops-twin.old; fi
  mv /opt/ops-twin.new /opt/ops-twin
  systemctl daemon-reload
  systemctl enable -q ops-twin
  systemctl restart ops-twin
  for _ in $(seq 1 30); do
    if curl -fs -m 2 http://127.0.0.1:8080/health >/dev/null; then echo "Сервис отвечает"; exit 0; fi
    sleep 1
  done
  journalctl -u ops-twin -n 40 --no-pager
  exit 1'
