#!/usr/bin/env python3
"""다섯 원본의 10~29번을 선택한 사본을 병합하고 검증한다. 원본은 변경하지 않는다."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


SOURCES = [
    ("green", "omx_green_dualcam_20261116_112902"),
    ("yellow", "omx_yellow_dualcam_20261116_114617"),
    ("red", "omx_red_dualcam_20261116_115930_417206024"),
    ("blue", "omx_blue_dualcam_20261116_120456_316162313"),
    ("white_tape", "omx_white_tape_dualcam_10ep_omit6_20261116_124137"),
]
OUTPUT_NAME = "omx_five_objects_ep10_29_100ep"


def expected_task(label):
    target = "white tape" if label == "white_tape" else f"{label} block"
    return f"Pick up the {target} and place it in the box."


def make_plan(base):
    """원본 에피소드 번호 10~29를 정확히 선택한다."""
    plan = []
    first_features = None
    for label, name in SOURCES:
        root = Path(base) / name
        info = json.loads((root / "meta/info.json").read_text())
        count = info.get("total_episodes")
        if type(count) is not int or count < 30:
            raise ValueError(f"{name}: 에피소드 29번까지 필요합니다. 현재 개수={count}")
        if info.get("fps") != 30 or info.get("codebase_version") != "v3.0":
            raise ValueError(f"{name}: LeRobot v3, 30 FPS가 아닙니다.")
        features = info.get("features", {})
        if first_features is None:
            first_features = features
        if features != first_features:
            raise ValueError(f"{name}: 다섯 데이터셋의 feature가 일치하지 않습니다.")
        cameras = {key for key in features if key.startswith("observation.images.")}
        if cameras != {"observation.images.front", "observation.images.top"}:
            raise ValueError(f"{name}: front/top 두 영상이 필요합니다.")
        keep = list(range(10, 30))
        plan.append({"label": label, "name": name, "root": str(root),
                     "task": expected_task(label), "source_episode_count": count,
                     "keep": keep, "exclude": [i for i in range(count) if i not in keep]})
    return plan


def read_rows(root, folder, columns=None):
    import pyarrow.parquet as pq
    for path in sorted((Path(root) / folder).rglob("*.parquet")):
        yield from pq.read_table(path, columns=columns).to_pylist()


def episode_signatures(root, episodes):
    """선택한 에피소드의 관절·Action·시간표를 원본과 대조하기 위한 서명을 만든다."""
    grouped = {i: [] for i in episodes}
    columns = ["episode_index", "frame_index", "timestamp", "observation.state", "action"]
    for row in read_rows(root, "data", columns):
        ep = row["episode_index"]
        if ep in grouped:
            grouped[ep].append(row)
    signatures = {}
    for ep, rows in grouped.items():
        rows.sort(key=lambda row: row["frame_index"])
        if not rows or [r["frame_index"] for r in rows] != list(range(len(rows))):
            raise ValueError(f"에피소드 프레임이 없거나 연속적이지 않습니다: {root}, {ep}")
        payload = [[r["frame_index"], r["timestamp"], r["observation.state"], r["action"]]
                   for r in rows]
        signatures[ep] = (len(rows), hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).hexdigest())
    return signatures


def verify_prepared(root):
    root = Path(root)
    manifest = json.loads((root / "selection_manifest.json").read_text())
    if (manifest.get("status") != "validated" or len(manifest.get("mapping", [])) != 100
            or manifest.get("info_sha256") != hashlib.sha256((root / "meta/info.json").read_bytes()).hexdigest()):
        raise ValueError("완료된 100개 선택 데이터셋이 아니거나 준비 후 메타데이터가 변경됐습니다.")
    return manifest


def prepare(base, output, work, owner, *, check_only=False):
    import pyarrow.parquet as pq
    plan = make_plan(base)
    source_signatures = {}
    for item in plan:
        tasks = pq.read_table(Path(item["root"]) / "meta/tasks.parquet").to_pylist()
        if not tasks or any(row.get("task") != item["task"] for row in tasks):
            raise ValueError(f"기존 instruction이 예상과 다릅니다: {item['name']}")
        source_signatures[item["name"]] = episode_signatures(item["root"], item["keep"])
        print(f"{item['label']}: 원본 {item['source_episode_count']}개 중 10~29번 20개 선택", flush=True)
    expected_frames = sum(n for data in source_signatures.values() for n, _ in data.values())
    print(f"선택 합계: 100개 에피소드, {expected_frames}프레임", flush=True)
    if check_only:
        print("읽기 전용 점검 완료. 사본 생성과 학습은 실행하지 않았습니다.")
        return
    output, work = Path(output), Path(work)
    if output.exists() or work.exists():
        raise FileExistsError("준비 경로가 이미 있습니다. 덮어쓰지 않습니다. 완료 여부를 확인하세요.")
    source_roots = [Path(item["root"]).resolve() for item in plan]
    if any(path.resolve().is_relative_to(source) for path in (output, work) for source in source_roots):
        raise ValueError("출력이나 작업 폴더를 원본 데이터셋 안에 만들 수 없습니다.")
    from lerobot.datasets.dataset_tools import delete_episodes, merge_datasets
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    work.mkdir(parents=True)
    datasets = []
    mapping = []
    for item in plan:
        source = LeRobotDataset(f"{owner}/{item['name']}", root=item["root"], video_backend="pyav")
        # 제외 기능은 별도 출력 경로의 사본에 적용한다. 원본에는 삭제를 수행하지 않는다.
        subset = delete_episodes(source, item["exclude"], output_dir=work / item["label"],
                                 repo_id=f"{owner}/{output.name}_{item['label']}")
        if subset.num_episodes != 20:
            raise ValueError(f"선택 결과가 20개가 아닙니다: {item['label']}")
        datasets.append(subset)
        for old in item["keep"]:
            n, signature = source_signatures[item["name"]][old]
            mapping.append({"episode_index": len(mapping), "source_dataset": item["name"],
                            "source_episode": old, "task": item["task"],
                            "frames": n, "state_action_time_sha256": signature})
    merged = merge_datasets(datasets, f"{owner}/{output.name}", output_dir=output,
                            concatenate_videos=False, concatenate_data=False)
    if merged.num_episodes != 100 or merged.num_frames != expected_frames:
        raise ValueError("병합 후 에피소드 또는 프레임 수가 예상과 다릅니다.")
    result_signatures = episode_signatures(output, range(100))
    for row in mapping:
        if result_signatures[row["episode_index"]] != (row["frames"], row["state_action_time_sha256"]):
            raise ValueError(f"원본과 병합 결과의 관절·Action·시간표가 다릅니다: {row['episode_index']}")
    result_tasks = {row["episode_index"]: row["tasks"] for row in read_rows(output, "meta/episodes")}
    if any(result_tasks.get(row["episode_index"]) != [row["task"]] for row in mapping):
        raise ValueError("병합 후 에피소드별 instruction이 원본과 다릅니다.")
    spec = importlib.util.spec_from_file_location("quality", Path(__file__).with_name("check_omx_dataset.py"))
    quality = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(quality)
    quality.JOINT_NAMES = set(plan_features(base)["observation.state"]["names"])
    report = quality.inspect_dataset(output)
    quality.write_report(report, work / "quality_report")
    if report["summary"]["status"] != "PASS":
        raise ValueError(f"최종 구조·영상 검사를 통과하지 못했습니다: {work / 'quality_report'}")
    for path in (output / "videos").rglob("*.mp4"):
        if path.is_file() and not path.is_symlink():
            path.chmod((path.stat().st_mode & 0o777) | 0o444)
    manifest = {"status": "validated", "repo_id": f"{owner}/{output.name}",
                "total_episodes": 100, "total_frames": expected_frames,
                "info_sha256": hashlib.sha256((output / "meta/info.json").read_bytes()).hexdigest(),
                "mapping": mapping}
    (output / "selection_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"검증 완료. 학습 데이터셋: {output}", flush=True)
    print("Hub 업로드와 모델 학습은 아직 실행하지 않았습니다.")


def plan_features(base):
    return json.loads((Path(base) / SOURCES[0][1] / "meta/info.json").read_text())["features"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="/workspace/lerobot_datasets")
    parser.add_argument("--output", default=f"/workspace/lerobot_datasets/{OUTPUT_NAME}")
    parser.add_argument("--work", default=f"/workspace/dataset_preparation/{OUTPUT_NAME}")
    parser.add_argument("--owner", default="juwon777")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify-prepared", help="기존 준비 결과의 완료 표시와 메타데이터를 확인")
    args = parser.parse_args()
    if args.verify_prepared:
        manifest = verify_prepared(args.verify_prepared)
        print(f"학습 데이터 확인: {manifest['total_episodes']}개, {manifest['total_frames']}프레임")
    else:
        prepare(args.base, args.output, args.work, args.owner, check_only=args.check_only)


if __name__ == "__main__":
    main()
