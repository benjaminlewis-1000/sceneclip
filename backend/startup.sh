#!/bin/bash
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

gunicorn -b 0.0.0.0:8000 --workers 2 --threads 4 --worker-class gthread --timeout 600 config.wsgi
