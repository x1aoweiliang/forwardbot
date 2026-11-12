#!/usr/bin/env bash
# Linux source deployment; identifies only app.py in this checkout's backend.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/ruoyi-fastapi-backend"
PYTHON="$ROOT/.venv/bin/python"
ENV_NAME="${APP_ENV:-prod}"
LOG="$BACKEND/nohup.out"
mkdir -p "$BACKEND/vf_admin"
exec 9>"$BACKEND/vf_admin/media-service.lock"
flock -n 9 || { echo '另一个启停操作正在执行'; exit 1; }

find_pids() {
  "$PYTHON" - "$BACKEND" <<'PY'
import os, sys
from pathlib import Path
backend = Path(sys.argv[1]).resolve()
for process in Path('/proc').glob('[0-9]*'):
    try:
        args = (process / 'cmdline').read_bytes().split(b'\0')
        if (process / 'cwd').resolve() == backend and any(
            arg in (b'app.py', os.fsencode(backend / 'app.py')) for arg in args
        ):
            print(process.name)
    except (OSError, RuntimeError):
        continue
PY
}

stop() {
  local pids pid round
  pids="$(find_pids)"
  if [[ -z "$pids" ]]; then echo '后端已停止'; return; fi
  for pid in $pids; do kill -TERM "$pid"; done
  echo '正在正常停止后端，等待下载保存进度和上传退出…'
  for ((round=0; round<120; round++)); do
    if [[ -z "$(find_pids)" ]]; then echo '后端已停止'; return; fi
    sleep 1
  done
  echo '后端仍在结束当前操作，尚未启动新进程；稍后再次执行 restart。'
  return 1
}

start() {
  local pids pid
  pids="$(find_pids)"
  if [[ -n "$pids" ]]; then echo "后端已经运行，PID: $pids"; return; fi
  cd "$BACKEND"
  nohup "$PYTHON" app.py "--env=$ENV_NAME" >>"$LOG" 2>&1 < /dev/null 9>&- &
  pid=$!
  "$PYTHON" - "$BACKEND/.env.$ENV_NAME" "$pid" "$LOG" <<'PY'
import os, sys, time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from dotenv import dotenv_values
settings = dotenv_values(sys.argv[1])
port = os.environ.get('APP_PORT') or settings.get('APP_PORT') or '9099'
opener = build_opener(ProxyHandler({}))
for _ in range(60):
    try:
        os.kill(int(sys.argv[2]), 0)
    except ProcessLookupError:
        break
    try:
        with opener.open(f'http://127.0.0.1:{port}/captchaImage', timeout=1) as response:
            if response.status == 200:
                print(f'后端已启动，PID: {sys.argv[2]}，日志: {sys.argv[3]}')
                sys.exit(0)
    except (OSError, URLError):
        time.sleep(1)
print(f'后端尚未通过启动检查，请查看日志: {sys.argv[3]}', file=sys.stderr)
sys.exit(1)
PY
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status)
    pids="$(find_pids)"
    if [[ -n "$pids" ]]; then echo "后端运行中，PID: $pids，日志: $LOG"; else echo '后端已停止'; fi
    ;;
  *) echo "用法: $0 {start|stop|restart|status}"; exit 2 ;;
esac
