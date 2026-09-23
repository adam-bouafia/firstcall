FROM python:3.12-slim
ARG KUBECTL_VERSION=v1.34.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
 && chmod +x /usr/local/bin/kubectl && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY scenarios/ scenarios/
WORKDIR /srv/backend
ENV FIRSTCALL_DB=/data/firstcall.db
EXPOSE 8000
USER 65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
