# 모델 실행 기록과 MATLAB 비교 준비

## 실행

GPU Docker 내부 터미널에서 기존 명령을 그대로 사용한다.

```bash
bash /root/ros2_ws/src/open_manipulator/tools/run_omx_toponly.sh red rtc 15
```

`toponly` 실행기는 기본으로 `record_omx_rollout.py`를 사용한다. 모델·카메라 매핑·RTC 설정·
목표값 제한·시작 자세 복귀 요청은 기존과 동일하다. 로봇·카메라는 LeRobot이 한 번만 연다.
원래 관측과 전송 호출을 관찰하며 추가 모터 조회나 별도 카메라 접속을 하지 않는다.
코드 저장 후 실행하면 적용되므로 ROS 빌드는 필요 없다. 이미 실행 중인 프로그램에는 적용되지 않는다.

다른 두 카메라 실행기에도 기록을 적용하려면 같은 Docker 터미널에서
`export OMX_RECORD_ROLLOUT=1`을 설정한 뒤 해당 실행기를 사용한다.
수동 평가 키도 사용하려면 `export OMX_MANUAL_EVALUATION=1`을 추가한다.
기록을 제외한 비교 실행은 `OMX_RECORD_ROLLOUT=0 bash .../run_omx_toponly.sh red rtc 15`로 가능하다.

## 수동 성공·실패 평가

toponly 실행기는 기본으로 평가 키를 활성화한다. 제어 시작 안내가 나온 뒤, 모델을 실행한
Docker 터미널에 포커스를 두고 Enter 없이 키 하나를 누른다. GUI 버튼이나 카메라 자동 판정은 아니다.

| 입력 | 결과 | 종료 처리 |
| --- | --- | --- |
| `s` | 사람이 성공으로 판정 | 추론 종료 요청 후 기존 설정에 따른 초기 자세 복귀·저장 |
| `f` | 사람이 실패로 판정 | 추론 종료 요청 후 기존 설정에 따른 초기 자세 복귀·저장 |
| `q` | 결과를 판단하지 않은 중단 | 같은 종료 처리, 성공·실패로 집계하지 않음 |
| Ctrl+C | 판정 없는 종료 요청 | 원래 LeRobot 종료 처리 유지 |

첫 판정만 채택한다. 첫 제어 명령 전, 종료 요청 후, 복귀 중에는 추가 판정을 받지 않는다.
한 번 실행한 프로그램은 하나의 평가 사례이며 자동으로 다음 시도를 시작하지 않는다.
이 키는 비상 정지가 아니다. 이미 진행 중인 전송·추론을 취소할 수 없으며, 종료 요청을 확인한 뒤
새 정책 목표 전송을 차단한다. 종료 단계의 복귀 명령은 허용한다.

`evaluation.json`에 `outcome=success|failure|not_assessed`, `source`, 판정 시각,
제어 시작 이후 경과시간, 연결 관측 번호, 모델·작업 정보를 기록한다.
입력 스레드는 라벨 확정·종료 이벤트 설정만 담당하고, 주 스레드가 제어 루프를 빠져나온 뒤
복귀 처리 전에 판정 파일을 저장한다. 종료가 끝나면 실행 상태와 기록 완전성을 갱신한다.
`run_metadata.json`에도 평가 결과를 함께 저장한다. 관절·영상·기존 기록은 유지된다.

작업 성공 라벨과 프로그램 종료 상태는 다르다. `f`를 눌러 정상적으로 종료하면
`exit_code.txt`는 0일 수 있지만 작업 결과는 `failure`다. 성공 판정 뒤 복귀나 저장에서
오류가 발생하면 `outcome=success`와 `execution_status=error`를 따로 남긴다.
통신 오류·시간 종료·Ctrl+C를 성공이나 작업 실패로 자동 추정하지 않는다.
실패 단계는 자동으로 알 수 없어 기본값 `failure_stage=unspecified`다.

키 입력은 `docker exec -it ... bash`와 같은 대화형 전경 TTY에서 사용한다. stdout이
`tee`로 연결돼도 stdin TTY로 읽는다. 비대화형·백그라운드 실행에서는 키 기능을 비활성화하고 안내한다.
종료 시 원래 터미널 설정을 복원한다. 강제 종료로 입력 표시가 복원되지 않았다면 해당 터미널에서
`stty sane`으로 복원할 수 있다. 키 기능을 끄려면 실행 앞에 `OMX_MANUAL_EVALUATION=0`을 붙인다.

이 기록은 학습 가중치나 데이터셋을 변경하지 않으며 PNG를 모델에 추가하지도 않는다.
성공률 비교·실패 사례 관리와 추후 자동 판정기 검증을 위한 사람이 붙인 정답이다.

## 저장 파일

매 실행마다 `/workspace/rollout_logs/toponly_대상_추론_FPS_시각/`에 새로 생성한다.
호스트 경로는 `/home/parkjuwon/open_manipulator/docker/workspace/rollout_logs/` 아래다.

| 파일 | 내용 |
| --- | --- |
| `observations.csv` | 관측 번호, 단계, 관측 시작·끝 시각, 관절 읽기 시작·끝 시각, 실제 관절값 6개 |
| `actions.csv` | 행동 번호, 직전 관측 번호·시각·관절값, 요청 목표 6개, 제한 후 전송 요청 목표 6개, 전송 호출 시각·결과 |
| `camera_timing.csv` | 관측 번호와 카메라의 호스트 수신·후처리 시각, 프레임 재사용 여부, 영상 대기열 접수 여부 |
| `video_frames.csv` | MP4 파일·프레임 번호·PTS와 관측 번호·시각의 대응 |
| `videos/top.mp4` | 제어 관측에 실제 사용된 top 영상. 두 카메라 실행이면 `front.mp4`도 생성 |
| `run_metadata.json` | 모델 경로·파일 해시, 작업 지시문, 실행 설정, 모터 ID·정규화 모드·캘리브레이션, 라이브러리 버전, 기록 누락·오류 |
| `evaluation.json` | 사람의 성공·실패·판정 없는 중단, 판정 시각, 실행 상태 |
| `console.log`, `command.txt`, `exit_code.txt` | 기존 콘솔·실행 명령·종료 코드 |

`requested_*`는 로봇 `send_action` API에 입력된 실행 목표이며 전체 모델 예측 묶음은 아니다.
`sent_*`는 `sync_write(Goal_Position)`에 전달한 제한 후 정규화 목표다.
전송 중 예외가 발생하면 이 값은 전송을 **시도한** 값으로 읽어야 한다.
`api_returned`도 모터의 수신 확인이나 목표 도달을 뜻하지 않는다.
성공 여부는 자동 판정하지 않는다. 수동 판정 전 metadata의 `success`는 `not_assessed`이며
`s` 또는 `f`를 누르면 해당 라벨을 기록한다.

## 시간과 단위

- `phase=control`은 모델 수행, `phase=shutdown`은 종료 처리 및 시작 자세 복귀다.
  시연과 비교할 때 우선 `control`만 선택한다.
- `actual_*`는 모터 상태 조회값이며 `actions.csv`에는 직전 관측을 연결한다.
  복귀 구간에서는 같은 관측값이 여러 행동에 연결될 수 있다. 매 전송 시점의 새 측정값으로 오해하지 않는다.
- 관측을 읽은 뒤 목표를 보내므로 같은 행의 목표와 실제값 차이는 응답 오차를 바로 뜻하지 않는다.
  반응 지연과 작업 구간을 정렬한 다음 시연 또는 목표에 대한 오차를 계산한다.
- 기본 팔 관절값은 정규화 범위 -100~100, 집게는 0~100이다. 도·라디안·손끝 XYZ가 아니다.
  FK는 기록한 캘리브레이션·관절 영점·방향과 실물 모델을 확인한 후 별도로 계산한다.
- 모든 `*_ns`는 동일 프로세스의 단조 시계다. `time_sec`는 세션 기준 경과시간이다.
  서로 다른 실행의 단조 시각을 직접 빼지 않는다.
- 카메라 시간은 호스트에서 받은 처리 구간이며 센서 노출 시각이나 하드웨어 동기화를 보장하지 않는다.
  프레임 배열과 시각의 연결을 확인하지 못하면 빈 시각과 `matched_frame=0`을 기록한다.
- 영상에는 관측에 사용한 프레임만 넣으므로 카메라 설정 30 FPS와 저장 영상 프레임 수는 다를 수 있다.
  인코딩 PTS는 관측 완료 간격을 반영한다. 첫 영상 프레임의 PTS는 0이며 실제 시각은 대응 CSV로 연결한다.
  같은 카메라 프레임을 재사용한 관측은 `reused_frame=1`로 표시한다.

## 종료와 기록 완전성

Ctrl+C를 한 번 누르고 원위치 복귀·장치 해제·영상 정리·종료 로그를 기다린다.
영상 인코딩은 최대 32개 대기열의 별도 스레드에서 수행한다. 큐가 차면 제어를 기다리게 하지 않고
해당 영상 프레임을 누락 표시한다. 디스크·인코딩 실패는 다음 제어 호출에서 예외로 알리며 원래
LeRobot의 종료 처리를 거친다. 종료 단계에는 기록 오류로 원래 장치 정리를 막지 않는다.
영상 정리 대기는 최대 15초이며 초과하면 불완전 기록으로 표시한다.
강제 종료·전원 차단에서는 MP4나 최종 metadata를 완성하지 못할 수 있다.

`run_metadata.json`의 `status`, `recording_complete`, `video_queue_dropped_frames`,
`unmatched_camera_timestamps`, `video_error`, `record_error`를 먼저 확인한다. `recording_complete=true`는
기록 누락을 검출하지 않았다는 뜻이며 작업 성공이나 엄밀한 물리적 시간 정렬 보증이 아니다.
기록용 복사·인코딩·디스크 쓰기에도 부하가 있으므로 실물 실행 후 제어 주기와 누락 여부를 확인한다.
영상은 호스트에서도 읽을 수 있도록 읽기 권한을 부여한다. Hugging Face에는 자동 업로드하지 않는다.

## 비교의 다음 단계

MATLAB Command Window(`>>`)에서 CSV를 읽을 수 있다.

```matlab
run_dir = "/home/parkjuwon/open_manipulator/docker/workspace/rollout_logs/실제_실행폴더";
Q = readtable(fullfile(run_dir, "observations.csv"), TextType="string");
Q = Q(Q.phase == "control", :);
plot(Q.time_sec - Q.time_sec(1), Q.actual_elbow_flex)
xlabel("Time [s]"); ylabel("Elbow position [normalized]"); grid on
```

이 코드는 관절 기록 확인용이다. 시연 비교 그래프·시간 정렬·FK·RMSE 자동 계산은 별도 단계다.
시연 데이터와 비교할 때 동일 작업 지시문과 조건의 에피소드를 고르고 접근·집기·이동·놓기 단계를
영상으로 확인한다. 경로가 시연과 다르다는 사실만으로 실패를 판정하지 않는다.

## 장치 없는 검증

```bash
python3 -m unittest discover -s tests -p test_record_omx_rollout.py
python3 -m unittest discover -s tests -p test_run_omx_100ep.py
python3 -m unittest discover -s tests -p test_omx_bs16_model.py
```

합성 영상 인코딩·재디코딩 테스트는 PyAV가 있는 GPU Docker의 Python으로 수행한다.
`--check-only`는 설치된 클래스에 기록 훅을 연결·복원하되 장치와 모델은 열지 않는다.
실물 모터 동작·카메라 스트림·실제 성공률은 이 테스트로 검증하지 않는다.
