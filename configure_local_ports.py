"""Update only the frontend origins; preserve existing database credentials."""
from pathlib import Path
import os
import re
path = Path(__file__).resolve().parent / '.env'
if not path.exists():
    raise SystemExit('No .env found. Create it and configure DATABASE_URL before running this script.')
lines = path.read_text().splitlines()
updated = []
for line in lines:
    if re.match(r'^\s*(?:export\s+)?ALLOWED_ORIGINS\s*=', line):
        continue
    updated.append(line)
updated.append('ALLOWED_ORIGINS=http://localhost:5181,http://127.0.0.1:5181')
temp = path.with_name('.env.ports-tmp')
try:
    temp.write_text('\n'.join(updated) + '\n')
    os.chmod(temp, 0o600)
    os.replace(temp, path)
finally:
    temp.unlink(missing_ok=True)
print('Frontend origins updated for port 5181. Other settings preserved.')
