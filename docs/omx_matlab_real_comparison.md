# 실물 모델 실행과 tele 시연의 MATLAB 비교

## 목적과 비교 기준

11월 26일 red 실행은 사용자가 들어 올리기 실패로 확인한 사례다.
`Outcome="lift_failed"`는 이 관찰을 기록한 이름이며 소프트웨어가 실패를 자동 판정한 결과가 아니다.
정확한 실패 시점은 지정되지 않았으므로 기본 분석은 전체 모델 수행 구간이다.

두 비교를 구분한다.

1. 같은 실행 안에서 요청 목표·제한 후 전송 목표·실제 관절값을 시간순으로 확인한다.
2. 같은 색상 tele 시연과 실행 경로를 실제 경과시간 및 시간 비율 축에서 별도로 비교한다.

실제 관절을 읽은 다음 보낸 명령을 같은 행에서 빼지 않는다. 관절 읽기 구간 전체가
해당 명령 전송 완료/API 반환 이후이고 다음 명령 시작 이전인 관측만 목표 차이에 사용한다.
실패 전송이나 전송 중 겹친 관측은 불확실한 값으로 제외한다. 원위치 복귀는 `shutdown`이므로 제외한다.

## tele 참조 CSV 준비

원본 데이터셋·모델·영상은 변경하지 않는다. 저장 성공한 `attempt`와 에피소드/프레임 번호를
Parquet에 일대일 연결하고, 폐기된 재촬영 시도는 제외한다. 관절 읽기 시작·끝의 중점을 실측 시각으로
사용한다. 고정 `frame_index/30` 시간은 `dataset_timestamp_sec`로 별도 보존한다.

GPU Docker 내부 터미널에서 색상별 한 번만 수행한다. 기존 출력은 덮어쓰지 않는다.

```bash
/opt/lerobot_venv/bin/python -I \
  /root/ros2_ws/src/open_manipulator/tools/prepare_omx_tele_reference.py \
  --dataset-root /workspace/lerobot_datasets/omx_red_dualcam_20261124_081757_314161051 \
  --timing-path /workspace/record_timing/omx_red_dualcam_20261124_081757_314161051/record_timing.jsonl \
  --output-dir /workspace/validation_references/omx_red_dualcam_20261124_081757_314161051
```

출력에는 전체 20개 시연을 담은 `tele_reference.csv`, 에피소드별 길이·실측 Hz를 담은 `episodes.csv`,
출처·시간 기준을 담은 `tele_reference_metadata.json`이 있다. 에피소드 번호는 0부터 시작한다.

## MATLAB 실행

MATLAB Command Window(`>>`)에서 실행한다. 호스트 Bash 터미널에 입력하는 명령이 아니다.

```matlab
addpath("/home/parkjuwon/open_manipulator/matlab")
report = omx_compare_red_lift_failure(0);
```

`0`은 비교할 red 시연 에피소드 번호다. 가장 비슷한 시연을 자동 선택하는 기능은 아니다.
다른 번호는 `omx_compare_red_lift_failure(7)`처럼 지정한다.

분석 출력은 호스트가 쓸 수 있는 `matlab_logs/real_rollout/실행이름_분석시각/`에 저장된다.

| 파일 | 해석 |
| --- | --- |
| `command_response.png` | 실제 관절값, 실행 요청 목표, 관측 시점에 유효한 이전 전송 목표의 6관절 그래프 |
| `active_goal_gap.png` | 유효 전송 목표와 이후 관측값의 차이. 도·라디안 단위가 아님 |
| `tele_elapsed.png` | 시연과 모델 실행의 실제 경과시간 비교. 속도 차이를 그대로 표시 |
| `tele_progress.png` | 선택 구간의 시간을 0~100%로 늘이거나 줄인 비교 |
| `active_goal_gap.csv` | 시간상 연결 가능한 전송 목표·실제값·차이, 불확실한 곳은 NaN |
| `joint_summary.csv` | 유효 관측 수, 관측 시점 목표 차이 RMSE·최대값, 목표 제한 횟수 |
| `comparison_metadata.json` | 비교 출처·선택 구간·사용자 결과 라벨·가정 |

`SampledGoalGapRMSE`는 움직이는 목표와 이후 관측 사이의 동적 차이 통계다.
정상상태 제어 오차, 물체 위치 오차 또는 tele 시연 재현 오차로 해석하지 않는다.
관측 개수 기준 통계이므로 제어 주기가 불규칙하면 시간 가중 RMSE와는 다르다.
비교 metadata에 선택한 관측 수 대비 유효 목표 연결 비율을 함께 저장한다.
`active_action_row_zero_based`는 `control` 명령 테이블 내 0부터 시작하는 행 번호다.

## 실패 구간만 비교

영상과 관절 그래프를 확인한 뒤 구간을 지정할 수 있다. 아래 숫자는 사용법 예시이며
실제 들어 올리기 시점으로 확인된 값이 아니다.

```matlab
report = omx_compare_red_lift_failure(0, ...
    RunWindow=[5 9], TeleWindow=[3 6]);
```

RunWindow는 실행의 첫 관절 관측 중점부터 초, TeleWindow는 선택 시연 첫 관절 관측 중점부터 초다.
창을 자르기 전에 전체 명령과 관측을 연결하므로 창 시작 전 보낸 유효 목표도 유지한다.
각 구간의 첫 선택 샘플을 0으로 놓고 비교하며 실제 선택 길이를 그림 제목에 표시한다.

실행 영상 시각은 `video_frames.csv`에서 관측 번호로 연결한다. tele의 MP4는 에피소드들이
하나의 파일에 연속 저장돼 있을 수 있다. 영상 시작 오프셋과 `dataset_timestamp_sec`, 실측
`time_sec`를 연결해야 하며 영상의 재생 초를 그대로 실측 구간으로 복사하면 안 된다.

## 중요한 한계

- 관절값은 LeRobot 정규화 값이다. FK·손끝 XYZ·라디안 변환은 이번 구현에 포함하지 않는다.
- 수집 당시 캘리브레이션은 기존 데이터셋에 저장돼 있지 않다. 같은 장치·정규화·영점이라는
  가정을 확인해야 하며 프로그램도 이 가정을 경고한다. 실행 metadata의 캘리브레이션은 보존한다.
- 0~100%는 시간 비율이다. 집게 닫기·들어 올리기 같은 의미적 작업 단계 자동 정렬이 아니다.
  tele 경로 RMSE는 자동 계산하지 않는다. 경로가 다르다는 이유만으로 실패를 판정하지 않는다.
- episode 0을 고른 결과가 전체 80개 시연을 대표하지 않는다. 같은 지시문과 비슷한 초기 조건을
  가진 여러 시연 및 여러 모델 실행을 비교해야 일반적인 결론을 낼 수 있다.
- 이번 두 사례로 특정 학습 설정이 실패 원인이라고 확정할 수 없다.

## 검증

Python 참조 내보내기는 가상 Parquet과 시간 기록으로 저장 시도 선택·실측 시간·관절 이름 순서를 검증한다.
MATLAB 테스트는 합성 CSV로 명령 이전/이후 연결, 실패·겹침 제외, 종료 구간 제외, 잘못된 입력 거부 및
그래프 생성을 검증한다. 실제 로봇 실행이나 새 데이터 수집은 수행하지 않는다.
