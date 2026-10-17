"""Explicit model-list diagnosis. Never sends an inference prompt."""
import json
import sys
from agweb.config import APP_ROOT,Config
from agweb.diagnostics import collect_models
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    report=collect_models(Config(APP_ROOT,'diagnostic-only',0),debug='--parser-debug' in sys.argv)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['ok'] else 1)
