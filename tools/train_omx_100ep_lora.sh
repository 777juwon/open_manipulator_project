#!/usr/bin/env bash
# 준비와 검증이 완료된 100개 선택 데이터로 새 SmolVLA LoRA를 학습한다.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_path="${OMX_PYTHON:-/opt/lerobot_venv/bin/python}"
dataset_root="${OMX_TRAIN_DATASET_ROOT:-/workspace/lerobot_datasets/omx_five_objects_ep10_29_100ep}"
dataset_repo="${OMX_TRAIN_DATASET_REPO:-juwon777/omx_five_objects_ep10_29_100ep}"
job_name="smolvla_five_objects_100ep_lora20k_$(date +%Y%m%d_%H%M%S_%N)"
output="${OMX_TRAIN_OUTPUT_BASE:-/workspace/outputs/train}/$job_name"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != --dry-run ]]; }; then
  echo '사용법: bash tools/train_omx_100ep_lora.sh [--dry-run]' >&2
  exit 2
fi
command=("$python_path" -I -m lerobot.scripts.lerobot_train
  --policy.path=lerobot/smolvla_base --policy.device=cuda --policy.use_amp=true
  --policy.push_to_hub=false "--dataset.repo_id=$dataset_repo" "--dataset.root=$dataset_root"
  --dataset.video_backend=pyav --dataset.eval_split=0.1
  '--rename_map={"observation.images.front":"observation.images.camera1","observation.images.top":"observation.images.camera2"}'
  --peft.method_type=LORA --peft.r=16 --peft.lora_alpha=16
  --batch_size=1 --steps=20000 --num_workers=2 --eval_steps=500 --max_eval_samples=512
  --save_checkpoint=true --save_freq=1000 --log_freq=20
  "--output_dir=$output" "--job_name=$job_name" --wandb.enable=false --seed=42)
printf '선택 데이터셋: %s\n학습: 90개, 검증: 10개(대상별 18개/2개)\nLoRA: r=16, alpha=16, 20000 step\n모델 저장: %s\n' "$dataset_root" "$output"
if [[ "${1:-}" == --dry-run ]]; then
  printf '실행 예정 명령: '; printf '%q ' "${command[@]}"; printf '\n'
  exit 0
fi
"$python_path" -I "$script_dir/prepare_omx_100ep.py" --verify-prepared "$dataset_root"
"$python_path" -I -c 'import torch; assert torch.cuda.is_available(), "CUDA GPU를 사용할 수 없어 학습을 시작하지 않습니다."; print("GPU:", torch.cuda.get_device_name(0))'
exec "${command[@]}"
