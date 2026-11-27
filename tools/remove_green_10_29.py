#!/usr/bin/env python3
"""그린 원본의 10~29번을 제외하고 같은 Hub 주소와 로컬 폴더를 갱신한다."""

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path


NAME = "omx_green_dualcam_20261116_112902"
REPO = f"juwon777/{NAME}"
REMOVE = list(range(10, 30))


def validate_original(info):
    if info.get("total_episodes") != 30:
        raise ValueError("현재 30개인 원본에만 적용합니다. 에피소드 수가 달라 중단합니다.")
    if info.get("fps") != 30 or info.get("robot_type") != "omx_follower":
        raise ValueError("예상한 OMX 원본 데이터셋이 아닙니다.")


def obsolete_files(remote_files, local_files):
    """이전 데이터 조각이 Hub에 남지 않도록 교체 대상의 불필요한 파일을 구분한다."""
    return sorted(p for p in remote_files if p.startswith(("data/", "meta/", "videos/"))
                  and p not in local_files)


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="검증 후 실제 로컬 교체·Hub 갱신 수행")
    parser.add_argument("--base", default="/workspace/lerobot_datasets")
    parser.add_argument("--work-base", default="/workspace/dataset_edits")
    args = parser.parse_args()
    root = Path(args.base) / NAME
    info = json.loads((root / "meta/info.json").read_text())
    validate_original(info)
    print(f"대상: {REPO}\n삭제할 번호: 10~29\n보존할 번호: 0~9\n결과: 30개 → 10개", flush=True)
    if not args.apply:
        print("미리보기만 수행했습니다. 실제 변경하려면 --apply를 지정하세요.")
        return

    from huggingface_hub import HfApi, hf_hub_download, CommitOperationAdd, CommitOperationDelete
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.dataset_tools import delete_episodes
    from lerobot.datasets.utils import create_lerobot_dataset_card

    api = HfApi()
    head = api.repo_info(REPO, repo_type="dataset", revision="main").sha
    remote_info = json.loads(Path(hf_hub_download(REPO, "meta/info.json", repo_type="dataset", revision=head)).read_text())
    if remote_info != info:
        raise ValueError("로컬과 Hub 메타데이터가 달라 중단합니다. 최신 데이터를 먼저 확인하세요.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    work = Path(args.work_base) / f"{NAME}_omit10_29_{stamp}"
    work.mkdir(parents=True, exist_ok=False)
    cleaned_root = work / "cleaned"
    backup = Path(args.base) / f"{NAME}_backup_before_delete_{stamp}"
    source = LeRobotDataset(REPO, root=root, video_backend="pyav")
    helper = load_helper("prepare_omx_100ep")
    expected = helper.episode_signatures(root, range(10))
    cleaned = delete_episodes(source, REMOVE, output_dir=cleaned_root, repo_id=REPO)
    if cleaned.num_episodes != 10 or helper.episode_signatures(cleaned_root, range(10)) != expected:
        raise ValueError("보존할 에피소드 0~9의 내용이 일치하지 않아 중단합니다.")
    quality = load_helper("check_omx_dataset")
    quality.JOINT_NAMES = set(info["features"]["observation.state"]["names"])
    report = quality.inspect_dataset(cleaned_root)
    quality.write_report(report, work / "quality_report")
    if report["summary"]["status"] != "PASS":
        raise ValueError("정리한 데이터셋의 구조·영상 검사에 실패했습니다. 원본은 유지합니다.")
    create_lerobot_dataset_card(dataset_info=cleaned.meta.info, repo_id=REPO).save(cleaned_root / "README.md")
    files = {str(p.relative_to(cleaned_root)): p for p in cleaned_root.rglob("*") if p.is_file()}
    remote_files = api.list_repo_files(REPO, repo_type="dataset", revision=head)
    deleted_paths = obsolete_files(remote_files, files)
    if json.loads((root / "meta/info.json").read_text()) != info:
        raise ValueError("검사 중 로컬 데이터가 변경됐습니다. 다른 수집 프로그램을 종료하세요.")
    # 업로드 실패 시에도 복구할 수 있도록 원본을 백업 이름으로 먼저 보존한다.
    root.rename(backup)
    try:
        cleaned_root.rename(root)
    except BaseException:
        backup.rename(root)
        raise
    operations = [CommitOperationDelete(path_in_repo=p) for p in deleted_paths]
    operations.extend(CommitOperationAdd(path_in_repo=p, path_or_fileobj=str(root / p)) for p in files)
    print(f"로컬 정리 완료. 원본 백업: {backup}", flush=True)
    try:
        commit = api.create_commit(REPO, repo_type="dataset", revision="main", parent_commit=head,
            operations=operations, commit_message="Remove green episodes 10 through 29; preserve episodes 0 through 9")
    except BaseException:
        print(f"Hub 갱신은 완료되지 않았습니다. 로컬 10개: {root}\n원본 30개 백업: {backup}", flush=True)
        raise
    try:
        tags = api.list_repo_refs(REPO, repo_type="dataset").tags
        if any(tag.name == "v3.0" for tag in tags):
            api.delete_tag(REPO, tag="v3.0", repo_type="dataset")
        api.create_tag(REPO, tag="v3.0", revision=commit.oid, repo_type="dataset")
    except Exception:
        print("Hub main 갱신은 완료됐지만 v3.0 태그 갱신이 실패했습니다. 태그 상태를 확인하세요.", flush=True)
        raise
    for path in (root / "videos").rglob("*.mp4"):
        path.chmod((path.stat().st_mode & 0o777) | 0o444)
    print(f"완료: 같은 저장소에 10개 에피소드 유지\nhttps://huggingface.co/datasets/{REPO}", flush=True)
    print("원본 백업·시간 로그·별도 100개 학습 사본은 보존했습니다.")


if __name__ == "__main__":
    main()
