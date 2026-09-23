FROM python:3.12-slim
# kubectl supports API servers one minor version either side (1.34 - 1.36 for v1.35)
ARG KUBECTL_VERSION=v1.35.8
# set by buildx/podman for multi-arch builds; falls back to the build host's architecture
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && URL=https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl \
 && curl -fsSLo /usr/local/bin/kubectl "$URL" \
 && echo "$(curl -fsSL "$URL.sha256")  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
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
