#!/usr/bin/env python3
"""Run the local React and Python development servers; stop both on Ctrl+C."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / 'backend' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not PYTHON.exists() or not (ROOT / 'frontend/node_modules').exists():
    sys.exit('Run scripts/setup.sh first (or see README.md for manual setup).')
processes=[]
try:
    processes.append(subprocess.Popen([str(PYTHON),'-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port','8000','--reload','--reload-dir','backend/app'],cwd=ROOT,start_new_session=True))
    processes.append(subprocess.Popen(['npm','run','dev'],cwd=ROOT/'frontend',start_new_session=True))
    print('\nTwin: http://127.0.0.1:5174\nAPI docs: http://127.0.0.1:8000/docs\nCtrl+C stops both servers.\n',flush=True)
    while all(p.poll() is None for p in processes):time.sleep(.5)
    if any(p.returncode for p in processes if p.poll() is not None):sys.exit('A server stopped. Check the messages above; ports 8000 and 5174 must be available.')
except KeyboardInterrupt:
    pass
finally:
    for p in processes:
        if p.poll() is None:
            if os.name=='posix':os.killpg(p.pid,signal.SIGTERM)
            else:p.terminate()
    for p in processes:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name=='posix':os.killpg(p.pid,signal.SIGKILL)
            else:p.kill()
