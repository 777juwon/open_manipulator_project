#!/usr/bin/env bash
# Docker 내부에서 대상과 개수를 지정해 새 수집 또는 기존 데이터 이어 수집을 실행한다.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: bash tools/record_omx_dualcam.sh 대상 개수 [public|private|local] [--resume-dataset 기존폴더명] [--dry-run]
예시: bash tools/record_omx_dualcam.sh yellow 10 public
대상: green, yellow, red, blue(블록), white_tape(흰색 테이프)
public: 공개 업로드(기본값), private: 비공개 업로드, local: 로컬 저장만
--dry-run: 실제 장치 연결과 수집 없이 설정·명령만 출력
--resume-dataset: 기존 로컬 데이터셋과 동일한 Hub 저장소에 지정 개수만큼 추가
이어 수집에서도 시간 로그는 새로운 폴더에 저장한다.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# < 2 )); then
  usage >&2
  exit 2
fi
color="$1"
episodes="$2"
shift 2
mode=public
if (( $# > 0 )) && [[ "$1" != --* ]]; then mode="$1"; shift; fi
dry_run=false
resume=false
resume_name=""
while (( $# > 0 )); do
  case "$1" in
    --dry-run) dry_run=true; shift ;;
    --resume-dataset)
      if (( $# < 2 )) || [[ "$2" == --* ]] || "$resume"; then usage >&2; exit 2; fi
      resume=true; resume_name="$2"; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
if "$resume" && [[ ! "$resume_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
  echo '기존 데이터셋은 경로나 URL이 아닌 폴더 이름만 지정하세요.' >&2
  exit 2
fi
case "$color" in
  green|yellow|red|blue) object="$color block" ;;
  white_tape) object="white tape" ;;
  *) echo '지원 대상: green yellow red blue white_tape' >&2; exit 2 ;;
esac
if [[ ! "$episodes" =~ ^[1-9][0-9]*$ ]]; then
  echo '에피소드 개수는 1 이상의 정수여야 합니다.' >&2
  exit 2
fi
push=true
private=false
case "$mode" in
  public) ;;
  private) private=true ;;
  local) push=false; private=true ;;
  *) usage >&2; exit 2 ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_path="${OMX_PYTHON:-/opt/lerobot_venv/bin/python}"
owner="${OMX_OWNER:-juwon777}"
# 이전 터미널의 OMX_RUN_NAME을 사용하지 않아 색상과 저장 경로가 섞이지 않는다.
run_name="omx_${color}_dualcam_$(date +%Y%m%d_%H%M%S_%N)"
if "$resume"; then run_name="$resume_name"; fi
root="${OMX_DATASET_BASE:-/workspace/lerobot_datasets}/$run_name"
timing="${OMX_TIMING_BASE:-/workspace/record_timing}/$run_name"
if "$resume"; then timing="${timing}_append_$(date +%Y%m%d_%H%M%S_%N)"; fi
follower="${OMX_FOLLOWER_PORT:-/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00}"
leader="${OMX_LEADER_PORT:-/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_57A9BF7F50323952582E3120FF073125-if00}"
wrist="${OMX_WRIST_CAMERA:-/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0}"
top="${OMX_TOP_CAMERA:-/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_200901010001-video-index0}"
task="Pick up the $object and place it in the box."
cameras="{front: {type: opencv, index_or_path: '$wrist', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}, top: {type: opencv, index_or_path: '$top', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}"
command=("$python_path" -I "$script_dir/record_omx_timed.py"
  --timing-dir "$timing" --readable-videos --
  --robot.type=omx_follower "--robot.port=$follower" --robot.id=omx_follower_arm
  "--robot.cameras=$cameras"
  --teleop.type=omx_leader "--teleop.port=$leader" --teleop.id=omx_leader_arm
  "--dataset.repo_id=$owner/$run_name" "--dataset.root=$root"
  "--dataset.single_task=$task" "--dataset.num_episodes=$episodes"
  --dataset.fps=30 --dataset.episode_time_s=60 --dataset.reset_time_s=60
  --dataset.video=true --dataset.streaming_encoding=true
  --dataset.rgb_encoder.vcodec=h264 --dataset.rgb_encoder.preset=fast
  --dataset.encoder_threads=2 "--dataset.push_to_hub=$push" "--dataset.private=$private"
  --dataset.no_stamp=true "--resume=$resume" --display_data=false --play_sounds=false)

printf '작업: %s\n에피소드: %s개\n저장 방식: %s\n데이터셋: %s/%s\n로컬: %s\n시간 로그: %s\n' \
  "$task" "$episodes" "$mode" "$owner" "$run_name" "$root" "$timing"
printf '이어 수집: %s (개수는 이번에 추가할 에피소드 수)\n' "$resume"
if "$dry_run"; then
  printf '실행 예정 명령: '
  printf '%q ' "${command[@]}"
  printf '\n'
  exit 0
fi
if [[ ! -x "$python_path" ]]; then echo "Python 실행 파일 없음: $python_path" >&2; exit 1; fi
if "$resume"; then
  "$python_path" -I "$script_dir/check_omx_resume.py" --root "$root" --task "$task"
elif [[ -e "$root" ]]; then
  echo '기존 저장 경로가 있어 중단합니다.' >&2
  exit 1
fi
for device in "$follower" "$leader" "$wrist" "$top"; do
  if [[ ! -e "$device" ]]; then echo "장치 경로 없음: $device" >&2; exit 1; fi
done
if [[ -e "$timing" ]]; then
  echo '기존 시간 로그 경로가 있어 중단합니다.' >&2
  exit 1
fi
echo '실제 로봇 시연을 기록합니다. 기존 제어 프로그램과 카메라 미리보기를 종료해야 합니다.'
exec "${command[@]}"
