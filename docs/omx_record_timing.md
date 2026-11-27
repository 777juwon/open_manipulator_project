# 실물 시연의 실제 수집 시각 검증

## 듀얼 카메라 수집 간편 명령

실행 위치: **Docker 내부 터미널**. 기존 Record, 모델 제어, 카메라 미리보기는 종료한다.

```bash
bash /root/ros2_ws/src/open_manipulator/tools/record_omx_dualcam.sh yellow 10 public
```

대상은 green/yellow/red/blue 블록 또는 white_tape(흰색 테이프), 개수는 양의 정수다.
white_tape는 Task를 `Pick up the white tape and place it in the box.`로 생성한다.
마지막 인자는 public(공개 업로드),
private(비공개 업로드), local(로컬만)이며 생략하면 public이다. Hub 업로드에는 해당
Docker의 HF 로그인과 쓰기 권한이 필요하다. 카메라 영상도 공개 대상에 포함된다.

기본 실행은 새 데이터셋과 시간 로그 이름을 생성하며 기존 OMX_RUN_NAME/OMX_TASK를
재사용하지 않는다. 640×480, 30fps, front(손목)/top(C270), 실제 시간 기록, MP4 읽기
권한 후처리를 유지한다.

### 기존 색상 데이터셋에 이어 수집

```bash
bash /root/ros2_ws/src/open_manipulator/tools/record_omx_dualcam.sh green 10 public \
  --resume-dataset omx_green_dualcam_20261116_112902
```

`10`은 이번에 추가할 개수다. 기존 10개라면 에피소드 10~19가 추가되어 총 20개가 된다.
기존 로컬 폴더와 동일한 Hub repo_id를 사용하고 `resume=true`로 기록한다. 기존 Task를
변경하지 않도록 시작 전에 Task와 카메라·관절 구성, FPS를 읽기 전용으로 검사한다.
시간 로그는 데이터셋 이름 뒤에 `_append_시각`을 붙인 새 폴더에 기록한다.
업로드는 기존 Hub 저장소를 갱신한다. 다른 노트북에서 해당 Hub 데이터를 수정했다면 먼저
최신 원본을 맞춘 뒤 실행하고, 같은 데이터셋을 여러 프로세스/노트북에서 동시에 기록하지 않는다.
파일 개수나 해시 전체를 검사하는 동시 편집 잠금 기능은 제공하지 않는다.
`--dry-run`에서는 실행 명령만 출력하고 실제 호환성 검사와 장치 연결은 하지 않는다.

수집 없이 설정만 확인하려면 마지막에 `--dry-run`을 붙인다.

```bash
bash /root/ros2_ws/src/open_manipulator/tools/record_omx_dualcam.sh red 10 public --dry-run
```

공통 설정 변경은 스크립트 기본값 또는 OMX_OWNER, OMX_FOLLOWER_PORT, OMX_LEADER_PORT,
OMX_WRIST_CAMERA, OMX_TOP_CAMERA, OMX_PYTHON, OMX_DATASET_BASE, OMX_TIMING_BASE 환경변수로
가능하다. 부모 터미널 변수를 자식 스크립트에 전달하려면 export 또는 명령 앞 할당을 사용한다.
시간 JSONL은 별도 로컬 폴더에 남으며 데이터셋 Hub 업로드에는 자동 포함되지 않는다.

장치 없는 회귀 테스트 4개에서 공개/비공개/로컬 옵션, 색상별 Task, 잘못된 입력 차단,
장치 누락 차단, 공백 경로 전달 및 종료 코드 보존을 확인했다. 실제 수집은 사용자가 실행한다.

기존 모델과 데이터셋은 비교 기준으로 보존한다. 처음에는 기존 성공 시연의 초기 자세,
카메라 방향, 블록 위치를 맞춰 기존 모델을 평가한다. 새 데이터는 별도 데이터셋으로
1개 시험 수집한 뒤 3~5개로 늘린다. 실제 시연은 사용자가 실행한다.

## 시간 로그를 함께 남기는 방법

실행 위치: **Docker 내부 터미널**. 기존 Record 설정 변수를 먼저 적용한다.
기존 기록 명령의 다음 부분만 교체하고 나머지 `--robot.*`, `--teleop.*`,
`--dataset.*` 옵션은 그대로 전달한다. 기존 dataset 이름을 덮어쓰지 않는다.

기존 시작 부분:

```bash
/opt/lerobot_venv/bin/python -I -m lerobot.scripts.lerobot_record \
```

교체할 시작 부분:

```bash
/opt/lerobot_venv/bin/python -I \
  /root/ros2_ws/src/open_manipulator/tools/record_omx_timed.py \
  --timing-dir "/workspace/record_timing/$OMX_RUN_NAME" \
  --readable-videos -- \
```

위 시작 부분만 실행하지 말고, 기존 Record의 모든 옵션을 뒤에 붙여 한 명령으로 실행한다.
LeRobot 0.6.1의 기존 기록 코드로 위임하며 센서값, 명령, 재시도 정책을 변경하지 않는다.
로그 폴더는 실행마다 새로 지정한다. 이어 수집도 시간 로그는 새 폴더를 사용한다.

`--readable-videos`는 일반 신규 파일에 umask 022를 사용하고, 에피소드 저장 및
Record 종료 후 이번 데이터셋의 MP4에 읽기 권한을 추가한다(0600 → 0644).
인코더가 0600을 명시하는 경우 umask만으로 해결되지 않아 자동 후처리를 사용한다.
파일 소유자와 쓰기 권한은 바꾸지 않고 외부로 연결된 심볼릭 링크는 처리하지 않는다.
SIGKILL이나 컨테이너 강제 종료 시에는 후처리 실행을 보장하지 않는다.

## 기록하는 항목

`record_timing.jsonl`에 동일 프로세스의 `perf_counter_ns` 시각을 기록한다.

- `session`: 단조 시각과 시스템 시각의 시작 기준.
- `buffered_frame`: 데이터셋 경로, episode_index, frame_index, 기록 시도 식별자 attempt.
- `observation_start_ns`, `observation_end_ns`: 로봇 관측 호출 구간.
- `bus_read_intervals_ns`: 관측 중 버스 조회 호출 구간. 관절별 센서 샘플링 시각은 아니다.
- `cameras`: 반환 이미지 객체와 연결한 카메라별 수신 시작/완료 및 전처리 완료 시각.
- `action_send_intervals_ns`: 목표 명령 API 호출 구간. 실제 모터 도착/동작 시각은 아니다.
- `add_start_ns`, `add_end_ns`: 데이터셋 프레임 추가 구간.
- `saved_episode`: 에피소드 저장 성공. 이 이벤트와 attempt가 일치하는 행만 최종 데이터와 연결한다.
- `observation_error`: 관측 예외. 원래 오류를 그대로 전달한다.
- `frame_timing_error`: 데이터 추가 전에 발견한 시간 연결 누락과 해당 카메라 정보를 기록한다.

재기록하거나 저장 전에 중단한 행은 최종 데이터셋에 포함되지 않을 수 있다.
에피소드 번호만으로 결합하지 말고 `saved_episode`와 attempt를 함께 확인한다.
이 도구는 LeRobot의 기본 에피소드 버퍼 저장 경로를 대상으로 한다.

## 알 수 있는 것과 한계

인접 행의 관측 시작 시각 차이로 실제 반복 간격과 평균 Hz를 계산할 수 있다.
관절 조회 구간과 해당 영상의 수신 구간을 비교해 호스트 기준 시간 차이를 확인한다.
`matched_frame=false`이면 영상 수신 시각을 추정하지 말고 결측으로 취급한다.
카메라 처리 스레드와 기록 스레드의 시각은 같은 단조 시계를 사용한다.

**수신 시각은 센서 노출 시각이 아니다.** 카메라/드라이버 버퍼 지연은 포함될 수 있으며
정확한 영상 노출과 관절 측정의 동시성을 증명하지 않는다. 개별 모터 측정 시각도
이 로그만으로 알 수 없다. 서로 다른 PC/프로세스의 단조 시각을 그대로 비교하면 안 된다.

기존 LeRobotDataset의 `timestamp=frame_index/fps`는 학습 호환성을 위해 유지한다.
시간 로그는 검증용 별도 파일이며 학습 입력에 자동으로 추가되지 않는다.
별도 `/workspace/record_timing` 폴더는 Dataset Hub 업로드에 자동 포함되지 않는다.
로컬 데이터셋과 시간 로그를 함께 보관하고 공유할 때도 두 폴더를 함께 전달한다.
로그 기록 자체의 처리 비용도 존재하므로 실제 수집 주기를 결과로 확인한다.

## 확인 상태

장치 없는 모의 회귀 테스트와 설치된 LeRobot 0.6.1의 훅 구성/복원 점검을 수행했다.
2026-11-12 첫 수집은 5개 에피소드 3,492프레임이며 실측 기록 주기는 약 29.9 Hz였다.
초기 코드가 `async_read`만 감싸고 OMX의 `read_latest`를 추적하지 않아 해당 수집의
카메라 시간 정보는 비어 있었다. 두 경로를 모두 추적하도록 수정하고 회귀 테스트를
추가했다. 수정 후 실제 카메라 시간 연결은 다음 1개 시험 수집에서 확인해야 한다.
기존 5개 시연의 관절 조회 및 반복 시각은 분석 가능하지만 영상 수신 시각은 복구할 수 없다.
카메라 시간 연결이 누락된 새 프레임은 데이터셋에 추가하기 전에 오류로 중단한다.
2026-11-16에는 객체 ID가 재사용되어 시각 캐시의 기존 키가 갱신될 때 정렬 순서가
갱신되지 않아 최신 정보가 오래된 항목으로 제거될 수 있는 버그를 재현하고 수정했다.
새 프레임뿐 아니라 기존 키 갱신도 캐시 끝으로 이동시킨다. 오류 시 해당 행의 카메라
연결 정보를 남기도록 보강했다. 당시 실패 행은 저장되지 않아 실제 중단이 이 경로였는지는
확정할 수 없으며, 수정 후 실제 수집 확인이 필요하다.
설치된 카메라의 실제 배경 읽기와 read_latest에 합성 프레임을 공급한 장치 없는
통합 테스트를 포함해 8개 테스트를 통과했다. 실제 수집 결과 확인은 별도로 필요하다.
