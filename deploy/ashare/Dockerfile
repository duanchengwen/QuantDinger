# Full official API dependencies; runtime execution stays paper-only in source.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend_api_python/requirements.lock backend_api_python/requirements-ashare.txt ./
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install --prefer-binary --no-deps -r requirements.lock
COPY deploy/ashare/requirements-runtime.txt /opt/paper/requirements-runtime.txt
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -r requirements-ashare.txt -r /opt/paper/requirements-runtime.txt
COPY deploy/ashare/check_dependencies.py /opt/paper/check_dependencies.py
RUN python /opt/paper/check_dependencies.py
ENV PYTHONPATH=/app
COPY backend_api_python/ ./
COPY deploy/ashare/runtime_check.py deploy/ashare/bootstrap.py /opt/paper/
RUN groupadd --gid 10001 paper && useradd --uid 10001 --gid paper --create-home paper \
    && mkdir -p /app/logs /app/data/evidence /app/settings \
    && chown -R paper:paper /app/logs /app/data /app/settings && chmod 0700 /app/settings
USER 10001:10001
CMD ["gunicorn", "-c", "gunicorn_config.py", "run:app"]
