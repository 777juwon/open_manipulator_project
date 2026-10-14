#!/usr/bin/env python3
"""LeRobot v3 OMX 데이터셋을 읽기 전용으로 검사하고 에피소드별 보고서를 만든다."""

import argparse
from bisect import bisect_left
import csv
import json
import math
from numbers import Real
from pathlib import Path
import tempfile


JOINT_NAMES = {"joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint_1"}
FRAME_FIELDS = ["episode_index", "frame_index", "timestamp", "index", "task_index",
                "observation.state", "action"]
EPISODE_FIELDS = ["episode_index", "frame_count", "expected_frame_count",
                  "invalid_state_rows", "invalid_action_rows", "duplicate_frames",
                  "missing_frames", "frame_order_errors", "timestamp_errors",
                  "task_errors", "video_checked", "status", "error_count", "warning_count"]
ISSUE_FIELDS = ["scope", "episode_index", "severity", "check", "message"]


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def iter_parquet_rows(path):
    import pyarrow.parquet as pq

    for batch in pq.ParquetFile(path).iter_batches(batch_size=1024):
        yield from batch.to_pylist()


def probe_video(path):
    import av

    # 한 영상 파일을 여러 에피소드가 공유하므로 파일당 한 번만 끝까지 디코딩한다.
    times = []
    size = None
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("영상 스트림이 없습니다.")
        stream = container.streams.video[0]
        rate = float(stream.average_rate) if stream.average_rate else None
        for frame in container.decode(stream):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("프레임의 영상 시간이 없습니다.")
            timestamp = float(frame.pts * frame.time_base)
            if not finite(timestamp) or (times and timestamp <= times[-1]):
                raise ValueError("영상 프레임 시간이 증가하지 않습니다.")
            frame_size = (frame.width, frame.height)
            if size is not None and frame_size != size:
                raise ValueError("영상 중간에 해상도가 변경됐습니다.")
            size = frame_size
            times.append(timestamp)
    if not times:
        raise ValueError("디코딩할 수 있는 영상 프레임이 없습니다.")
    return {"timestamps": times, "size": size, "fps": rate}


def dataset_path(root, template, **values):
    path = (root / template.format(**values)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("메타데이터 파일 경로가 데이터셋 폴더 밖을 가리킵니다.")
    return path


def inspect_dataset(dataset_root, *, skip_video=False, timestamp_tolerance=1e-4):
    root = Path(dataset_root).resolve()
    info = json.loads(dataset_path(root, "meta/info.json").read_text(encoding="utf-8"))
    if info.get("codebase_version") != "v3.0":
        raise ValueError("이 검사기는 LeRobotDataset v3.0을 대상으로 합니다.")
    fps = info.get("fps")
    if not finite(fps) or fps <= 0:
        raise ValueError("meta/info.json의 fps는 양수여야 합니다.")
    if not finite(timestamp_tolerance) or timestamp_tolerance <= 0:
        raise ValueError("시간 허용 오차는 양수여야 합니다.")
    if not isinstance(info.get("features"), dict):
        raise ValueError("meta/info.json에 features가 있어야 합니다.")

    issues = {}

    def issue(check, message, episode=None, severity="ERROR"):
        # 같은 오류가 매 프레임에 반복돼도 원인별로 한 번만 보고한다.
        key = (episode, severity, check)
        issues.setdefault(key, {"scope": "dataset" if episode is None else "episode",
                                "episode_index": "" if episode is None else episode,
                                "severity": severity, "check": check, "message": message})

    for key in ("observation.state", "action"):
        feature = info["features"].get(key, {})
        if not isinstance(feature, dict):
            feature = {}
        names = feature.get("names")
        if (feature.get("shape") != [6] or not isinstance(names, list)
                or not all(isinstance(name, str) for name in names)
                or len(names) != 6 or set(names) != JOINT_NAMES):
            issue("feature_schema", f"{key}는 OMX 관절 5개와 그리퍼 1개의 이름 및 shape [6]이 필요합니다.")
    video_features = {key: value for key, value in info["features"].items()
                      if isinstance(value, dict) and value.get("dtype") == "video"}
    if not video_features:
        issue("video_schema", "영상 feature가 없습니다.")

    metadata = {}
    for path in sorted((root / "meta/episodes").glob("**/*.parquet")):
        try:
            dataset_path(root, str(path.relative_to(root)))
            for row in iter_parquet_rows(path):
                episode = row.get("episode_index")
                if not integer(episode):
                    issue("metadata_episode_index", "메타데이터에 유효하지 않은 에피소드 번호가 있습니다.")
                    continue
                if episode in metadata:
                    issue("duplicate_episode_metadata", "에피소드 메타데이터가 중복됐습니다.", episode)
                else:
                    metadata[episode] = row
        except Exception as error:
            issue("metadata_read", f"에피소드 메타데이터를 읽지 못했습니다: {path.name}: {error}")
    if not metadata:
        issue("episode_metadata_missing", "읽을 수 있는 에피소드 메타데이터가 없습니다.")

    tasks = {}
    try:
        for row in iter_parquet_rows(dataset_path(root, "meta/tasks.parquet")):
            index, text = row.get("task_index"), row.get("task")
            if not integer(index) or not isinstance(text, str) or not text.strip() or index in tasks:
                issue("task_metadata", "task 번호가 중복됐거나 작업 텍스트가 비어 있습니다.")
            else:
                tasks[index] = text
    except Exception as error:
        issue("task_metadata_read", f"task 메타데이터를 읽지 못했습니다: {error}")

    episodes = {}

    def episode_state(episode):
        if episode not in episodes:
            episodes[episode] = {
                "episode_index": episode, "frame_count": 0, "expected_frame_count": "",
                "invalid_state_rows": 0, "invalid_action_rows": 0, "duplicate_frames": 0,
                "missing_frames": 0, "frame_order_errors": 0, "timestamp_errors": 0,
                "task_errors": 0, "video_checked": False, "frames": set(),
                "indices": [], "first_path": None, "last_timestamp": None,
            }
        return episodes[episode]

    total_rows = 0
    files = sorted((root / "data").glob("**/*.parquet"))
    if not files:
        issue("data_missing", "data 폴더에 Parquet 파일이 없습니다.")
    for path in files:
        try:
            dataset_path(root, str(path.relative_to(root)))
            for row in iter_parquet_rows(path):
                if row.get("index") != total_rows or not integer(row.get("index")):
                    issue("global_index", "전체 index가 0부터 순서대로 이어지지 않습니다.")
                total_rows += 1
                episode = row.get("episode_index")
                if not integer(episode):
                    issue("data_episode_index", "관절 데이터에 유효하지 않은 에피소드 번호가 있습니다.")
                    continue
                state = episode_state(episode)
                ordinal = state["frame_count"]
                state["frame_count"] += 1
                state["first_path"] = state["first_path"] or path.resolve()
                state["indices"].append(row.get("index"))
                if not all(field in row for field in FRAME_FIELDS):
                    issue("frame_columns", "필수 프레임 열이 누락됐습니다.", episode)
                for key, counter in (("observation.state", "invalid_state_rows"),
                                     ("action", "invalid_action_rows")):
                    values = row.get(key)
                    if not isinstance(values, list) or len(values) != 6 or not all(map(finite, values)):
                        state[counter] += 1
                        issue(counter, f"{key}에 누락값, 비수치 값 또는 관절 개수 오류가 있습니다.", episode)

                frame = row.get("frame_index")
                if not integer(frame) or frame != ordinal:
                    state["frame_order_errors"] += 1
                    issue("frame_order", "frame_index가 0부터 순서대로 이어지지 않습니다.", episode)
                if integer(frame):
                    if frame in state["frames"]:
                        state["duplicate_frames"] += 1
                        issue("duplicate_frames", "중복된 frame_index가 있습니다.", episode)
                    state["frames"].add(frame)

                timestamp = row.get("timestamp")
                previous = state["last_timestamp"]
                if (not finite(timestamp) or not integer(frame)
                        or abs(timestamp - frame / fps) > timestamp_tolerance
                        or (previous is not None and finite(timestamp) and timestamp <= previous)):
                    state["timestamp_errors"] += 1
                    issue("timestamp", "시간이 유효하지 않거나 증가 순서 및 frame_index/fps와 다릅니다.", episode)
                if finite(timestamp):
                    state["last_timestamp"] = timestamp

                task = row.get("task_index")
                declared_tasks = metadata.get(episode, {}).get("tasks")
                if (not integer(task) or task not in tasks or not isinstance(declared_tasks, list)
                        or tasks.get(task) not in declared_tasks):
                    state["task_errors"] += 1
                    issue("task_reference", "프레임 task가 에피소드의 작업 텍스트와 연결되지 않습니다.", episode)
        except Exception as error:
            issue("data_read", f"관절 Parquet를 읽지 못했습니다: {path.name}: {error}")

    observed_episodes = set(episodes)
    video_cache = {}
    video_segments = {}
    for episode in sorted(set(episodes) | set(metadata)):
        state = episode_state(episode)
        meta = metadata.get(episode)
        if meta is None:
            issue("episode_metadata_missing", "관절 데이터에 대응하는 에피소드 메타데이터가 없습니다.", episode)
            continue
        length = meta.get("length")
        if not integer(length) or length == 0:
            issue("episode_length", "에피소드 length는 양의 정수여야 합니다.", episode)
        else:
            state["expected_frame_count"] = length
            state["missing_frames"] = length - sum(0 <= frame < length for frame in state["frames"])
            if state["frame_count"] != length or state["missing_frames"] or any(
                    frame >= length for frame in state["frames"]):
                issue("episode_length", "프레임 수 또는 frame_index 범위가 메타데이터 length와 다릅니다.", episode)
        start, end = meta.get("dataset_from_index"), meta.get("dataset_to_index")
        if (not integer(start) or not integer(end) or not integer(length) or end - start != length
                or len(state["indices"]) != length
                or any(value != start + i for i, value in enumerate(state["indices"]))):
            issue("dataset_range", "전체 index 범위가 에피소드 메타데이터와 다릅니다.", episode)
        try:
            chunk, file = meta["data/chunk_index"], meta["data/file_index"]
            if not integer(chunk) or not integer(file):
                raise ValueError("data 파일 번호는 음수가 아닌 정수여야 합니다.")
            expected = dataset_path(root, info["data_path"], chunk_index=chunk, file_index=file)
            if state["first_path"] != expected:
                raise ValueError("에피소드 시작 Parquet 파일이 메타데이터와 다릅니다.")
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            issue("data_file_reference", str(error), episode)

        videos_ok = bool(video_features) and not skip_video
        if skip_video:
            issue("video_skipped", "영상 디코딩 검사를 생략했습니다. 전체 점검 통과가 아닙니다.", episode, "WARNING")
        for key, feature in video_features.items():
            try:
                prefix = f"videos/{key}"
                chunk, file = meta[f"{prefix}/chunk_index"], meta[f"{prefix}/file_index"]
                start, end = meta[f"{prefix}/from_timestamp"], meta[f"{prefix}/to_timestamp"]
                if not integer(chunk) or not integer(file):
                    raise ValueError("영상 파일 번호는 음수가 아닌 정수여야 합니다.")
                if (not finite(start) or not finite(end) or start < 0 or end <= start
                        or not integer(length) or abs(end - start - length / fps) > timestamp_tolerance):
                    raise ValueError("영상 시간 구간과 에피소드 length/fps가 다릅니다.")
                path = dataset_path(root, info["video_path"], video_key=key, chunk_index=chunk, file_index=file)
                if not path.is_file():
                    raise ValueError("연결된 영상 파일이 없습니다.")
                video_segments.setdefault(path, []).append((start, end, episode, key))
                if skip_video:
                    continue
                if path not in video_cache:
                    try:
                        video_cache[path] = probe_video(path)
                    except Exception as error:
                        video_cache[path] = error
                video = video_cache[path]
                if isinstance(video, Exception):
                    raise ValueError(f"영상 디코딩 실패: {video}")
                shape = feature.get("shape")
                if not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3 or video["size"] != (shape[1], shape[0]):
                    raise ValueError("영상 해상도가 feature shape와 다릅니다.")
                if not finite(video["fps"]) or abs(video["fps"] - fps) > 1e-3:
                    raise ValueError("영상 FPS가 메타데이터 fps와 다릅니다.")
                times = video["timestamps"]
                lower = bisect_left(times, start - timestamp_tolerance)
                upper = bisect_left(times, end - timestamp_tolerance)
                segment = times[lower:upper]
                if len(segment) != length or any(abs(t - start - i / fps) > timestamp_tolerance
                                                 for i, t in enumerate(segment)):
                    raise ValueError("에피소드 영상 구간의 프레임 수 또는 프레임 시간이 맞지 않습니다.")
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                videos_ok = False
                issue(f"video:{key}", str(error), episode)
        state["video_checked"] = videos_ok

    for segments in video_segments.values():
        previous = None
        for start, end, episode, key in sorted(segments):
            if previous is not None and start < previous[1] - timestamp_tolerance:
                for affected, camera in ((episode, key), (previous[2], previous[3])):
                    issue(f"video_overlap:{camera}", "서로 다른 에피소드가 같은 영상 시간 구간을 중복 참조합니다.", affected)
                    episodes[affected]["video_checked"] = False
            if previous is None or end > previous[1]:
                previous = (start, end, episode, key)

    for key, actual in (("total_frames", total_rows), ("total_episodes", len(observed_episodes)),
                        ("total_tasks", len(tasks))):
        if not integer(info.get(key)) or info[key] != actual:
            issue(key, f"{key}: 메타데이터={info.get(key)}, 실제={actual}")
    if sorted(episodes) != list(range(len(episodes))):
        issue("episode_sequence", "에피소드 번호가 0부터 연속되지 않습니다.")
    if sorted(tasks) != list(range(len(tasks))):
        issue("task_sequence", "task 번호가 0부터 연속되지 않습니다.")

    issue_rows = list(issues.values())
    episode_rows = []
    for episode, state in sorted(episodes.items()):
        relevant = [row for row in issue_rows if row["episode_index"] in ("", episode)]
        errors = sum(row["severity"] == "ERROR" for row in relevant)
        warnings = sum(row["severity"] == "WARNING" for row in relevant)
        state.update(error_count=errors, warning_count=warnings,
                     status="FAIL" if errors else "WARNING" if warnings else "PASS")
        episode_rows.append({key: state[key] for key in EPISODE_FIELDS})
    errors = sum(row["severity"] == "ERROR" for row in issue_rows)
    warnings = sum(row["severity"] == "WARNING" for row in issue_rows)
    return {
        "summary": {"dataset_root": str(root), "status": "FAIL" if errors else "WARNING" if warnings else "PASS",
                    "episode_count": len(observed_episodes), "metadata_episode_count": len(metadata),
                    "frame_count": total_rows, "fps": fps,
                    "error_count": errors, "warning_count": warnings, "video_check_skipped": skip_video,
                    "timestamp_tolerance_sec": timestamp_tolerance,
                    "limitations": ["저장 구조 점검이며 물체 집기 성공, 실제 영상/관절 측정 동기화, 추종 오차를 판정하지 않습니다."]},
        "episodes": episode_rows, "issues": issue_rows,
    }


def write_report(report, report_dir):
    output = Path(report_dir).resolve()
    root = Path(report["summary"]["dataset_root"]).resolve()
    if output.is_relative_to(root):
        raise ValueError("보고서는 원본 데이터셋 폴더 밖에 저장해야 합니다.")
    if output.exists():
        raise FileExistsError(f"기존 보고서 폴더를 덮어쓰지 않습니다: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 모든 파일을 먼저 완성한 뒤 새 폴더에 게시한다. 원본에는 쓰지 않는다.
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".omx_quality_") as folder:
        staging = Path(folder)
        for name, rows, fields in (("episodes.csv", report["episodes"], EPISODE_FIELDS),
                                   ("issues.csv", report["issues"], ISSUE_FIELDS)):
            with (staging / name).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        (staging / "summary.json").write_text(json.dumps(report["summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.mkdir()
        published = []
        try:
            for path in staging.iterdir():
                destination = output / path.name
                path.replace(destination)
                published.append(destination)
        except OSError:
            # 이 실행이 새로 만든 파일만 정리한다. 기존 보고서나 원본은 삭제하지 않는다.
            for path in published:
                path.unlink(missing_ok=True)
            try:
                output.rmdir()
            except OSError:
                pass
            raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, help="수집이 종료된 LeRobot 데이터셋 폴더")
    parser.add_argument("--report-dir", required=True, help="새로 만들 보고서 폴더, 기존 폴더는 덮어쓰지 않음")
    parser.add_argument("--skip-video", action="store_true", help="영상 디코딩을 생략, 결과는 WARNING 또는 FAIL")
    parser.add_argument("--timestamp-tolerance", type=float, default=1e-4, help="프레임/영상 시간 허용 오차 [초], 기본 0.0001")
    args = parser.parse_args()
    try:
        # 실행 환경 누락과 데이터 자체의 오류를 구분한다.
        import pyarrow.parquet  # noqa: F401
        if not args.skip_video:
            import av  # noqa: F401
        report = inspect_dataset(args.dataset_root, skip_video=args.skip_video,
                                 timestamp_tolerance=args.timestamp_tolerance)
        output = write_report(report, args.report_dir)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        parser.error(str(error))
    summary = report["summary"]
    print(f"점검 결과: {summary['status']}")
    print(f"에피소드 {summary['episode_count']}개, 프레임 {summary['frame_count']}개")
    print(f"오류 {summary['error_count']}건, 경고 {summary['warning_count']}건")
    print(f"보고서: {output}")
    print("원본 데이터셋은 변경하지 않았습니다. 작업 성공이나 실제 측정 동기화 판정은 별도입니다.")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
