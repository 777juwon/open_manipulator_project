#!/usr/bin/env python3
"""LeRobot의 관절 배열을 보기용 CSV로 펼친다. 원본 데이터셋은 변경하지 않는다."""

import argparse
import csv
import json
from pathlib import Path
import tempfile


INDEX_FIELDS = ["episode_index", "frame_index", "timestamp", "index", "task_index"]


def feature_names(info, key):
    names = info["features"][key]["names"]
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError(f"{key}의 관절 이름은 비어 있지 않은 문자열 목록이어야 합니다.")
    if len(set(names)) != len(names):
        raise ValueError(f"{key}에 중복된 관절 이름이 있습니다.")
    return names


def flatten_frame(row, state_names, action_names):
    result = {name: row[name] for name in INDEX_FIELDS}
    for key, prefix, names in (
        ("observation.state", "state", state_names),
        ("action", "action", action_names),
    ):
        values = row[key]
        if len(values) != len(names):
            raise ValueError(f"{key}의 값 개수가 관절 이름 개수와 다릅니다.")
        result.update({f"{prefix}_{name}": value for name, value in zip(names, values)})
    return result


def export_dataset_csv(dataset_root, csv_out):
    root = Path(dataset_root)
    output = Path(csv_out)
    if output.suffix.lower() != ".csv":
        raise ValueError("보기용 출력 파일의 확장자는 .csv여야 합니다.")
    if output.exists():
        raise FileExistsError(f"기존 파일을 덮어쓰지 않습니다: {output}")
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    state_names = feature_names(info, "observation.state")
    action_names = feature_names(info, "action")
    shards = sorted((root / "data").glob("**/*.parquet"))
    if not shards:
        raise ValueError("data 폴더에 저장된 Parquet 파일이 없습니다.")

    # 호스트의 단위 테스트는 배열 변환만 검증하므로 PyArrow를 여기서 불러온다.
    import pyarrow.parquet as pq

    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = INDEX_FIELDS + [f"state_{n}" for n in state_names] + [f"action_{n}" for n in action_names]
    count = 0
    temporary = None
    try:
        # 변환 중 실패하면 불완전한 파일을 최종 CSV로 남기지 않는다.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=output.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for shard in shards:
                for batch in pq.ParquetFile(shard).iter_batches(batch_size=1024):
                    for row in batch.to_pylist():
                        writer.writerow(flatten_frame(row, state_names, action_names))
                        count += 1
        # 배타적으로 최종 파일을 생성해 동시에 만들어진 기존 파일도 보호한다.
        # Docker에서 만든 파일도 호스트의 MATLAB이 읽을 수 있도록 한다.
        temporary.chmod(0o644)
        output.hardlink_to(temporary)
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, help="LeRobot 데이터셋 폴더")
    parser.add_argument("--csv-out", required=True, help="새로 만들 보기용 CSV 경로")
    args = parser.parse_args()
    try:
        count = export_dataset_csv(args.dataset_root, args.csv_out)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f"보기용 CSV 저장: {args.csv_out}")
    print(f"프레임 수: {count} (원본 Parquet과 영상은 변경하지 않았습니다.)")


if __name__ == "__main__":
    main()
