#!/bin/bash
set -e

# docker-compose starts db/web together with no readiness gate -- Postgres
# can take a few seconds after its container reports "started" before it's
# actually accepting connections, so retry rather than failing on the first
# migrate.
python - <<'PYEOF'
import os
import sys
import time

import psycopg2

for attempt in range(30):
    try:
        psycopg2.connect(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ.get("POSTGRES_HOST", "db"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
        ).close()
        break
    except psycopg2.OperationalError:
        time.sleep(1)
else:
    sys.exit("Postgres never became reachable")
PYEOF

python manage.py migrate --noinput
python manage.py collectstatic --noinput

gunicorn -b 0.0.0.0:8000 --workers 2 --threads 4 --worker-class gthread --timeout 600 config.wsgi
