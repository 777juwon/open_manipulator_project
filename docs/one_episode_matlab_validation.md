# One-Episode Gazebo and MATLAB Validation

## 실행 위치와 저장 오류 복원

- `parkjuwon@...$` 프롬프트는 노트북의 호스트 터미널이다. `docker exec` 명령은 여기서 실행한다.
- `root@...#` 프롬프트는 Docker 내부 터미널이다.
- MATLAB의 `>>` 프롬프트는 Command Window이다. `addpath`, `load`, `parquetread`와 아래 MATLAB 코드는 여기서 실행한다.
- MATLAB 코드를 Bash 터미널에 입력하면 괄호 근처에서 문법 오류가 발생한다. 설치 실패가 아니라 실행 위치가 다른 것이다.

MATLAB이 열려 있지 않다면 호스트 터미널에서 다음 명령으로 실행한다.

```bash
/home/parkjuwon/MATLAB/R2026a/bin/matlab
```

`Stop and Save` 이후 MAT만 남고 `table`의 `VariableNames` 오류가 발생했던 경우,
이미 기록한 데이터를 다시 시연할 필요 없이 복원할 수 있다. **MATLAB의 `>>` 창**에서 실행한다.

```matlab
addpath("/home/parkjuwon/open_manipulator/matlab")
paths = export_omx_joint_log("/실제/경로/joint_log_날짜.mat");
report = analyze_omx_joint_log(paths.csv_path);
```

복원 함수는 원본 숫자를 유지하며 CSV와 PNG를 만들고 MAT에 `ee_xyz`를 추가한다.
파일명과 경로는 본인이 기록한 MAT 파일로 바꾼다. 그래프와 CSV가 생성됐는지 함수 출력과 파일 목록으로 확인한다.

MAT를 숫자 표로 보려면 MATLAB의 `>>` 창에서 다음 명령을 실행한다.

```matlab
S = load("/실제/경로/joint_log_날짜.mat");
Q = S.joint_log;
openvar("Q")
```

각 행은 기록 시점이고 열 1부터 5는 `joint1`부터 `joint5`이다.
이번 데이터셋의 관절 배열은 MATLAB R2026a의 `parquetread`에서 지원하지 않는 형식으로 확인됐다.
파일이 손상된 것은 아니다. Docker의 PyArrow로 숫자를 펼친 보기용 CSV를 만들 수 있다.
다음 명령은 **호스트 터미널**에서 실행하며 원본 데이터셋은 변경하지 않는다.

```bash
run=omx_f_validation_1_20261014_052600_1950
docker exec open_manipulator /opt/lerobot_venv/bin/python \
  /root/ros2_ws/src/open_manipulator/tools/export_omx_dataset_csv.py \
  --dataset-root "/workspace/lerobot_datasets/$run" \
  --csv-out "/workspace/lerobot_datasets/$run/dataset_preview.csv"
```

`run`은 확인할 데이터셋 폴더 이름으로 바꾼다. 생성된 CSV는
`docker/workspace/lerobot_datasets/<run>/dataset_preview.csv`에서 확인한다.
`state_joint1` 등의 열은 실제 관절값이고 `action_joint1` 등의 열은 목표 명령이다.
CSV 변환은 보기 편하게 펼치는 작업이지 새로운 학습 데이터셋을 만드는 과정은 아니다.

현재 MATLAB 기능은 실제 관절값과 FK 경로를 기록하고 범위를 요약한다.
목표 명령을 수신해 추종 오차를 계산하는 기능은 아니다. 목표 대비 실제 응답은 기존 Simulink 모델에서 별도로 비교한다.
데이터셋의 Action은 동작 끝의 목표 관절값이므로 이동 중 실제 관절값과 다를 수 있다.
MP4는 모델이 보는 화면이다. 큐브와 상자가 보이는지, 누락되거나 멈춘 화면이 없는지 확인해야 한다.

아래 합성 데이터 회귀 테스트는 ROS에 연결하거나 로봇을 움직이지 않는다.

```matlab
results = runtests("/home/parkjuwon/open_manipulator/tests");
assertSuccess(results);
```

This workflow runs the existing automatic collector for one successful episode
and observes joint motion independently in MATLAB. It does not train or run
SmolVLA. Run the terminal commands yourself; no Git operation is required.

## Communication Settings

The container normally selects Domain 30 and Zenoh. MATLAB uses Fast DDS by
default, and this project's logger uses Domain 0. Every participating process
must be started with compatible settings before creating ROS 2 nodes:

| Setting | Value | Purpose |
| --- | --- | --- |
| `ROS_DOMAIN_ID` | `0` | Put the nodes in the same discovery domain. |
| `RMW_IMPLEMENTATION` | `rmw_fastrtps_cpp` | Use Fast DDS instead of Zenoh. |
| `FASTRTPS_DEFAULT_PROFILES_FILE` | `tools/fastdds_udp_only.xml` | Disable built-in transports, including shared memory, and use UDPv4. |

`tools/omx_matlab_env.sh` sets these values using the current checkout's path.
The launch wrapper applies them automatically after sourcing ROS. MATLAB's
`configure_omx_ros2` applies the same settings using the host checkout path.
`export` affects a shell and its child processes, not other terminals or an
already running MATLAB session. MATLAB uses `setenv`, not shell `export`.
These settings do not change the container's default configuration globally.

Stop old Gazebo, teleop, and collection nodes first. A Zenoh router is not needed
for this Fast DDS workflow. If MATLAB already created nodes with different
settings, stop those nodes and restart MATLAB before using the new logger.

## Prerequisites

- The `open_manipulator` container is running.
- The current checkout is mounted at `/root/ros2_ws/src/open_manipulator`.
- The three changed ROS packages have been built and sourced.
- `/opt/lerobot_venv` contains the dependencies from
  `open_manipulator_lerobot/requirements.txt`. The setuptools constraint prevents
  the known conflict between LeRobot and colcon-core.
- MATLAB has ROS Toolbox and Robotics System Toolbox available.

## 1. Start Gazebo (Host Terminal 1)

```bash
cd /home/parkjuwon/open_manipulator
xhost +local:docker
docker exec -it open_manipulator bash \
  /root/ros2_ws/src/open_manipulator/tools/omx_matlab_validation.sh gazebo
```

This loads the table, cube, destination bin, and wrist camera. It hides only the
separate camera-viewer window. Wait until `arm_controller`, `gripper_controller`,
and `joint_state_broadcaster` are active.

## 2. Start the MATLAB Logger

Run in the MATLAB Command Window:

```matlab
addpath("/home/parkjuwon/open_manipulator/matlab")
report = omx_one_episode_validation;
```

Wait until the joint graph is updating before starting collection. MATLAB does
not command the robot: it only receives joint states. The function keeps running
until you press **Stop and Save** or close the graph window. Do not use Ctrl+C
as the normal stop mechanism.

The original `joint_state_logger_until_stop` command remains available. Its
original CSV columns retain their order; `ros_time_sec` is appended.

## 3. Collect One Episode (Host Terminal 2)

```bash
cd /home/parkjuwon/open_manipulator
docker exec -it open_manipulator bash \
  /root/ros2_ws/src/open_manipulator/tools/omx_matlab_validation.sh collect
```

The wrapper sets Fast DDS and the LeRobot Python path automatically. It requests
one successful episode with at most one attempt so a failed first test is not
hidden by retries. It uses a color-neutral task description because cube colors
are randomized, and creates a fresh dataset path for each invocation:

```text
Container: /workspace/lerobot_datasets/omx_f_validation_1_<timestamp>_<pid>
Host:      docker/workspace/lerobot_datasets/omx_f_validation_1_<timestamp>_<pid>
```

Existing 50-episode datasets are not reused or overwritten. If collection fails,
keep the error output for diagnosis. An output directory alone does not mean
that an episode succeeded. If it succeeds, look for `SUCCESS: saved episode 1`.

## 4. Stop and Inspect the MATLAB Log

After collection finishes or fails, press **Stop and Save** in MATLAB. Logs are
written under `matlab_logs/one_episode/`:

- `joint_log_<timestamp>.csv`: joints, gripper, FK XYZ, and ROS message time.
- `joint_log_<timestamp>.mat`: numeric arrays for later analysis.
- `joint_plot_<timestamp>.png`: joint plots.
- `ee_position_plot_<timestamp>.png`: calculated end-effector XYZ and 3D path.
- `joint_log_<timestamp>_summary.csv`: per-signal minimum, maximum, range, and unit.

The returned `report` contains the sample count, elapsed span, mean receive rate,
ROS clock diagnostics, and file paths. Analyze an older CSV independently with:

```matlab
report = analyze_omx_joint_log("/absolute/path/to/joint_log.csv");
```

## What This Test Does and Does Not Verify

The test observes joint motion, computes FK relative to the URDF robot base,
and summarizes an independent MATLAB log. FK is not measured object position.
The log includes idle time before and after the saved episode. Its sample count
and sampling rate need not match the 30 FPS LeRobot dataset.

`time_sec` is MATLAB elapsed wall time. `ros_time_sec` is the `/joint_states`
message timestamp. The current LeRobot writer computes its dataset timestamp
from `frame_index / fps`; it does not preserve these ROS timestamps. These clocks
are not interchangeable, and this workflow does not prove camera/action time
synchronization. It also does not calculate tracking RMSE; use the existing
Simulink reference/feedback monitor for that separate check.

Success requires separately inspecting the saved episode count, video, and
state/action data. This workflow does not prove that a learned policy succeeds.

## Checks Without Moving the Robot

```bash
bash tools/omx_matlab_validation.sh env
shellcheck tools/omx_matlab_env.sh tools/omx_matlab_validation.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The MATLAB summary tests use synthetic CSV files and do not connect to ROS:

```matlab
results = runtests("/home/parkjuwon/open_manipulator/tests/test_analyze_omx_joint_log.m");
assertSuccess(results);
```

After stopping Gazebo, restore the host X server access setting:

```bash
xhost -local:docker
```
