#!/usr/bin/env bash
# 새 배치 16 모델에 맞는 카메라 순서와 경로를 지정해 기존 실행기를 사용한다.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OMX_100EP_MODEL_PATH="${OMX_BS16_MODEL_PATH:-/workspace/models/omx_smolvla_dualcam_lora_bs16_20k_compat061}"
export OMX_ROLLOUT_RENAME_MAP='{"observation.images.top":"observation.images.camera1","observation.images.front":"observation.images.camera2"}'
export OMX_RUN_LABEL=80ep_bs16
export PATH="/opt/lerobot_venv/bin:$PATH"
exec bash "$script_dir/run_omx_100ep.sh" "$@"
