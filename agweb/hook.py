"""No pickle transport: only authenticated JSON bytes over a per-run pipe."""
import json
import os
import sys
from multiprocessing.connection import Client

def main():
    decision = {"decision": "deny", "reason": "WebUI guard unavailable"}
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized hook")
        payload = json.loads(raw.decode("utf-8"))
        with Client(os.environ["AGWEB_HOOK_PIPE"], family="AF_PIPE", authkey=bytes.fromhex(os.environ["AGWEB_HOOK_KEY"])) as connection:
            if os.getenv('AGWEB_HOOK_FAULT')=='exception':raise ValueError('injected acceptance fault')
            connection.send_bytes(json.dumps({"kind": sys.argv[1], "payload": payload}).encode())
            if not connection.poll(2):
                raise TimeoutError("guard acknowledgement missing")
            decision = json.loads(connection.recv_bytes(1024 * 1024))
    except Exception:
        # A timeout/nonzero exit is NOT treated as a permission denial by our supervisor.
        # The supervisor's PreInvocation/stream correlation is a separate safety gate.
        try:
            with Client(os.environ["AGWEB_HOOK_PIPE"],family='AF_PIPE',authkey=bytes.fromhex(os.environ['AGWEB_HOOK_KEY'])) as alarm:
                alarm.send_bytes(json.dumps({'kind':'guard_error','payload':{}}).encode())
                if alarm.poll(1):alarm.recv_bytes(1024*1024)
        except Exception:pass
    print(json.dumps(decision, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
