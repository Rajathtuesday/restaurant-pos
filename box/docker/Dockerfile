# The Rasova box image: Rasova compiled to machine code (box/docker/compile.py),
# no source inside. Built on Rasova's PC, shipped to the box as a signed bundle.
#
#   docker build -f box/docker/Dockerfile -t rasova-box:<version> --build-arg RASOVA_VERSION=<version> .
#   docker build -f box/docker/Dockerfile --target test -t rasova-box-test .   (compiled app + the tests)

ARG PYTHON=python:3.13-slim-bookworm

# ── 1. build: install dependencies, compile Rasova ──────────────────────────
FROM ${PYTHON} AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir "cython==3.1.4" "setuptools>=75"
COPY . .
RUN python box/docker/compile.py /src /app-test --keep-tests --without-tests-into /app
# No debug information or symbol names in what ships (smaller, and less to read).
RUN find /app /app-test -name '*.so' -exec strip --strip-unneeded {} +

# ── 2. the runtime every box image shares ───────────────────────────────────
FROM ${PYTHON} AS runtime
# libpq for the database, postgresql-client 15 for backups (same major as the
# database), Pango for PDF bills (WeasyPrint), tini to pass signals properly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client-15 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 rasova
COPY --from=build /install /usr/local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=core.settings \
    RASOVA_EDITION=box TZ=Asia/Kolkata
WORKDIR /app

# ── 3. the box ──────────────────────────────────────────────────────────────
FROM runtime AS box
ARG RASOVA_VERSION=dev
ENV RASOVA_VERSION=${RASOVA_VERSION} LOG_DIR=/data/logs
COPY --from=build /app /app
COPY box/docker/entrypoint.sh /usr/local/bin/rasova-entrypoint
# collectstatic only loads the settings, which insist on these being set;
# the real ones are made on the box at install (rasova-box), never baked in.
RUN chmod 755 /usr/local/bin/rasova-entrypoint \
    && SECRET_KEY=build-only DB_ENGINE=django.db.backends.postgresql \
       FIELD_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
       python manage.py collectstatic --noinput -v 0 \
    && mkdir -p /app/media /app/public /data /backups && chown -R rasova:rasova /app/media /app/public /data /backups
USER rasova
EXPOSE 8000
ENTRYPOINT ["tini", "--", "rasova-entrypoint"]
CMD ["web"]

# ── 4. test: the compiled app with the tests as .py, to prove nothing changed
FROM runtime AS test
# The suite runs as the cloud does (the box tests switch box mode on
# themselves); tests that read files a box doesn't ship skip (core/testing.py).
ENV RASOVA_EDITION=cloud RASOVA_COMPILED_TESTS=1
COPY --from=build /app-test /app
COPY requirements-test.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt \
    && mkdir -p /app/public /app/media
CMD ["python", "manage.py", "test", "--noinput", "--parallel", "8"]
