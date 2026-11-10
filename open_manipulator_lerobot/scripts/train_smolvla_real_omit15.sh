#!/usr/bin/env bash

set -euo pipefail

VENV=/workspace/lerobot_venv
DATASET=/workspace/lerobot_datasets/realworld/omx_real_20261110_110159
OUTPUT=/workspace/outputs/smolvla_omx_real_omit_episode_15

# The source dataset contains episodes 0 through 25. Episode 15 is intentionally
# omitted without modifying the downloaded dataset.
EPISODES='[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,16,17,18,19,20,21,22,23,24,25]'

exec "$VENV/bin/lerobot-train" \
  --policy.path=lerobot/smolvla_base \
  --policy.device=cuda \
  --policy.use_amp=true \
  --policy.push_to_hub=false \
  --dataset.repo_id=juwon777/omx_real_20261110_110159 \
  --dataset.root="$DATASET" \
  --dataset.episodes="$EPISODES" \
  --dataset.eval_split=0.1 \
  --dataset.image_transforms.enable=true \
  --dataset.video_backend=pyav \
  --rename_map='{"observation.images.front":"observation.images.camera1"}' \
  --batch_size=1 \
  --steps=10000 \
  --eval_steps=500 \
  --max_eval_samples=512 \
  --save_checkpoint=true \
  --save_freq=1000 \
  --log_freq=20 \
  --num_workers=2 \
  --peft.method_type=LORA \
  --peft.r=16 \
  --peft.lora_alpha=16 \
  --output_dir="$OUTPUT" \
  --job_name=smolvla_omx_real_omit_episode_15 \
  --wandb.enable=true \
  --wandb.project=omx-real-smolvla \
  --seed=42
