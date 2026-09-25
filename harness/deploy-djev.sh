#!/usr/bin/env bash
# Deploy djev (DiffusionGemma-Jev, via github.com/taeold/djev-run) as a PRIVATE Cloud Run GPU
# service. NOT RUN YET — our project had no quota (checked 2026-09-24 via serviceusage consumerQuotaMetrics):
#   nvidia_rtx_pro_6000_gpu_allocation_no_zonal_redundancy  us-central1 = 0   (every region = 0)
#   nvidia_l4_gpu_allocation_no_zonal_redundancy            us-central1 = 3   (useless: NVFP4 needs Blackwell)
#
# Differences from the upstream README, all deliberate:
#   - --no-allow-unauthenticated: upstream sets --allowed-origins '*' and has no auth of its own,
#     so a public URL would be free GPU inference for anyone at ~$3/hr of OUR money.
#   - image pinned by digest (set IMAGE_DIGEST); :latest from a third-party GHCR is a supply-chain hole.
#   - labels so the gcp-billing-kill-switch and audits can tell what this is. It is NOT in
#     PROTECTED_SERVICES, so a budget breach scales it to zero — correct for an experiment.
#   - min-instances=0 / max-instances=1: $0 idle, one GPU at most.
#   - weights mounted read-only: entrypoint.sh only `cp -r`s them into /dev/shm.
# Known and accepted: the entrypoint runs `vllm serve --trust-remote-code`, i.e. Python shipped
# in the nvidia/diffusiongemma HF repo executes at load. Alternative to trusting GHCR at all:
# build the 4-file image ourselves from the upstream repo (its base is pinned by digest).
set -euo pipefail
: "${PROJECT:?set PROJECT=<your GCP project id>}"
REGION=us-central1
BUCKET="${BUCKET:?set BUCKET=<a GCS bucket name for the weights>}"
SERVICE=djev-commander-experiment
: "${IMAGE_DIGEST:?set IMAGE_DIGEST=sha256:... (resolve ghcr.io/taeold/djev-run:latest once and pin it)}"

if [[ "${1:-}" == "weights" ]]; then
  # ~18 GB. The HF repo may be gated behind the Gemma licence; needs `hf auth login` first.
  gcloud storage buckets create "gs://${BUCKET}" --project="$PROJECT" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
  hf download nvidia/diffusiongemma-26B-A4B-it-NVFP4 --local-dir /tmp/dgemma
  gcloud storage cp -r /tmp/dgemma/* "gs://${BUCKET}/dgemma/"
  exit 0
fi

gcloud beta run deploy "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --image="ghcr.io/taeold/djev-run@${IMAGE_DIGEST}" \
  --no-allow-unauthenticated \
  --labels=purpose=experiment,experiment=djev-commander \
  --gpu=1 --gpu-type=nvidia-rtx-pro-6000 --no-gpu-zonal-redundancy \
  --cpu=20 --memory=80Gi --no-cpu-throttling \
  --concurrency=32 --min-instances=0 --max-instances=1 \
  --port=8080 \
  --network=default --subnet=default --vpc-egress=all-traffic \
  --add-volume=name=weights,type=cloud-storage,bucket="${BUCKET}",readonly=true,mount-options=enable-buffered-read=true \
  --add-volume-mount=volume=weights,mount-path=/mnt/gcs \
  --startup-probe=httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=5,periodSeconds=2,timeoutSeconds=2,failureThreshold=120

echo "Then: DJEV_URL=\$(gcloud run services describe $SERVICE --region=$REGION --project=$PROJECT --format='value(status.url)') \\"
echo "      DJEV_AUTH=gcloud python3 paired.py --agent djev --seeds 8"
