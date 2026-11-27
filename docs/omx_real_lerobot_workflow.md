# 실물 OMX 리더·팔로워와 손목 카메라: LeRobot 실행 안내

이 문서는 실물 OMX 두 팔과 USB RGB 카메라를 LeRobot 0.6.1로 직접 연결하고,
Teleoperation과 시연 데이터 기록을 재현하기 위한 안내다.
실행 명령은 사용자가 직접 입력한다. 명령 위치는 호스트와 Docker 내부로 구분한다.

이미 설치된 환경에서 바로 기록할 때의 최소 순서:

1. 4절의 명령으로 Docker에 진입한다.
2. 5절의 공통 설정을 적용한다.
3. 8절에서 작업 문장, 개수, 로컬 저장/Hub 업로드를 선택하고 Record를 실행한다.
4. `n` 또는 `q`로 기록을 마무리하고 10절에서 저장 위치를 확인한다.

친구의 최초 설치는 2~6절부터 진행한다. Rerun 시각화는 9절의 선택 항목이다.

## 1. 지금까지 확인한 결과

- 리더 모터 ID 1~6, 팔로워 모터 ID 11~16: 총 12개 통신 확인.
- 손목 카메라와 함께 리더·팔로워 Teleoperation 수행 확인.
- Rerun에서 카메라 영상과 관절 데이터 표시 확인. 종료 시 응답 없음도 발생했으므로
  기본 데이터 수집에서는 Rerun 표시를 끈다.
- 실물 데이터셋 `omx_real_20261110_080716`: 에피소드 1개, 1,795프레임,
  설정 30fps, H.264 MP4 및 Parquet 저장 확인.
- 해당 실행의 Hugging Face 업로드 완료 로그 확인.
- 저장된 작업 문장: `Pick up the block and place it in the box.`

이 결과는 현재 노트북에서 확인한 것이다. 친구 노트북의 새 설치와 실제 시연까지
시험한 것은 아니다. 파일 생성과 업로드만으로 물체 집기 성공이나 정확한 센서 동기화를
판정하지 않는다.

## 2. 필요한 환경과 연결

친구 노트북도 Ubuntu와 Docker Engine/Compose를 사용하는 조건이다.
호스트에 ROS 2를 따로 설치할 필요는 없으며, 아래 실물 LeRobot 명령에는
Gazebo, ROS 2 launch, `zenohd`, MATLAB용 Fast DDS 설정이 필요하지 않다.
Gazebo/MATLAB 실행은 [기존 시뮬레이션 안내](one_episode_matlab_validation.md)를 참고한다.

Docker가 없다면 [Ubuntu용 공식 설치 안내](https://docs.docker.com/engine/install/ubuntu/)에 따라 설치한다.
아래에서는 일반 사용자로 `docker` 명령을 실행할 수 있다고 가정한다.

| 장치 | 연결 |
|---|---|
| 리더 OMX-L | 노트북의 데이터 통신 가능한 USB 포트 |
| 팔로워 OMX-F | 노트북 USB + OMX 키트에 맞는 외부 전원 |
| 손목 카메라 | 손목에 고정하고 USB 케이블은 노트북에 연결 |

USB 포트가 부족하면 데이터 통신을 지원하는 허브를 사용한다.
카메라와 두 OpenRB가 실제로 인식되는지는 아래 장치 목록으로 확인한다.
전원 배선을 조정할 때는 팔을 지지하고 외부 전원과 USB 전원을 모두 분리한다.
팔로워에는 전압 조건이 다른 XL430과 XL330이 섞여 있으므로 키트의 전원 변환 경로를 유지한다.

현재 확인한 버전은 다음과 같다.

| 패키지 | 버전 |
|---|---|
| LeRobot | 0.6.1 |
| Dynamixel SDK | 3.8.4 |
| pyserial | 3.5 |
| Rerun SDK/Viewer | 0.33.1 |
| huggingface-hub | 1.33.0 |
| setuptools | 79.0.1 |
| PyAV | 15.1.0 |

## 3. 친구 노트북의 최초 Docker 준비

이미 프로젝트와 `open_manipulator` 컨테이너가 있다면 이 절의 새 빌드는 생략하고
4절의 Docker 진입부터 진행한다. 아래 명령은 새 환경의 최초 준비용이다.

실행 위치: **호스트 터미널** (`사용자@노트북:~$`).

```bash
cd ~
git clone https://github.com/777juwon/open_manipulator_project.git open_manipulator
cd ~/open_manipulator/docker
touch /tmp/.docker.xauth
docker compose build
docker compose up -d
```

이 프로젝트의 Dockerfile은 ROS 패키지와 기본 LeRobot 의존성도 설치하므로 최초 빌드에
시간이 걸린다. 빌드 오류는 먼저 해결한 뒤 진행한다.
기존 컨테이너를 사용 중일 때 `container.sh start`를 반복 실행하면 이미지 pull과
컨테이너 교체가 발생할 수 있다. 이후 일상적인 진입은 `docker exec`를 사용한다.

호스트의 프로젝트 폴더와 Docker 내부 경로는 다음처럼 연결된다.

| 호스트 | Docker 내부 |
|---|---|
| `~/open_manipulator/` | `/root/ros2_ws/src/open_manipulator/` |
| `~/open_manipulator/docker/workspace/` | `/workspace/` |

## 4. 실물용 의존성 설치와 Docker 진입

실행 위치: **호스트 터미널**.

```bash
docker exec -it open_manipulator bash
```

이후 프롬프트가 `root@...:~/ros2_ws#`이면 Docker 내부다.

실행 위치: **Docker 내부 터미널**, 최초 한 번.

```bash
if [ ! -x /opt/lerobot_venv/bin/python ]; then
  python3 -m venv --system-site-packages /opt/lerobot_venv
fi
/opt/lerobot_venv/bin/python -I -m pip install -r /root/ros2_ws/src/open_manipulator/open_manipulator_lerobot/requirements.txt
/opt/lerobot_venv/bin/python -I -m pip install "lerobot[dynamixel]==0.6.1" "dynamixel-sdk==3.8.4" "pyserial==3.5" "rerun-sdk==0.33.1" "huggingface-hub==1.33.0" "setuptools==79.0.1"
/opt/lerobot_venv/bin/python -I -m pip check
```

`No broken requirements found.`를 확인한다. `setuptools<80`은 현재 ROS 빌드 도구와의
호환성을 위한 조건이다. 이 추가 설치는 현재 컨테이너에 적용된다. 컨테이너를 삭제하고
재생성했다면 다시 설치해야 한다. `/workspace`에 저장한 데이터는 호스트에 남는다.

새 Docker 터미널을 열 때마다 다음 경로 설정을 적용한다.

```bash
export PATH="/opt/lerobot_venv/bin:$PATH"
```

명령에서 사용하는 `python -I`는 ROS의 `PYTHONPATH` 영향을 줄여 가상환경의
Dynamixel SDK를 사용하기 위한 것이다. Python을 절대 경로로 실행하더라도,
Rerun Viewer 같은 별도 실행 파일을 찾으려면 위 `PATH` 설정이 필요하다.

## 5. 장치 확인과 공통 설정

실행 위치: **호스트 터미널**.

```bash
lsusb
```

같은 팔 두 대를 사용한다면 `ROBOTIS OpenRB-150` 두 개가 보여야 한다.
현재 사용한 외장 카메라의 USB ID는 `32e4:9230`, 이름은 `HD USB Camera`다.
`30c9:0069 HP Wide Vision HD Camera`는 기존 노트북의 내장 카메라다.
친구의 다른 카메라는 이름과 USB ID가 달라질 수 있다.

실행 위치: **Docker 내부 터미널**.

```bash
ls -l /dev/serial/by-id/
ls -l /dev/v4l/by-id/
```

`ttyACM0`, `ttyACM1`, `video0`, `video2` 번호는 연결 순서에 따라 달라진다.
이후에는 `by-id` 경로를 사용한다. 같은 실물 팔을 친구 PC에 옮기면 시리얼 ID는 유지되지만,
다른 팔이나 카메라를 사용하면 아래 값을 실제 조회 결과로 수정해야 한다.
시리얼 포트를 모를 때는 리더와 팔로워를 한 대씩 연결해 구분한다.

다음 공통 설정을 **명령을 실행할 Docker 터미널마다** 적용한다.

```bash
export PATH="/opt/lerobot_venv/bin:$PATH"
export OMX_LEADER_PORT="/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_57A9BF7F50323952582E3120FF073125-if00"
export OMX_FOLLOWER_PORT="/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_06D5BF5150323952582E3120FF070A27-if00"
export OMX_CAMERA_PATH="/dev/v4l/by-id/usb-HD_USB_Camera_HD_USB_Camera-video-index0"
```

사용한 카메라는 `640×480`, `30fps`, `YUYV`로 실제 프레임 수신을 확인했다.
다른 카메라는 지원 모드를 먼저 확인한다. 여기서 `front`는 카메라 이름이며 손목에
부착해도 사용할 수 있다. 수집과 모델 실행에서 같은 이름을 유지한다.

## 6. 카메라와 모터 통신 확인

### 카메라 확인

실행 위치: **Docker 내부 터미널**.

```bash
/opt/lerobot_venv/bin/python -I -m lerobot.scripts.lerobot_find_cameras opencv \
  --output-dir /workspace/camera_check \
  --record-time-s 2
```

확인 이미지는 호스트의 `~/open_manipulator/docker/workspace/camera_check/`에 생긴다.
카메라 탐색 프로그램이 종료된 뒤 다음 명령을 실행한다.

영상 창만 보려면 아래 9절의 화면 권한을 허용한 뒤, Docker 내부에서 실행한다.

```bash
ffplay -hide_banner -loglevel warning \
  -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 30 \
  -window_title "OMX Camera" "$OMX_CAMERA_PATH"
```

카메라 창에서 `q`로 종료한다. `ffplay`는 미리보기이며 데이터셋을 저장하지 않는다.
Record/Teleoperation을 시작하기 전에는 이 창을 종료해서 카메라 사용을 해제한다.

### 총 12개 모터 Ping 검사

다른 로봇 제어 프로그램을 종료한 상태에서 실행한다.
이 코드는 Ping 응답과 모델 번호를 조회하며 이동·토크 변경 명령을 보내지 않는다.

실행 위치: **Docker 내부 터미널**, 5절의 공통 설정 적용 후.

```bash
/opt/lerobot_venv/bin/python -I - <<'PY'
import os
from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

buses = [
    ("리더", os.environ["OMX_LEADER_PORT"],
     {1: 1200, 2: 1200, 3: 1200, 4: 1200, 5: 1200, 6: 1190}),
    ("팔로워", os.environ["OMX_FOLLOWER_PORT"],
     {11: 1060, 12: 1060, 13: 1060, 14: 1200, 15: 1200, 16: 1200}),
]
packet = PacketHandler(2.0)
all_ok = True
for label, path, motors in buses:
    print(f"\n[{label}]")
    port = PortHandler(path)
    try:
        if not port.openPort() or not port.setBaudRate(1000000):
            raise RuntimeError("통신 포트 또는 통신 속도 설정 실패")
        for motor_id, expected in motors.items():
            model, result, error = packet.ping(port, motor_id)
            ok = result == COMM_SUCCESS and error == 0 and model == expected
            all_ok = all_ok and ok
            print(f"ID {motor_id}: {'PASS' if ok else 'FAIL'}, 모델={model}")
            if result != COMM_SUCCESS:
                print(packet.getTxRxResult(result))
            elif error:
                print(packet.getRxPacketError(error))
    except Exception as error:
        all_ok = False
        print(f"FAIL: {error}")
    finally:
        port.closePort()
print("\n전체 통신 검사:", "PASS" if all_ok else "FAIL")
raise SystemExit(0 if all_ok else 1)
PY
```

리더 6개와 팔로워 6개, 총 12개다. `16`은 팔로워 그리퍼의 ID이며 모터 개수가 아니다.
전원과 배선은 공식 OMX 조립 안내에 따라 확인한다.

## 7. 카메라 없이 Teleoperation 확인

실행 위치: **Docker 내부 터미널**, 공통 설정 적용 후.
연결 초기화 중 팔로워나 리더 그리퍼가 움직일 수 있으므로 팔을 고정하고 공간을 확보한다.
팔로워 관절을 손으로 강제로 움직이지 않고 리더를 작게 조작한다.

```bash
/opt/lerobot_venv/bin/python -I -m lerobot.scripts.lerobot_teleoperate \
  --robot.type=omx_follower \
  --robot.port="$OMX_FOLLOWER_PORT" \
  --robot.id=omx_follower_arm \
  --teleop.type=omx_leader \
  --teleop.port="$OMX_LEADER_PORT" \
  --teleop.id=omx_leader_arm \
  --fps=30 \
  --display_data=false
```

종료는 이 명령을 실행한 터미널에서 `Ctrl+C`를 누르고 프롬프트 복귀를 확인한다.
종료 시 모터 유지 힘이 해제될 수 있으므로 팔을 안정적으로 지지한다.
Teleoperation은 영상 파일이나 학습용 데이터셋을 자동 저장하지 않는다.

## 8. 데이터셋 기록: 기본 경로

Record 하나가 리더 입력, 팔로워 제어, 카메라 기록을 함께 처리한다.
기존 Teleoperation과 카메라 미리보기 프로그램은 먼저 종료한다.
Rerun 창 없이 녹화해도 MP4는 저장된다.

### 저장과 작업 문장 설정

실행 위치: **Docker 내부 터미널**, 공통 설정 적용 후.

```bash
OMX_RUN_NAME="omx_real_$(date +%Y%m%d_%H%M%S)"
OMX_DATASET_ROOT="/workspace/lerobot_datasets/$OMX_RUN_NAME"
OMX_TASK="Pick up the block and place it in the box."
OMX_EPISODES=1
OMX_OWNER="local"
OMX_PUSH_TO_HUB=false
OMX_RESUME=false
```

- `OMX_TASK`는 실제 시연 내용에 맞게 수정한다. 위 예시는 블록을 집어 상자에 넣기다.
- 이 Record 실행에서 저장하는 에피소드에는 동일한 작업 문장이 붙는다.
- 처음은 1개, 이후에는 `OMX_EPISODES=5` 또는 `10`으로 설정할 수 있다.
- `OMX_RUN_NAME`을 새로 만들면 별도 데이터셋이 된다. 기존 기록에 추가하려면 10절을 따른다.
- 위 날짜는 Docker의 시간대를 따른다. 기존 성공 사례의 폴더 시각은 UTC였다.

### Hugging Face 업로드를 원할 때만 로그인

로컬 저장은 로그인이 필요 없다. 업로드하려면 같은 Docker 안에서 로그인한다.

```bash
/opt/lerobot_venv/bin/hf auth login
/opt/lerobot_venv/bin/hf auth whoami
```

토큰 입력이 필요하면 로그인 입력창에만 입력한다. 토큰을 채팅, 문서, Git 또는 명령
문자열에 넣지 않는다. 업로드에는 해당 데이터셋의 쓰기 권한이 필요하다.
Git 자격 증명 추가는 Hub API 업로드의 필수 조건이 아니다.

`whoami` 출력에서 확인한 **친구 본인 계정명**으로 다음 값을 변경한다.
예시 문자열을 실제 계정명으로 바꾸기 전에는 업로드 명령을 실행하지 않는다.

```bash
HF_USER="본인_Hugging_Face_계정명"
OMX_OWNER="$HF_USER"
OMX_PUSH_TO_HUB=true
```

현재 CLI의 `whoami` 첫 줄은 `✓ Logged in`이므로 문서의 오래된 `awk` 첫 줄 추출 예시를
그대로 쓰지 않는다. `${HUGGINGFACE_TOKEN}`는 환경변수 참조 문법이다.
실제 토큰 문자열을 `${...}` 안에 넣는 방식이 아니다.

### Record 실행

실행 위치: **설정을 입력한 같은 Docker 내부 터미널**.
Rerun은 실행하지 않아도 된다. 아래는 로컬 저장과 선택적 비공개 업로드를 수행한다.

```bash
printf '데이터셋: %s/%s\n저장 경로: %s\n' "$OMX_OWNER" "$OMX_RUN_NAME" "$OMX_DATASET_ROOT"
/opt/lerobot_venv/bin/python -I -m lerobot.scripts.lerobot_record \
  --robot.type=omx_follower \
  --robot.port="$OMX_FOLLOWER_PORT" \
  --robot.id=omx_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: '$OMX_CAMERA_PATH', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}" \
  --teleop.type=omx_leader \
  --teleop.port="$OMX_LEADER_PORT" \
  --teleop.id=omx_leader_arm \
  --dataset.repo_id="$OMX_OWNER/$OMX_RUN_NAME" \
  --dataset.root="$OMX_DATASET_ROOT" \
  --dataset.single_task="$OMX_TASK" \
  --dataset.num_episodes="$OMX_EPISODES" \
  --dataset.fps=30 \
  --dataset.episode_time_s=60 \
  --dataset.reset_time_s=60 \
  --dataset.video=true \
  --dataset.streaming_encoding=true \
  --dataset.rgb_encoder.vcodec=h264 \
  --dataset.rgb_encoder.preset=fast \
  --dataset.encoder_threads=2 \
  --dataset.push_to_hub="$OMX_PUSH_TO_HUB" \
  --dataset.private=true \
  --dataset.no_stamp=true \
  --resume="$OMX_RESUME" \
  --display_data=false \
  --play_sounds=false
```

### 시연, 에피소드 완료, 전체 종료

| 표시 또는 조작 | 의미 |
|---|---|
| `Recording episode ...` | 리더로 작업을 시연하는 기록 구간 |
| `n` 또는 `→` | 현재 기록 구간 또는 준비 구간을 일찍 끝내기 |
| `Reset the environment` | 물체와 시작 자세를 수동으로 준비하는 구간. 기록 대상 아님 |
| `r` 또는 `←` | 현재 시연을 취소하고 다시 기록 |
| `q` 또는 `Esc` | 전체 수집을 종료하고 저장과 설정된 업로드 마무리 |

실행 중인 Docker 터미널을 선택한 상태에서 키를 누른다. 최대 60초가 지나도 다음 구간으로 넘어간다.
설정한 개수를 기록하면 자동 종료한다. 중도 종료할 때는 미완료 시연이 포함될 수 있다.
정상 기록 종료에는 위 키를 사용하고 `Exiting`과 셸 프롬프트 복귀까지 기다린다.

## 9. 선택: Rerun으로 영상을 보면서 Teleoperation

이 절은 화면 표시가 필요할 때만 사용한다. 데이터셋 자동 저장 명령은 아니다.

실행 위치: **호스트 터미널 A**.

```bash
xhost +SI:localuser:root
docker exec -it -e DISPLAY="$DISPLAY" open_manipulator /opt/lerobot_venv/bin/rerun
```

Rerun 창과 `Listening ... 9876` 로그를 확인한 뒤 그 창을 켜둔다.
처음에는 데이터가 없으므로 빈 창이 정상이다. `xhost` 허용은 데스크톱 세션이 바뀌면
다시 적용해야 할 수 있다. 녹화만 할 때는 이 권한 설정이 필요하지 않다.

실행 위치: **호스트 터미널 B**, Docker 진입.

```bash
docker exec -it -e DISPLAY="$DISPLAY" open_manipulator bash
```

이 새 Docker 터미널에 5절의 공통 설정을 적용하고 실행한다.

```bash
/opt/lerobot_venv/bin/python -I -m lerobot.scripts.lerobot_teleoperate \
  --robot.type=omx_follower \
  --robot.port="$OMX_FOLLOWER_PORT" \
  --robot.id=omx_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: '$OMX_CAMERA_PATH', width: 640, height: 480, fps: 30, fourcc: 'YUYV'}}" \
  --teleop.type=omx_leader \
  --teleop.port="$OMX_LEADER_PORT" \
  --teleop.id=omx_leader_arm \
  --fps=30 \
  --display_data=true \
  --display_ip=127.0.0.1 \
  --display_port=9876
```

Rerun에 카메라 영상과 관절 데이터가 표시된다. 종료 순서는 Teleoperation 터미널에서
`Ctrl+C`, 프롬프트 복귀 확인, 마지막으로 Rerun 창 닫기 순서다.
Viewer를 먼저 닫았을 때 전송 대기에 걸린 사례가 있었다.

## 10. 저장 결과와 이어 수집

호스트 저장 위치는 다음과 같다. 프로젝트를 다른 위치에 받았다면 그 위치를 기준으로 찾는다.

```text
~/open_manipulator/docker/workspace/lerobot_datasets/<OMX_RUN_NAME>/
├── videos/observation.images.front/   # H.264 MP4
├── data/                            # 관절 상태·행동 명령 Parquet
└── meta/                            # 작업 문장, 에피소드 정보, 통계
```

실행 위치: **Docker 내부 터미널**, 기록 후 같은 설정이 남아 있는 상태.

```bash
/opt/lerobot_venv/bin/python -I - "$OMX_DATASET_ROOT" <<'PY'
import json
import sys
from pathlib import Path
import pyarrow.parquet as pq

root = Path(sys.argv[1])
info = json.loads((root / "meta/info.json").read_text())
print("저장 경로:", root)
print("에피소드 수:", info["total_episodes"])
print("프레임 수:", info["total_frames"])
print("설정 FPS:", info["fps"])
print("작업 목록:", pq.read_table(root / "meta/tasks.parquet").to_pylist())
for video in sorted((root / "videos").rglob("*.mp4")):
    print("영상:", video)
PY
```

한 에피소드가 반드시 MP4 한 파일에 대응하는 것은 아니다. 에피소드 구간은 메타데이터로 구분한다.
실물 기본 관절 값은 LeRobot의 정규화 값이며 Gazebo ROS의 rad 값과 같지 않다.
기존 Gazebo 전용 품질 검사와 MATLAB FK에 적용하려면 이름·단위·카메라 항목을 맞춰야 한다.

같은 터미널에서 같은 데이터셋에 5개를 추가하려면 다음 두 값만 바꾸고 8절 Record 명령을 다시 실행한다.

```bash
OMX_EPISODES=5
OMX_RESUME=true
```

이때 `OMX_RUN_NAME`, `OMX_OWNER`, `OMX_DATASET_ROOT`, 카메라 설정을 기존 값으로 유지한다.
새 터미널에서는 기존 이름과 경로를 직접 복원한다. `num_episodes`는 추가할 개수이므로
기존 1개에 5개를 추가하면 총 6개다. 작업 문장을 바꾸면 새로 기록하는 에피소드에 적용된다.

## 11. 발생했던 오류와 대응

| 증상 | 확인된 원인 또는 대응 |
|---|---|
| `hf: command not found` | 호스트에 명령이 없었다. Docker 내부에서 `/opt/lerobot_venv/bin/hf` 사용 |
| `Failed to find Rerun Viewer executable in PATH` | 가상환경의 `bin` 경로를 `PATH`에 추가 |
| `Failed to open connection to X server` | 현재 호스트 데스크톱에서 root 화면 접근 허용 후 `DISPLAY`를 전달 |
| Viewer 종료 후 `Sender has been blocked ...` | 수신 창 종료 후 전송이 대기했다. 기록은 `display_data=false`로 진행 |
| 모델 번호가 맞지만 Hardware Error로 FAIL | 모터 통신은 되었으나 내부 오류가 보고됨. 전원 문제로 단정하지 않고 오류 상태 조회 |
| ID 16만 `There is no status packet` | 당시에는 케이블 접촉 문제를 해결한 뒤 PASS. 다른 사례도 같은 원인이라고 단정하지 않음 |
| USB 번호 변경 | `ttyACM*`, `video*` 대신 실제 `by-id` 경로 사용 |

### XL430 ID 11~13 하드웨어 오류 조회

다른 제어 프로그램을 종료한 상태에서 Docker 내부에서 실행한다.
조회만 수행하고 모터 설정, 토크, 목표 위치, 재부팅을 변경하지 않는다.

```bash
/opt/lerobot_venv/bin/python -I - <<'PY'
import os
from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler

port = PortHandler(os.environ["OMX_FOLLOWER_PORT"])
packet = PacketHandler(2.0)
labels = {1: "전압 오류", 4: "과열", 8: "엔코더 오류", 16: "전기적 오류/전원 부족", 32: "과부하"}

def read(motor_id, address, size):
    method = packet.read1ByteTxRx if size == 1 else packet.read2ByteTxRx
    value, result, error = method(port, motor_id, address)
    if result != COMM_SUCCESS:
        raise RuntimeError(packet.getTxRxResult(result))
    # 하드웨어 경고 비트가 있어도 읽기 응답 데이터는 확인한다.
    if error & 0x7F:
        raise RuntimeError(packet.getRxPacketError(error & 0x7F))
    return value

try:
    if not port.openPort() or not port.setBaudRate(1000000):
        raise RuntimeError("팔로워 통신 포트 열기 실패")
    for motor_id in (11, 12, 13):
        try:
            status = read(motor_id, 70, 1)
            voltage = read(motor_id, 144, 2) / 10
            temperature = read(motor_id, 146, 1)
            errors = [label for mask, label in labels.items() if status & mask]
            print(f"ID {motor_id}: 오류=0x{status:02X}, 전압={voltage:.1f}V, 온도={temperature}도, {errors or ['오류 없음']}")
        except Exception as error:
            print(f"ID {motor_id}: 조회 실패: {error}")
finally:
    port.closePort()
PY
```

당시 재조회에서는 세 모터 모두 오류 `0x00`, 전압 `11.6V`, 온도 `39~42°C`였다.
이 수치가 다른 시점의 정상 상태까지 보장하지는 않는다.

### 종료되지 않는 프로세스

실행 위치: **호스트 터미널**.

```bash
docker exec open_manipulator ps -eo pid,stat,args
```

목록에서 문제가 된 Teleoperation의 실제 PID를 찾는다. 아래 예시 문자열을 숫자로 바꾸고
명령이 맞는지 다시 확인한다. 과거의 PID `245`, `659`를 그대로 사용하지 않는다.

```bash
OMX_PID="실제_PID로_교체"
docker exec open_manipulator ps -p "$OMX_PID" -o pid,stat,args
docker exec open_manipulator kill -TERM "$OMX_PID"
docker exec open_manipulator ps -p "$OMX_PID" -o pid,stat,args
```

동일한 프로세스가 계속 남고 종료할 대상이 확실할 때만 다음 강제 종료를 사용한다.

```bash
docker exec open_manipulator kill -KILL "$OMX_PID"
docker exec open_manipulator ps -p "$OMX_PID" -o pid,stat,args
```

프로세스 행이 없어져야 종료된 것이다. `do_coredump` 대기가 확인된 사례도 있었으며,
계속 남는다면 반복 실행 대신 상태를 확인한다. 강제 종료는 녹화 마무리와 토크 해제를
보장하지 않는다. 팔을 지지하고 기존 제어 프로그램이 끝나기 전에는 Record를 중복 실행하지 않는다.

## 12. Instruction과 학습 데이터 관리

`--dataset.single_task`는 녹화 시작 전에 정한다. 수집 중 실제 동작은 사용자가 리더로 시연하고,
문장은 그 시연의 설명으로 저장된다. 모델 학습에서 영상·로봇 상태·작업 문장을 입력으로,
기록한 행동을 학습 대상으로 사용한다.

물체 위치만 달라도 작업이 같다면 같은 문장을 유지할 수 있다. 작업이 달라지면 문장도 맞춘다.
단순 관절 테스트에 집기 작업 문장을 붙인 기록은 집기 학습에 그대로 섞지 않는다.
사후 수정은 공식 Dataset Tools로 가능하지만, 로컬과 Hub 복사본의 수정 내용을 함께 관리한다.

## 13. 공식 참고 문서

- [LeRobot 0.6.1 OMX](https://huggingface.co/docs/lerobot/v0.6.1/en/omx)
- [데이터 기록과 키보드 조작](https://huggingface.co/docs/lerobot/v0.6.1/en/il_robots#record-a-dataset)
- [OMX 전용 예시](https://github.com/huggingface/lerobot/blob/v0.6.1/examples/omx/README.md)
- [카메라 설정](https://huggingface.co/docs/lerobot/v0.6.1/en/cameras)
- [작업 문장 등 데이터셋 편집](https://huggingface.co/docs/lerobot/v0.6.1/en/using_dataset_tools)
- [Hugging Face 로그인](https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login)
- [XL430 상태 레지스터](https://emanual.robotis.com/docs/en/dxl/x/xl430-w250/)
