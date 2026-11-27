# OMX 모델 실행 지연과 통신 오류 진단

`tools/diagnose_omx_rollout.py`는 LeRobot 0.6.1의 기본 동기 실행을 계측한다.
모델, 목표값, 제어 주기와 통신 오류 처리는 원래 실행기에 맡긴다.
예외를 무시하거나 통신 재시도를 추가하지 않는다. `--check-only`를 제외하면 실제 팔로워가 움직인다.

## 로봇을 움직이지 않는 검사

호스트 터미널에서 실행한다. 임시 폴더에 빈 CSV 두 개가 생성된다.

```bash
docker exec open_manipulator_gpu /opt/lerobot_venv/bin/python -I \
  /root/ros2_ws/src/open_manipulator/tools/diagnose_omx_rollout.py \
  --log-dir /tmp/omx_rollout_check --check-only
```

이미 검사에 사용한 폴더는 덮어쓰지 않는다. 다시 검사할 때는 다른 새 폴더를 지정한다.

## 실행 로그 수집

GPU 컨테이너의 같은 터미널에서 실행한다. 다른 로봇 제어 프로그램은 종료한 상태여야 한다.
학습 시 사용한 초기 자세와 카메라 배치를 맞춘다. 아래 명령은 실제 로봇을 움직인다.

```bash
OMX_DIAG_DIR="/workspace/rollout_logs/green_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OMX_DIAG_DIR"
set -o pipefail

/opt/lerobot_venv/bin/python -I -u \
  /root/ros2_ws/src/open_manipulator/tools/diagnose_omx_rollout.py \
  --log-dir "$OMX_DIAG_DIR" -- \
  --strategy.type=base \
  --inference.type=sync \
  --policy.path=/workspace/models/smolvla_omx_real_omit_episode_15 \
  --policy.device=cuda \
  --policy.use_amp=true \
  --device=cuda \
  --robot.type=omx_follower \
  --robot.port=/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00 \
  --robot.id=omx_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: '/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}" \
  --rename_map='{"observation.images.front":"observation.images.camera1"}' \
  --task="Pick up the green block and place it in the box." \
  --fps=30 \
  --duration=30 \
  --return_to_initial_position=false \
  --display_data=false \
  --play_sounds=false \
  2>&1 | tee "$OMX_DIAG_DIR/console.log"
```

저장되는 세 파일은 `console.log`, `timing.csv`, `timing_summary.csv`다.
호스트에서는 `docker/workspace/rollout_logs/` 아래에서 확인한다.
정상 종료와 Python 예외, 처리되는 Ctrl+C에서는 CSV를 정리한다.
강제 종료나 전원 차단 시 마지막 버퍼와 요약 파일은 저장되지 않을 수 있다.

## 측정값 해석

| operation | 의미 |
|---|---|
| control_interval | 연속된 관측 시작 사이 실제 시간. 첫 반복에는 없음 |
| observation | 관절 읽기와 최신 카메라 조회를 포함한 관측 취득 |
| motor_read | 모터 동기 읽기 호출의 경과시간 |
| motor_rx_id_번호 | 그룹 조회에서 해당 ID의 응답을 기다린 시간과 SDK 반환 코드 |
| camera_latest | 카메라 최신 버퍼 조회 시간. 센서 노출·영상 나이는 측정하지 않음 |
| policy_chunk | 새 Action 묶음을 요청한 반복의 전처리·예측·후처리 전체 시간 |
| policy_cached | 기존 묶음에서 다음 Action을 꺼낸 반복의 전처리·후처리 전체 시간 |
| send_action | 실제 전송 API 호출. 로봇이 목표 위치에 도착하는 시간은 아님 |
| motor_write | 모터 동기 쓰기 API 호출 시간. 모터 수신 확인을 뜻하지 않음 |

관측과 전송 측정은 내부 모터 측정을 포함하므로 모든 행의 시간을 합산하면 중복 계산된다.
같은 예외가 내부 호출과 상위 호출에 각각 기록될 수 있으므로 오류 행의 총합도
독립적인 통신 실패 횟수로 해석하지 않는다.
`motor_rx_id_14`의 타임아웃은 ID 14 응답을 기다리던 단계에서 실패했다는 뜻이다.
해당 ID의 모터 자체가 원인이라는 판정은 아니다. `COMM_숫자`는 SDK 통신 결과이고,
`PACKET_STATUS_0x숫자`는 응답 상태 바이트다. 진단기는 이 반환값을 바꾸거나 재시도하지 않는다.
`policy_chunk`는 모델 내부만의 실행 시간이 아니다. 현재 동기 실행기는 결과를 CPU로 옮긴 뒤
반환하므로, 이 측정은 GPU 결과 대기까지 포함한다. 별도의 CUDA 동기화는 추가하지 않는다.

`phase`는 초기화 `startup`, 실제 제어 `control`, 종료 `shutdown`으로 나눈다.
실행 중 지연은 우선 `control` 행으로 비교하고, 최초 추론의 준비 비용은 이후 추론과 구분한다.
30 Hz의 목표 간격은 약 33.3 ms다. `control_interval`이 자주 초과하는지 확인한다.

이 진단은 새 실행의 병목을 찾기 위한 것이다. 과거 시연의 실제 수집 주기나
카메라·관절의 물리적 시각 정렬을 소급해서 증명하지는 않는다.

## 추론을 제외한 동기 읽기 비교 검사

그룹 수신 실패가 추론 없이도 재현되는지 확인하려면 GPU 컨테이너에서 다음을 실행한다.
다른 로봇 제어 프로그램과 진단 실행기는 종료한 상태여야 한다.
이 검사는 위치 레지스터 132번의 4바이트를 조회하며 목표값·토크·모터 설정을 전송하지 않는다.
통신 결과 실패를 CSV에 기록하고 제한 시간 동안 다음 조회를 계속한다.
실패한 상태로 모델 제어를 계속하는 기능은 아니다.

```bash
OMX_PROBE_DIR="/workspace/rollout_logs/read_only_$(date +%Y%m%d_%H%M%S)"
/opt/lerobot_venv/bin/python -I -u \
  /root/ros2_ws/src/open_manipulator/tools/probe_omx_sync_read.py \
  --port /dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00 \
  --fps 30 --duration 30 --log-dir "$OMX_PROBE_DIR"
```

결과는 새 폴더의 `sync_read.csv`에 저장된다. 모터 상태를 변경하지 않으므로
이미 적용되어 있던 목표값이나 토크 상태를 초기화하는 기능도 없다.
`responses`는 조회 순서별 `ID:통신결과:응답상태`다.
이 검사에서 오류가 발생하면 VLM 계산 없이도 수신 실패가 재현된 것이다.
오류가 없더라도 한 번의 검사로 원인을 확정하지 않고 모델 실행 결과와 비교한다.

## 관측과 추론만 수행하는 비교 검사

동기 읽기만 정상이라면 위 실행 로그 수집 명령의 `--log-dir` 앞에
`--inference-only`를 추가하고 새 로그 폴더를 지정한다.
`--strategy.type=base`, `--inference.type=sync`,
`--return_to_initial_position=false`를 유지한다.

이 모드는 기존 보정값이 일치하는지 읽기로 확인하고 카메라를 연결하지만,
일반 연결의 모터 설정과 보정 쓰기는 수행하지 않는다.
기존 보정값이 일치하지 않으면 설정을 덮어쓰지 않고 종료한다.
모델이 예측한 Action은 `action_suppressed`로 기록하며 전송하지 않는다.
모터 레지스터 쓰기 API는 예외로 차단한다. 종료 시에도 토크 쓰기를 보내지 않고
포트를 닫는다. 따라서 기존 토크·목표 상태를 초기화하는 기능은 없다.

결과에서 `policy_chunk`, `policy_cached`, `motor_read`, `motor_rx_id_번호`를 비교한다.
`action_suppressed`는 예측값을 전송하지 않았다는 기록이며 모터 수신 확인이 아니다.
이 모드에서 수신 실패가 재현되면 목표값 전송 없이도 발생한 것이다.
실패가 없으면 다음 단계로 목표값 전송이 포함된 조건을 별도로 비교한다.

## 일반 초기화를 포함한 추론 비교 검사

`--inference-only`와 일반 모델 실행은 목표값 전송뿐 아니라 초기화 과정도 다르다.
이 차이를 분리하려면 앞선 실행 명령에서 `--inference-only`를
`--configured-inference-only`로 바꾸고 새 로그 폴더를 지정한다.
두 옵션은 동시에 사용할 수 없다. 기본 동기 실행과 초기 위치 복귀 비활성화는 유지한다.

이 모드는 원래 연결·보정·모터 설정·토크 활성화 및 종료 처리를 유지한다.
모델 Action은 전송하지 않고, 현재 OMX 모델들의 목표 위치 레지스터 주소 116 쓰기도
명시적으로 차단한다. 다른 설정 쓰기는 원래 값을 그대로 전달하고 주소별 시간을 기록한다.
**초기화 시 토크가 활성화되므로 기존 목표값과 상태에 따라 로봇이 움직일 수 있다.**
움직임이 없는 검사로 설명하거나 사용하지 않는다.

`register_addr_주소`는 실제 설정 쓰기 시간이며 `goal_write_blocked`는 예상하지 못한
목표 위치 쓰기가 요청되어 예외로 차단된 경우다. 일반 통신 실패도 그대로 전달한다.
이 검사가 실패하면 모델의 목표값 전송 없이도 일반 초기화를 포함한 조건에서 재현된 것이다.
정상이어도 초기화의 영향이 완전히 배제된 것은 아니며, 다음에는 일정한 목표값 전송과
모델이 예측한 목표값 전송을 비교한다. 한 번의 성공만으로 원인을 확정하지 않는다.

## 고정 목표값 반복 전송 검사

일반 초기화를 포함한 추론 검사가 정상이라면 옵션을 `--hold-initial-position`으로
바꿔 새 폴더에 기록한다. 이 모드는 일반 초기화·카메라·모델 추론·원래 통신 오류 처리를
유지하면서 모델 Action 대신 초기화 후 처음 관측한 6개 위치를 반복 전송한다.
초기화 시 토크 상태가 변경되며 위치 유지 목표값도 실제 전송하므로 움직임이 없다고 보장하지 않는다.
처음 읽은 자세를 유지하는 명령이므로 실행 전 원하는 시험 자세로 준비한다.

처음 관측한 위치는 `hold_target.json`에 저장한다. 이 값은 LeRobot의 보정된 위치 표현이며
ROS 관절 각도의 rad 값으로 해석하지 않는다. 이후 관측이 달라져도 목표는 처음 값으로 유지한다.
초기 위치가 없거나 명령 관절 이름이 다르면 전송 전에 중단한다.
`hold_action`은 고정 목표를 선택했다는 기록이며 실제 전송 시간은 `send_action`,
`motor_write`에서 확인한다.

여기서도 수신 실패가 발생하면 모델이 예측한 이동 목표값 없이도 읽기·쓰기 조합에서 재현된 것이다.
정상이면 모델이 예측한 목표값의 범위·변화량 및 전처리/후처리 정합성을 다음에 비교한다.
일회성 성공으로 원인을 확정하지 않는다.

## 모델 목표값 기록

모든 진단 모드는 첫 Action 호출부터 `actions.jsonl`도 기록한다.
`action_requested`에는 직전 관측 `state`, 제어 단계에 도달한 모델 목표 `target`,
현재 위치와의 차이 `delta_to_state`, 직전 요청과의 차이 `delta_to_previous`를 저장한다.
이 값들은 정규화 해제 후 LeRobot 로봇 위치 표현이며 rad 단위로 간주하지 않는다.
목표값은 전송 API 호출 전에 즉시 파일로 내보내므로 전송 도중 실패해도 마지막 요청을 확인할 수 있다.

`action_api_returned`는 API가 정상 반환했다는 뜻이며 모터 수신·도착 확인이 아니다.
전송 차단 모드에서도 이 행은 생긴다. `raw_goal_write_requested`는 주소 116으로 보내려는
SDK 정수 목표값이며 역시 실제 수신 확인이 아니다. 고정 목표 검사에서는 모델 요청과
실제 쓰기 요청이 다르므로 두 이벤트를 구분한다.

목표값만 확인하려면 `--inference-only`를 사용하고 `--duration=5`로 줄인다.
이 경우 모델의 목표값은 저장하지만 실제 목표·토크·모터 설정 쓰기는 차단된다.

## 관절 조회를 한 번 재시도하는 비교

기본값 `--read-retries 0`은 원래 조회 실패 정책을 유지한다.
같은 모델 실행에서 일시적인 수신 실패가 새 조회로 복구되는지 확인하려면
`--log-dir` 앞에 `--read-retries 1`을 추가하고 새 폴더를 사용한다.
기존 SDK의 `num_retry`를 `Present_Position` 조회에만 전달한다.
첫 조회에 실패하면 전체 그룹을 한 번 다시 요청하며, 마지막 시도도 실패하면 원래 예외로 중단한다.
이전 관절값이나 임의의 0을 대신 사용하지 않고 목표값 쓰기 재시도도 추가하지 않는다.

`read_recovered`는 내부 수신 실패 이후 위치 조회 API가 정상 반환한 경우다.
ID별 수신 실패 행과 함께 확인해야 한다. 재시도로 실행이 유지되어도
최초 수신 실패의 근본 원인이 해결되었다는 의미는 아니다.

## ID별 개별 위치 조회 비교

같은 모델 실행에 `--position-read-mode individual`을 추가하면 위치 조회만
ID별 요청·응답으로 바뀐다. 기본값은 `group`이며 다른 레지스터 조회와 목표값 전송은 유지한다.
`--read-retries 1`을 함께 사용하면 중간 ID에서 실패한 경우 처음 ID부터 한 묶음을 다시 조회한다.
실패한 묶음의 일부 값을 다음 묶음과 섞지 않으며 마지막 시도도 실패하면 중단한다.
각 ID 내부에서 별도로 재시도 횟수를 늘리지는 않는다.

`individual_read_id_번호`는 각 요청의 시간과 실패를 기록하고,
`individual_sweep_retry`는 관절 묶음의 재조회를 기록한다.
개별 조회는 각 관절의 취득 시각 차이와 요청 수를 늘리므로 원래 그룹 조회와 완전히 같은 부하는 아니다.
성공하면 그룹 요청·응답 방식과의 연관성을 지지하지만 원인이 해결되었다고 바로 판정하지 않는다.
비교 실행은 같은 시작 자세와 작업 배치에서 수행한다.

## 한 줄 실행과 집게 목표 고정

GPU 컨테이너에서 다음 한 줄을 실행하면 새 로그 폴더와 `console.log`가 자동 생성된다.
`mkdir`, `OMX_DIAG_DIR`, `set -o pipefail`을 따로 입력할 필요가 없다.

```bash
bash /root/ros2_ws/src/open_manipulator/tools/run_omx_diagnosis.sh --position-read-mode individual --read-retries 1 --freeze-gripper
```

이 실행은 실제 팔을 움직인다. 집게만 초기 위치 목표로 고정하며
ID 16의 조회와 목표값 전송은 제거하지 않는다. 나머지 5개 관절은 모델 목표를 사용한다.
`actions.jsonl`의 모델 요청과 API 반환·SDK 쓰기 요청을 구분해서 확인한다.
집게 초기 위치는 `frozen_gripper.json`에 저장한다.

기본 실행 시간은 10초다. 변경하려면 명령 앞에 `OMX_DIAG_DURATION=20`처럼 지정한다.
모델·장치 경로도 `OMX_MODEL_PATH`, `OMX_FOLLOWER_PORT`, `OMX_CAMERA_PATH` 환경변수로 변경할 수 있다.
부모 터미널의 일반 변수는 자동 전달되지 않으므로 앞에 지정하거나 `export`한다.
스크립트는 Python 또는 로그 저장 실패의 종료 코드를 유지한다.

## SDK 패킷 수신 오류 기록

같은 실행에 `--trace-packets`를 추가하면 최근 128개 송수신 이벤트를 메모리에 유지한다.
SDK `rxPacket`이 통신 실패 코드를 반환할 때만 `packet_errors.jsonl`에 최근 기록을 저장한다.
수신 실패가 없으면 이 파일은 생성하지 않는다. 모터 명령과 수신 결과는 변경하지 않는다.

`port_rx_bytes`는 시리얼 읽기가 실제 반환한 바이트 조각이며 패킷 단위와 일치하지 않을 수 있다.
`port_tx_request`는 전송 API에 전달한 바이트와 그 직전 수신 버퍼의 대기 바이트 수다.
`sdk_rx_result`의 패킷은 SDK 처리 후 결과이며 정상 수신에서는 byte stuffing 제거 후일 수 있다.
따라서 원래 바이트 검사에는 `port_rx_bytes`를 사용하고 SDK 결과와 구분한다.
각 이벤트는 256바이트까지만 저장하며 잘림 여부를 기록한다.
기록 자체의 추가 비용이 있으므로 세밀한 성능 측정값보다는 수신 실패 분석에 사용한다.

## 응답 대기 시간 비교

`--rx-timeout-ms 100`은 SDK가 요청마다 계산하는 응답 대기를 최소 100 ms로 늘린다.
원래 대기가 이보다 길면 줄이지 않는다. 요청 바이트·수신 판정·관절값은 변경하지 않는다.
관절 조회뿐 아니라 같은 SDK의 다른 응답 대기에도 적용되며 실행이 끝나면 원래 코드로 복원된다.
100 ms에서도 응답이 없으면 실패하고 제한된 재시도 후 중단한다.
따라서 실패 시 제어 대기가 더 길어질 수 있으며 영구 해결 설정으로 간주하지 않는다.

패킷 추적 시 `packet_errors.jsonl`에는 35 ms 이상 걸려 성공한 응답도
`trigger=slow_success`, `result=0`으로 저장한다. 이를 통해 원래 대기보다 늦게 도착한
응답이 실제 있었는지 구분한다. 계속 빈 수신으로 실패하면 짧은 대기만의 문제라는
가설을 지지하지 않는 결과다. 지연이 확인되지 않은 상태에서 대기를 계속 늘리지 않는다.
