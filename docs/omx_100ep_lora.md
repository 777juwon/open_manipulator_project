# 원본 150개 중 10~29번만 선택한 100개 데이터 학습

실행 위치는 GPU Docker 내부 터미널이다. 데이터 준비와 실제 모델 학습은 사용자가 실행한다.

## 1. 데이터 준비

```bash
/opt/lerobot_venv/bin/python -I /root/ros2_ws/src/open_manipulator/tools/prepare_omx_100ep.py
```

원본 경로는 실행 파일의 SOURCES에 고정되어 있다. 다섯 원본은 각각 30개이며
각각 10~29번 20개만 선택한다. 새 선택 사본에만 제외 작업을 수행하고 원본에는 쓰지 않는다.
서로 다른 Task 문장은 그대로 유지한다. 단순히 `LeRobotDataset(episodes=...)`를
merge_datasets에 넘기면 원본 루트 전체를 병합할 수 있어, 실제 선택 사본을 먼저 만든다.

출력: `/workspace/lerobot_datasets/omx_five_objects_ep10_29_100ep`

작업용 선택 사본과 검사 보고서: `/workspace/dataset_preparation/omx_five_objects_ep10_29_100ep`

기존 출력 또는 작업 폴더가 있으면 덮어쓰지 않는다. 중간 실패 시 임의로 학습을 시작하지
말고 오류를 확인한다. 다른 출력 경로는 --output, --work로 지정할 수 있다.
영상 일부는 선택 과정에서 재인코딩될 수 있다. 저장 형식·영상 디코딩 검사를 수행하고
각 에피소드의 관절·Action·시간표를 원본과 대조한다. 병합 영상은 불필요한 추가 병합
인코딩을 피하기 위해 원본 선택 사본별 파일로 유지한다.

모든 검사를 통과한 경우에만 `selection_manifest.json`을 생성한다. 여기에는 원본
데이터셋·원본 에피소드 번호와 새 번호의 대응 관계가 포함된다. 선택 후 번호는 0~99이며
그린 0~19, 옐로우 20~39, 레드 40~59, 블루 60~79, 테이프 80~99다.
원본 시간 검증 로그는 복사하거나 재작성하지 않고 보존한다. manifest로 연결한다.

읽기 전용 사전 점검만 수행하려면 `--check-only`를 붙인다. 준비 명령은 Hub에 업로드하지 않는다.
로컬 데이터로 학습 가능하므로 업로드는 필수가 아니다.

## 2. 학습

```bash
bash /root/ros2_ws/src/open_manipulator/tools/train_omx_100ep_lora.sh
```

- 베이스: lerobot/smolvla_base. 이전 50개 모델을 이어 학습하는 것이 아니라 새 LoRA를 학습한다.
- LoRA r=16, alpha=16. 총 20,000 step. CUDA와 AMP, batch_size=1.
- 입력 이름: front → camera1, top → camera2.
- 대상별 20개 중 18개 학습, 2개 검증. 총 학습 90개/검증 10개.
- 500 step마다 검증, 1,000 step마다 체크포인트 저장. seed=42.
- W&B와 모델 Hub 자동 업로드는 끈다.
- `/workspace/outputs/train/smolvla_five_objects_100ep_lora20k_시각`에 새로 저장한다.

준비 완료 표시와 CUDA 사용 가능 여부를 검사하고, GPU가 없으면 CPU로 학습하지 않고 중단한다.
실제 실행 없이 명령만 확인하려면 마지막에 `--dry-run`을 붙인다. 다른 데이터 경로는
OMX_TRAIN_DATASET_ROOT, OMX_TRAIN_DATASET_REPO로 지정한다. 기존 모델을 덮어쓰지 않는다.

## 3. 최종 모델의 실물 평가

실행 위치: GPU Docker 내부 터미널. 모델 학습 종료 후 최종 020000 체크포인트가
생성되었는지 확인하고 실행한다. Record, Teleoperation, 카메라 미리보기는 종료한다.

```bash
bash /root/ros2_ws/src/open_manipulator/tools/run_omx_100ep.sh green rtc 30
```

green/yellow/red/blue/white_tape에 맞는 기존 Task를 생성한다. 실행 파일의 기본 모델은
`smolvla_five_objects_100ep_lora20k_20261119_092117_591357546/checkpoints/020000/pretrained_model`이다.
다른 학습 결과는 OMX_100EP_MODEL_PATH로 명시한다. 같은 터미널의 과거 OMX_MODEL_PATH를
자동으로 재사용하지 않는다. --dry-run으로 로봇 연결 없이 명령을 확인할 수 있다.

RTC는 행동 묶음 생성을 배경에서 처리하고 앞 행동과의 연결을 유도한다. 설정은
execution_horizon=10, max_guidance_weight=10.0이다. 30 Hz는 학습 데이터의 시간 간격에
맞춘 비교 시작점이며 최적 설정으로 검증된 값은 아니다. 과거 sync의 6~8 Hz 경고는
일부 긴 계산 구간이었으며 평균 제어 주기 전체를 뜻하지 않았다. fps=5는 카메라 FPS가
아니라 명령 실행 주기를 낮추므로 단순한 추론 속도 보정으로 사용하지 않는다.

비교 명령:

```bash
bash /root/ros2_ws/src/open_manipulator/tools/run_omx_100ep.sh green sync 30
bash /root/ros2_ws/src/open_manipulator/tools/run_omx_100ep.sh green rtc 5
```

처음부터 두 설정을 동시에 실행하지 않는다. 카메라는 학습과 동일하게 두 대 모두
640×480, 30fps, YUYV를 사용한다. 카메라 포맷 MJPG와 정책 제어 FPS는 별개다.
max_relative_target=5는 관절별 현재 위치 대비 목표 차이 제한이며 degree/rad 단위가 아니다.
설치된 OMX 구성에서는 정규화 위치값 기준이며 이 옵션은 추가 관절 조회를 발생시킬 수 있다.

시간 제한(duration=0)과 작업 성공 자동 종료는 없다. Ctrl+C로 종료하며 현재 실행기는
종료 시 실행 시작 자세 복귀를 요청한다. 복구 불가능한 통신·추론 오류 처리는 유지한다. GPU가 없으면 실행하지 않는다.
Rerun 표시를 끄고 `/workspace/rollout_logs/100ep_대상_방식_주기_시각`에 실제 명령, 콘솔,
종료 코드를 남긴다. 이 실행 파일은 원래 lerobot_rollout을 사용하며 이전 sync 진단 도구의
개별 조회·추가 재시도·상세 timing.csv를 적용하지 않는다. RTC 성능과 통신 안정성은 별도
실물 평가가 필요하다. 기본 100개·bs16 실행기는 콘솔만 기록한다. `OMX_RECORD_ROLLOUT=1`로
관절·영상을 기록하고 `OMX_MANUAL_EVALUATION=1`로 수동 평가를 추가할 수 있다.
toponly 실행기는 두 기능을 기본으로 활성화한다. 상세 내용은 [실행 기록 안내](omx_rollout_recording.md)를 참고한다.

## 새 80개 모델(bs16)의 다운로드와 실행

기존 학습·실행과 별개로 `SeunghakHan/omx_smolvla_dualcam_lora_bs16_20k`를 평가한다.
공개 설정에서 batch_size=16, steps=20000, LoRA r=16/alpha=32를 확인했다.
이 모델은 **top → camera1, front → camera2**로 이전 모델과 카메라 매핑이 반대다.

실행 위치: GPU Docker 내부 터미널. 사용자가 직접 다운로드한다.

```bash
/opt/lerobot_venv/bin/hf download SeunghakHan/omx_smolvla_dualcam_lora_bs16_20k \
  --revision b54acc6cb74ecf3d47cc1c57eac1f9be222276a8 \
  --local-dir /workspace/models/omx_smolvla_dualcam_lora_bs16_20k

/opt/lerobot_venv/bin/python -I \
  /root/ros2_ws/src/open_manipulator/tools/prepare_omx_bs16_model.py

bash /root/ros2_ws/src/open_manipulator/tools/run_omx_bs16.sh green rtc 15
```

다운로드 원본은 보존하며 `_compat061` 사본의 설정만 현재 버전에 맞춘다.
지원하지 않는 dtype/optimizer_fused는 값이 None인 경우만 제거하고, 저장된 로컬
토크나이저의 상대 경로를 해당 사본의 절대 경로로 바꾼다. 가중치와 정규화 값은 바꾸지 않는다.
다른 미지원 설정이 있으면 조용히 삭제하지 않고 중단한다. 이미 사본이 있으면 덮어쓰지 않는다.

run_omx_bs16.sh는 최근 실행기를 재사용하고 모델 경로·카메라 순서·로그 이름만 새 모델에
맞춘다. 대상, rtc/sync, FPS 지정 방식은 동일하다. 시간 제한은 없고 Ctrl+C 시 시작 자세로
복귀하는 현재 설정을 유지한다. batch_size=16은 학습 배치 크기이며 실행 FPS와 별개다.
실제 로봇·카메라 연결 및 받은 가중치의 실물 동작은 아직 검증하지 않았다.

## 기존 100개 도구 검증 범위

실제 원본의 10~29번은 100개, 24,464프레임임을 읽기 전용 점검으로 확인했다.
에피소드 선택·서명·완료 표시·학습 인자에 대한 장치 없는 회귀 테스트를 수행했다.
실제 선택 사본 생성, 영상 편집·병합과 학습은 아직 실행하지 않았다.
# 탑 카메라 전용 모델

실행 시 관절·목표값·영상 기록은 기본 활성화된다. 파일 구성과 종료 방법은
[모델 실행 기록 안내](omx_rollout_recording.md)를 참고한다.

`SeunghakHan/omx_smolvla_toponly_lora_20k`의 리비전
`9a9519543a54dfd229b8711c55f4df448ef2ff82`에서 확인한 매핑은
`observation.images.top → observation.images.camera1`이다.
GPU Docker 내부에서 사용자가 다운로드와 실행을 수행한다.

```bash
/opt/lerobot_venv/bin/hf download SeunghakHan/omx_smolvla_toponly_lora_20k \
  --revision 9a9519543a54dfd229b8711c55f4df448ef2ff82 \
  --local-dir /workspace/models/omx_smolvla_toponly_lora_20k

/opt/lerobot_venv/bin/python -I /root/ros2_ws/src/open_manipulator/tools/prepare_omx_bs16_model.py \
  --source /workspace/models/omx_smolvla_toponly_lora_20k \
  --output /workspace/models/omx_smolvla_toponly_lora_20k_compat061 \
  --camera-layout top

bash /root/ros2_ws/src/open_manipulator/tools/run_omx_toponly.sh red rtc 15
```

호환 사본 생성은 한 번만 수행한다. 기존 출력 폴더는 덮어쓰지 않는다.
실행기는 C270 탑 카메라만 열고 손목 카메라는 확인하거나 열지 않는다.
640×480, 30 FPS, YUYV는 기존 C270 실행 설정을 유지한다.
제어 FPS는 세 번째 인자로 선택한다. 시간 제한은 없고 정상 종료 시 시작 자세 복귀를 요청한다.
다른 실행기가 장치를 사용 중이면 먼저 해당 실행을 종료해야 한다.
정적 테스트는 명령 구성과 설정 변환을 검증하며 실제 장치 동작이나 작업 성공을 보장하지 않는다.
