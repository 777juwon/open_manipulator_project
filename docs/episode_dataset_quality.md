# 에피소드별 LeRobot Dataset 품질 점검

`tools/check_omx_dataset.py`는 수집이 끝난 LeRobotDataset v3.0의 관절 데이터,
에피소드 메타데이터와 영상 파일을 읽어서 품질 보고서를 생성한다.
Gazebo와 MATLAB을 실행할 필요가 없으며 로봇을 움직이지 않는다.
원본 데이터셋은 수정하지 않는다. 기존 `/opt/lerobot_venv`의 PyArrow와 AV를 사용한다.

## 실행 방법

다음 명령은 **노트북의 호스트 Bash 터미널**에서 실행한다. MATLAB의 `>>` 창이 아니다.
`run`은 검사할 데이터셋 폴더 이름으로 바꾼다. 수집기를 종료한 뒤 실행한다.

```bash
cd /home/parkjuwon/open_manipulator
run=omx_f_validation_1_20261014_052600_1950
stamp=$(date +%Y%m%d_%H%M%S)
docker exec open_manipulator /opt/lerobot_venv/bin/python \
  /root/ros2_ws/src/open_manipulator/tools/check_omx_dataset.py \
  --dataset-root "/workspace/lerobot_datasets/$run" \
  --report-dir "/root/ros2_ws/src/open_manipulator/matlab_logs/dataset_quality/${run}_${stamp}"
```

보고서는 호스트의 `matlab_logs/dataset_quality/<run>_<stamp>/`에 생성된다.
보고서 경로는 원본 데이터셋 폴더 밖이어야 한다. 기존 보고서 폴더는 덮어쓰지 않는다.
새 패키지 설치나 ROS2 재빌드는 필요 없다. 1개, 50개, 100개 모두 같은 명령을 사용한다.

## 검사 항목

| 구분 | 검사 기준 |
| --- | --- |
| 관절 및 Action | OMX 관절 5개와 그리퍼 1개의 스키마, 배열 크기, 숫자 및 NaN/Inf 여부 |
| 프레임 | 에피소드별 `frame_index`가 0부터 연속, 중복·누락 및 전체 `index` 순서 |
| 시간 | 유한한 값, 에피소드 내부 증가 순서, `frame_index / fps`와의 일치 |
| 메타데이터 | 전체 프레임·에피소드·task 수, 에피소드 length와 index 범위, 시작 Parquet 경로 |
| 작업 텍스트 | 프레임의 task 번호와 에피소드 작업 텍스트의 연결 |
| 영상 | 참조 파일 존재, 전체 디코딩, 해상도, FPS, 에피소드 영상 구간의 프레임 수와 시간, 구간 중복 |

LeRobot v3에서는 여러 에피소드가 한 Parquet 또는 영상 파일을 공유할 수 있다.
파일 개수와 에피소드 개수를 같다고 가정하지 않는다. 영상은 파일당 한 번 디코딩하고,
메타데이터의 `from_timestamp`부터 `to_timestamp`까지 에피소드 구간을 구분한다.
시간 비교의 기본 허용 오차는 `0.0001`초이며 필요하면 `--timestamp-tolerance`로 조정한다.

영상 디코딩을 생략하려면 `--skip-video`를 추가한다. 이 경우 전체 검사를 완료하지
않았으므로 오류가 없어도 `PASS`가 아닌 `WARNING`으로 표시한다.

## 생성되는 보고서

- `episodes.csv`: 에피소드별 프레임 수, 오류 프레임 수, 누락·중복 수, 영상 점검과 최종 상태.
- `issues.csv`: 원인별 오류·경고 코드와 한국어 설명. 같은 원인의 반복 오류는 한 항목으로 모은다.
- `summary.json`: 전체 결과, 실제 집계 수, 설정값과 검사 한계.

CSV는 한글을 읽기 쉽도록 UTF-8 BOM을 사용한다. 표 계산 도구 또는 MATLAB에서 열 수 있다.
**MATLAB의 `>>` 창**에서는 다음처럼 확인한다. `report_dir`는 생성된 실제 경로로 바꾼다.

```matlab
report_dir = "/home/parkjuwon/open_manipulator/matlab_logs/dataset_quality/실제_보고서_폴더";
E = readtable(fullfile(report_dir, "episodes.csv"), "TextType", "string");
openvar("E")
I = readtable(fullfile(report_dir, "issues.csv"), "TextType", "string");
openvar("I")
```

`PASS`는 정의한 저장 구조 검사가 통과했다는 뜻이다. `FAIL`은 데이터 또는 연결 오류가
있다는 뜻이고 `WARNING`은 영상 생략처럼 검사가 불완전하다는 뜻이다.
데이터셋 전체 오류는 특정 에피소드로 원인을 좁힐 수 없으므로 모든 에피소드에도 표시한다.
종료 코드는 전체 `PASS`가 `0`, `FAIL` 또는 `WARNING`이 `1`, 실행 설정 오류가 `2`이다.
`1`로 종료돼도 보고서는 저장되므로 `issues.csv`를 확인한다.

## 점검의 한계

- timestamp는 FPS에서 만들어진 프레임 시간이다. ROS 메시지 측정 시간의 일치를 증명하지 않는다.
- 정상적인 영상 파일도 물체가 가려져 있거나 작업이 실패한 장면을 담을 수 있다.
- 이 기능은 물체 집기 성공, 물체 최종 위치 오차, 목표 대비 추종 RMSE를 계산하지 않는다.
- 수집기는 성공 에피소드만 저장한다. 저장된 데이터셋만으로 전체 시도 성공률은 계산할 수 없다.
- 완료된 수집 결과를 대상으로 한다. 수집 중이거나 아직 finalize되지 않은 데이터는 오류로 나올 수 있다.
- 파일을 자동 삭제하거나 학습 데이터에서 제외하지 않는다. 문제 원인을 확인한 후 별도로 조치한다.

## 회귀 테스트

다음 명령은 **호스트 터미널**에서 실행한다. 실제 파일 테스트는 임시 데이터셋을
만들며 원본 데이터와 로봇은 건드리지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
docker exec -e PYTHONDONTWRITEBYTECODE=1 -w /root/ros2_ws/src/open_manipulator \
  open_manipulator /opt/lerobot_venv/bin/python \
  -m unittest discover -s tests -p 'test_*.py' -v
```

호스트에 AV/PyArrow가 없으면 실제 Parquet/MP4 테스트는 생략된다. Docker 테스트에서는
기존 LeRobot 의존성으로 실제 파일 테스트까지 수행한다.
