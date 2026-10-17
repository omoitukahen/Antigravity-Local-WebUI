import sys
import time
import os
from pathlib import Path
Path('mock-usage.pid').write_text(str(os.getpid()),encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print('Antigravity CLI 1.3.0\r\n> \r\n? for shortcuts',flush=True)
command=''
while '/usage' not in command:
    command+=sys.stdin.readline()
panel=(Path(__file__).parent/'fixtures/quota-argon-1.3.0.txt').read_text(encoding='utf-8').rstrip()
panel=panel.replace('GEMINI MODELS','Account: person@example.com\nToken usage information\nGEMINI MODELS')
sys.stdout.write('\x1b[2J\x1b[H'+panel.replace('\n','\r\n')+'\r\n↑/↓ Scroll · esc Close')
sys.stdout.flush()
time.sleep(60)
