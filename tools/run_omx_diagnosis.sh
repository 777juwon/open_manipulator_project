#!/usr/bin/env bash
# GPU 컨테이너에서 실행한다. 로그 폴더와 콘솔 저장을 자동으로 준비한다.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_path="${OMX_PYTHON:-/opt/lerobot_venv/bin/python}"
log_root="${OMX_LOG_ROOT:-/workspace/rollout_logs}"
run_dir="${log_root}/diagnosis_$(date +%Y%m%d_%H%M%S_%N)"
model_path="${OMX_MODEL_PATH:-/workspace/models/smolvla_omx_real_omit_episode_15}"
follower_port="${OMX_FOLLOWER_PORT:-/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00}"
camera_path="${OMX_CAMERA_PATH:-/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0}"
task="${OMX_TASK:-Pick up the green block and place it in the box.}"
duration="${OMX_DIAG_DURATION:-10}"

mkdir -p "$run_dir"
printf '진단 로그 폴더: %s\n' "$run_dir"

if "$python_path" -I -u "$script_dir/diagnose_omx_rollout.py" \
  --log-dir "$run_dir" "$@" -- \
  --strategy.type=base \
  --inference.type=sync \
  --policy.path="$model_path" \
  --policy.device=cuda \
  --policy.use_amp=true \
  --device=cuda \
  --robot.type=omx_follower \
  --robot.port="$follower_port" \
  --robot.id=omx_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: '$camera_path', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}" \
  --rename_map='{"observation.images.front":"observation.images.camera1"}' \
  --task="$task" \
  --fps=30 \
  --duration="$duration" \
  --return_to_initial_position=false \
  --display_data=false \
  --play_sounds=false \
  2>&1 | tee "$run_dir/console.log"; then
  result=0
else
  result=$?
fi

printf '진단 종료 코드: %s\n로그 폴더: %s\n' "$result" "$run_dir"
exit "$result"
