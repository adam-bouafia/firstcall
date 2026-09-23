#!/usr/bin/env bash
# Build-free image import into k3s' containerd. No registry, no docker daemon.
# Why a script and not a pipe in the Makefile: `podman save | sudo k3s ctr import -`
# hands sudo's stdin to the pipe, so sudo cannot read a password and dies with
# "sudo: timed out reading password". We take the password first, then use files.
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io/adam-bouafia}"
TAG="${TAG:-0.3.0}"
OCI="${OCI:-$(command -v podman >/dev/null && echo podman || echo docker)}"
IMAGES=("$REGISTRY/firstcall-backend:$TAG" "$REGISTRY/firstcall-frontend:$TAG")

command -v k3s >/dev/null || { echo "k3s not found. Run: make cluster"; exit 1; }

echo ">> importing into k3s needs root. Your sudo password, once:"
sudo -v                     # prompts on the terminal, not through a pipe
# keep the sudo timestamp alive while the (slow) import runs
( while true; do sudo -n true 2>/dev/null; sleep 50; done ) &
KEEPALIVE=$!
trap 'kill "$KEEPALIVE" 2>/dev/null || true' EXIT

tmp="$(mktemp -d)"
trap 'kill "$KEEPALIVE" 2>/dev/null || true; rm -rf "$tmp"' EXIT

for img in "${IMAGES[@]}"; do
  short="${img##*/}"
  tar="$tmp/${short//[:\/]/_}.tar"
  echo ">> saving  $img"
  "$OCI" save -o "$tar" "$img"          # docker-archive is the default for both
  echo ">> import  $short ($(du -h "$tar" | cut -f1))"
  sudo k3s ctr images import "$tar"
done

echo
echo ">> now in k3s:"
sudo k3s ctr images ls -q | grep -E 'firstcall-(backend|frontend)' || {
  echo "!! the images are not in containerd - install will fail"; exit 1; }
