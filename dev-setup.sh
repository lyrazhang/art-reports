#!/bin/sh

set -ex

get_data() {
  local key="$1"
  sed -e '1,/<<'$key'$/ d; /^'$key'/,$ d' $0
}

sudo DEBIAN_FRONTEND=noninteractive apt-get -qy install python-dev python-pip libffi-dev libssl-dev git wget python-virtualenv openjdk-7-jre-headless

get_data POSTGRESQL_KEY | sudo apt-key add -

sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt/ trusty-pgdg main" > /etc/apt/sources.list.d/postgresql.list'
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -qy postgresql-9.5 postgresql-contrib-9.5 libpq-dev

if [ -d /vagrant ]; then
  cd /vagrant
fi

if [ ! -d ext/art-testing ]; then
  git clone "https://android-review.linaro.org/linaro/art-testing" ext/art-testing
fi
sudo DEBIAN_FRONTEND=noninteractive apt-get install -qy \
  python3-scipy python3-numpy

if [ ! -f .virtualenv/bin/python ]; then
  virtualenv .virtualenv
fi
.virtualenv/bin/pip install -r requirements-dev.txt

if [ ! -f crayonbox/settings/private.py ]; then
  get_data DEVELOPMENT_SETTINGS | sed -e "s/\$USER/$USER/g" > crayonbox/settings/private.py
fi

# create database and user
sudo -u postgres createuser --createdb "$USER" || true
sudo -u postgres psql -c "ALTER USER $USER WITH PASSWORD 'test'"
sudo -u postgres psql -d 'template1' -c "CREATE EXTENSION IF NOT EXISTS hstore;"
createdb art-reports || true
.virtualenv/bin/python ./manage.py migrate


########################################################################
# SCRIPT ENDS HERE
########################################################################

: <<POSTGRESQL_KEY
-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v1

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
IIP9up4xwgje9LB7fMxsSkCDTHOk
=s3DI
-----END PGP PUBLIC KEY BLOCK-----
POSTGRESQL_KEY

: <<DEVELOPMENT_SETTINGS
from . import *

URL = 'http://localhost:8000'

SECRET_KEY = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

QUERY_INSPECT_ENABLED = True
QUERY_INSPECT_LOG_QUERIES = True
MIDDLEWARE_CLASSES += ( 'qinspect.middleware.QueryInspectMiddleware',)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'art-reports',
        'USER': '$USER',
        'HOST': 'localhost',
        'PASSWORD': 'test',
    }
}

LOGGING['loggers']['qinspect'] = { 'handlers': ['console'], 'level': 'DEBUG',
                                   'propagate': True, }

CELERY_ALWAYS_EAGER = True
CELERY_EAGER_PROPAGATES_EXCEPTIONS = True

EMAIL_REPORTS_TO = [ "$USER@localhost.localdomain", ]

EMAIL_HOST = "localhost"

MEDIA_ROOT = "/var/tmp/art-reports-dev/media"

DEVELOPMENT_SETTINGS

