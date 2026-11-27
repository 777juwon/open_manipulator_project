#!/usr/bin/env bash
# 탑 카메라 한 대로 학습한 모델을 같은 관측 구성으로 실행한다.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OMX_100EP_MODEL_PATH="${OMX_TOPONLY_MODEL_PATH:-/workspace/models/omx_smolvla_toponly_lora_20k_compat061}"
export OMX_ROLLOUT_CAMERA_LAYOUT=top
export OMX_ROLLOUT_RENAME_MAP='{"observation.images.top":"observation.images.camera1"}'
export OMX_RUN_LABEL=toponly
export OMX_RECORD_ROLLOUT="${OMX_RECORD_ROLLOUT:-1}"
export OMX_MANUAL_EVALUATION="${OMX_MANUAL_EVALUATION:-1}"
export PATH="/opt/lerobot_venv/bin:$PATH"
exec bash "$script_dir/run_omx_100ep.sh" "$@"
