"""Install the same staged-source audit used by CI."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
location = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--git-path', 'hooks/pre-commit'], text=True).strip()
target = Path(location)
if not target.is_absolute():
    target = ROOT/target
body = '#!/bin/sh\nset -e\npython scripts/check_repository.py\npython scripts/check_docs.py\n'
if target.exists() and target.read_text() != body:
    raise SystemExit('Existing different hook preserved: '+str(target))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(body, newline='\n')
target.chmod(0o755)
print('Installed:', target)
