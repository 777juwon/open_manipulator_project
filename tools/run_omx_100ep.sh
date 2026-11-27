#!/usr/bin/env bash
# GPU Docker 내부에서 100개 선택 데이터로 학습한 최종 모델을 평가한다.
set -euo pipefail
usage() {
  echo '사용법: bash tools/run_omx_100ep.sh 대상 [rtc|sync] [FPS] [--dry-run]'
  echo '대상: green yellow red blue white_tape. 기본값: rtc, 30 Hz. Ctrl+C로 종료.'
}
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
if (( $# < 1 )); then usage >&2; exit 2; fi
target="$1"; shift
engine=rtc
fps=30
dry_run=false
if (( $# > 0 )) && [[ "$1" != --* ]]; then engine="$1"; shift; fi
if (( $# > 0 )) && [[ "$1" != --* ]]; then fps="$1"; shift; fi
if (( $# > 0 )) && [[ "$1" == --dry-run ]]; then dry_run=true; shift; fi
if (( $# > 0 )); then usage >&2; exit 2; fi
case "$target" in
  green|yellow|red|blue) object="$target block" ;;
  white_tape) object='white tape' ;;
  *) usage >&2; exit 2 ;;
esac
case "$engine" in rtc|sync) ;; *) usage >&2; exit 2;; esac
case "$fps" in 5|10|15|20|25|30) ;; *) echo 'FPS는 5, 10, 15, 20, 25, 30 중 선택하세요.' >&2; exit 2;; esac
python_path="${OMX_PYTHON:-/opt/lerobot_venv/bin/python}"
# 이전 10개 모델을 실수로 사용하지 않도록 별도의 환경변수와 최종 step 경로를 사용한다.
model="${OMX_100EP_MODEL_PATH:-/workspace/outputs/train/smolvla_five_objects_100ep_lora20k_20261119_092117_591357546/checkpoints/020000/pretrained_model}"
follower="${OMX_FOLLOWER_PORT:-/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00}"
wrist="${OMX_WRIST_CAMERA:-/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0}"
top="${OMX_TOP_CAMERA:-/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_200901010001-video-index0}"
camera_layout="${OMX_ROLLOUT_CAMERA_LAYOUT:-dual}"
case "$camera_layout" in
  dual)
    cameras="{front: {type: opencv, index_or_path: '$wrist', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}, top: {type: opencv, index_or_path: '$top', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}"
    devices=("$follower" "$wrist" "$top") ;;
  top)
    cameras="{top: {type: opencv, index_or_path: '$top', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}"
    devices=("$follower" "$top") ;;
  *) echo '카메라 구성은 dual 또는 top이어야 합니다.' >&2; exit 2 ;;
esac
run_label="${OMX_RUN_LABEL:-100ep}"
rename_map="${OMX_ROLLOUT_RENAME_MAP:-}"
if [[ -z "$rename_map" ]]; then
  rename_map='{"observation.images.front":"observation.images.camera1","observation.images.top":"observation.images.camera2"}'
fi
log_dir="${OMX_LOG_ROOT:-/workspace/rollout_logs}/${run_label}_${target}_${engine}_${fps}hz_$(date +%Y%m%d_%H%M%S_%N)"
task="Pick up the $object and place it in the box."
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${OMX_RECORD_ROLLOUT:-0}" in
  1)
    command=("$python_path" -I -u "$script_dir/record_omx_rollout.py" --log-dir "$log_dir")
    case "${OMX_MANUAL_EVALUATION:-0}" in
      1) command+=(--manual-evaluation) ;;
      0) ;;
      *) echo 'OMX_MANUAL_EVALUATION은 0 또는 1이어야 합니다.' >&2; exit 2 ;;
    esac
    command+=(--) ;;
  0) command=("$python_path" -I -u -m lerobot.scripts.lerobot_rollout) ;;
  *) echo 'OMX_RECORD_ROLLOUT은 0 또는 1이어야 합니다.' >&2; exit 2 ;;
esac
command+=(
  --strategy.type=base "--inference.type=$engine"
  "--policy.path=$model" --policy.device=cuda --policy.use_amp=true --device=cuda
  --robot.type=omx_follower "--robot.port=$follower" --robot.id=omx_follower_arm
  --robot.max_relative_target=5
  "--robot.cameras=$cameras"
  "--rename_map=$rename_map"
  "--task=$task" "--fps=$fps" --duration=0 --return_to_initial_position=true
  --display_data=false --play_sounds=false)
if [[ "$engine" == rtc ]]; then
  command+=(--inference.rtc.execution_horizon=10 --inference.rtc.max_guidance_weight=10.0)
fi
printf '작업: %s\n모델: %s\n추론: %s, 목표 제어: %s Hz, 영상: 각 30 FPS\n로그: %s\n' \
  "$task" "$model" "$engine" "$fps" "$log_dir"
if "$dry_run"; then
  printf '실행 예정 명령: '; printf '%q ' "${command[@]}"; printf '\n'
  exit 0
fi
for file in config.json adapter_config.json adapter_model.safetensors policy_preprocessor.json policy_postprocessor.json; do
  if [[ ! -f "$model/$file" ]]; then echo "완료된 모델 파일을 찾지 못했습니다: $model/$file" >&2; exit 1; fi
done
for device in "${devices[@]}"; do
  if [[ ! -e "$device" ]]; then echo "장치 경로가 없습니다: $device" >&2; exit 1; fi
done
"$python_path" -I -c 'import torch; assert torch.cuda.is_available(), "CUDA GPU를 사용할 수 없어 실행을 중단합니다."; print("GPU:", torch.cuda.get_device_name(0))'
mkdir -p "$log_dir"
printf '%q ' "${command[@]}" > "$log_dir/command.txt"
printf '\n' >> "$log_dir/command.txt"
echo '실제 팔로워를 움직입니다. 시간 제한과 작업 성공 자동 종료는 없습니다. Ctrl+C로 종료하세요.'
if [[ "${OMX_RECORD_ROLLOUT:-0}" == 1 && "${OMX_MANUAL_EVALUATION:-0}" == 1 ]]; then
  echo '제어 시작 후 실행 터미널에서 s=성공, f=실패, q=판정 없이 종료. Enter는 누르지 않습니다.'
fi
echo '복구되지 않는 통신·추론 오류 처리는 유지합니다. 종료 시 실행 시작 자세로 복귀하도록 설정돼 있습니다.'
# 첫 종료 신호는 LeRobot이 처리하고 tee는 영상 정리와 종료 로그를 기다린다.
trap ':' INT TERM
if "${command[@]}" 2>&1 | (trap '' INT TERM; exec tee "$log_dir/console.log"); then
  result=0
else
  result=$?
fi
printf '%s\n' "$result" > "$log_dir/exit_code.txt"
printf '실행 종료 코드: %s\n로그: %s\n' "$result" "$log_dir"
exit "$result"
