#!/usr/bin/env python3
"""장치 연결 전에 기존 듀얼 카메라 데이터와 작업 문장의 호환성을 검사한다."""

import argparse
import json
from pathlib import Path


JOINTS = ["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
          "wrist_flex.pos", "wrist_roll.pos", "gripper.pos"]


def validate_info(info, tasks, expected_task):
    """같은 대상과 동일한 입력 구성을 이어 수집하는 경우만 허용한다."""
    if info.get("codebase_version") != "v3.0" or info.get("robot_type") != "omx_follower":
        raise ValueError("LeRobot v3 OMX 팔로워 데이터셋이 아닙니다.")
    if info.get("fps") != 30:
        raise ValueError("기존 데이터셋의 FPS가 현재 설정 30과 다릅니다.")
    features = info.get("features", {})
    cameras = {k for k in features if k.startswith("observation.images.")}
    if cameras != {"observation.images.front", "observation.images.top"}:
        raise ValueError("기존 카메라 구성이 front/top 듀얼 카메라와 다릅니다.")
    for key in cameras:
        f = features[key]
        if (f.get("shape") != [480, 640, 3] or f.get("dtype") != "video"
                or f.get("info", {}).get("video.fps") != 30):
            raise ValueError(f"기존 영상 설정이 640×480, 30fps와 다릅니다: {key}")
    for key in ("observation.state", "action"):
        f = features.get(key, {})
        if f.get("shape") != [6] or f.get("names") != JOINTS:
            raise ValueError(f"기존 관절 이름·순서·차원이 현재 설정과 다릅니다: {key}")
    if not tasks or any(row.get("task") != expected_task for row in tasks):
        raise ValueError("기존 instruction과 요청 대상이 다릅니다. 기존 문장을 변경하지 않습니다.")
    count = info.get("total_episodes")
    if type(count) is not int or count < 1:
        raise ValueError("기존 데이터셋의 저장 에피소드 수를 확인할 수 없습니다.")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    try:
        import pyarrow.parquet as pq
        root = Path(args.root)
        info = json.loads((root / "meta/info.json").read_text())
        tasks = pq.read_table(root / "meta/tasks.parquet").to_pylist()
        count = validate_info(info, tasks, args.task)
    except (OSError, ValueError, KeyError, ImportError) as error:
        parser.error(str(error))
    print(f"기존 에피소드: {count}개. 다음 기록은 에피소드 {count}부터 시작합니다.")
    print(f"유지할 instruction: {args.task}")


if __name__ == "__main__":
    main()
