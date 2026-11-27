#!/usr/bin/env python3
"""저장 성공한 tele 에피소드의 관절값과 실측 시각을 MATLAB용 CSV로 내보낸다."""

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import tempfile


JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


def saved_timings(path, dataset_name):
    """폐기된 재촬영 시도를 제외하고 저장 완료 attempt만 연결한다."""
    saves, buffered, roots = {}, {}, set()
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if Path(event.get("dataset_root", "")).name != dataset_name:
                continue
            roots.add(str(Path(event["dataset_root"])))
            if len(roots) > 1:
                raise ValueError("동일한 이름의 서로 다른 데이터셋 경로가 시간 로그에 섞여 있습니다.")
            kind = event.get("event")
            if kind == "saved_episode":
                episode = event["episode_index"]
                if episode in saves:
                    raise ValueError("같은 에피소드의 저장 성공 기록이 중복됐습니다.")
                saves[episode] = event["attempt"]
            elif kind == "buffered_frame":
                key = (event["episode_index"], event["attempt"], event["frame_index"])
                if key in buffered:
                    raise ValueError("프레임 시간 기록이 중복됐습니다.")
                buffered[key] = event
    selected = {}
    for (episode, attempt, frame), event in buffered.items():
        if saves.get(episode) != attempt:
            continue
        intervals = event.get("bus_read_intervals_ns", [])
        if len(intervals) != 1 or len(intervals[0]) != 2:
            raise ValueError("관절 조회 구간을 하나로 식별할 수 없습니다.")
        start, end = intervals[0]
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            raise ValueError("관절 조회 시각이 올바르지 않습니다.")
        selected[episode, frame] = (start, end)
    if not selected:
        raise ValueError("데이터셋과 연결되는 저장 성공 시간 기록이 없습니다.")
    return selected


def export_reference(dataset_root, timing_path, output_dir):
    import pyarrow.parquet as pq

    root, output = Path(dataset_root), Path(output_dir)
    if output.exists():
        raise FileExistsError(f"기존 참조 사본을 덮어쓰지 않습니다: {output}")
    info = json.loads((root / "meta/info.json").read_text())
    orders = {}
    for key in ["observation.state", "action"]:
        names = info["features"][key]["names"]
        expected = [f"{name}.pos" for name in JOINTS]
        if len(names) != len(expected) or set(names) != set(expected):
            raise ValueError(f"관절 이름이 현재 실물 모델과 다릅니다: {key}")
        orders[key] = [names.index(name) for name in expected]
    tasks = {row["task_index"]: row["task"] for row in pq.read_table(root / "meta/tasks.parquet").to_pylist()}
    timings = saved_timings(timing_path, root.name)
    episodes = {}
    for file in sorted((root / "data").rglob("*.parquet")):
        columns = ["episode_index", "frame_index", "timestamp", "task_index", "observation.state", "action"]
        for batch in pq.ParquetFile(file).iter_batches(columns=columns):
            for row in batch.to_pylist():
                episodes.setdefault(row["episode_index"], []).append(row)
    if len(episodes) != info["total_episodes"] or sum(map(len, episodes.values())) != info["total_frames"]:
        raise ValueError("메타데이터와 실제 에피소드·프레임 수가 다릅니다.")
    if set(timings) != {(ep, row["frame_index"]) for ep, rows in episodes.items() for row in rows}:
        raise ValueError("저장 성공 시간 기록과 Parquet 프레임이 일대일로 연결되지 않습니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="tele_reference_", dir=output.parent))
    summary = []
    try:
        with (staging / "tele_reference.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["episode_index", "frame_index", "task", "time_sec", "dataset_timestamp_sec",
                             "state_start_ns", "state_end_ns",
                             *[f"state_{j}" for j in JOINTS], *[f"action_{j}" for j in JOINTS]])
            for episode, rows in sorted(episodes.items()):
                rows.sort(key=lambda row: row["frame_index"])
                if [r["frame_index"] for r in rows] != list(range(len(rows))):
                    raise ValueError("에피소드 프레임 번호가 연속적이지 않습니다.")
                midpoint2 = [sum(timings[episode, r["frame_index"]]) for r in rows]
                if len(rows) < 2 or any(b <= a for a, b in zip(midpoint2, midpoint2[1:])):
                    raise ValueError("관절 측정 시각이 증가하지 않거나 프레임 수가 부족합니다.")
                episode_tasks = {tasks[r["task_index"]] for r in rows}
                if len(episode_tasks) != 1:
                    raise ValueError("한 에피소드에 서로 다른 작업 지시문이 있습니다.")
                for row, midpoint in zip(rows, midpoint2):
                    values = []
                    for key in ["observation.state", "action"]:
                        if len(row[key]) != len(JOINTS):
                            raise ValueError("관절 배열 길이가 다릅니다.")
                        values.extend(float(row[key][i]) for i in orders[key])
                    if not all(math.isfinite(v) for v in values):
                        raise ValueError("관절값에 NaN 또는 무한대가 있습니다.")
                    start, end = timings[episode, row["frame_index"]]
                    writer.writerow([episode, row["frame_index"], tasks[row["task_index"]],
                                     (midpoint - midpoint2[0]) / 2e9, row["timestamp"], start, end, *values])
                duration = (midpoint2[-1] - midpoint2[0]) / 2e9
                summary.append([episode, next(iter(episode_tasks)), len(rows), duration, (len(rows) - 1) / duration])
        with (staging / "episodes.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["episode_index", "task", "frames", "duration_sec", "measured_hz"])
            writer.writerows(summary)
        metadata = {"dataset_root": str(root), "timing_path": str(timing_path), "joints": list(JOINTS),
                    "time_basis": "measured_joint_read_midpoint", "episodes": len(episodes),
                    "frames": sum(map(len, episodes.values())), "dataset_fps": info["fps"],
                    "calibration_available": False,
                    "unit_note": "원본 숫자를 보존했습니다. 수집 시 캘리브레이션이 없어 실행과 동일한 정규화·영점이라는 가정을 확인해야 합니다."}
        (staging / "tele_reference_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for file in staging.iterdir():
            file.chmod(0o644)
        staging.chmod(0o755)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"episodes": len(episodes), "frames": sum(map(len, episodes.values())), "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--timing-path", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(export_reference(args.dataset_root, args.timing_path, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
