#!/usr/bin/env python3
"""다운로드 원본을 보존하고 현재 LeRobot에서 읽을 수 있는 설정 사본을 만든다."""

import argparse
from dataclasses import fields
import json
from pathlib import Path
import shutil


MAPPING = {"observation.images.top": "observation.images.camera1",
           "observation.images.front": "observation.images.camera2"}


def adapt_config(config, known_fields):
    """지원하지 않는 새 선택 항목은 값이 None인 두 항목만 제거한다."""
    result = dict(config)
    unknown = set(result) - set(known_fields) - {"type"}
    for key in unknown:
        if key not in {"dtype", "optimizer_fused"} or result[key] is not None:
            raise ValueError(f"현재 버전에서 의미를 보존해 변환할 수 없는 설정입니다: {key}")
        del result[key]
    return result


def adapt_processor(processor, tokenizer_path, expected_mapping=None):
    result = json.loads(json.dumps(processor))
    mapping = next((s["config"].get("rename_map") for s in result["steps"]
                    if s.get("registry_name") == "rename_observations_processor"), None)
    if mapping != (MAPPING if expected_mapping is None else expected_mapping):
        raise ValueError("새 모델의 학습 카메라 매핑이 예상과 다릅니다.")
    steps = [s for s in result["steps"] if s.get("registry_name") == "tokenizer_processor"]
    if len(steps) != 1:
        raise ValueError("토크나이저 설정을 확인할 수 없습니다.")
    steps[0]["config"]["tokenizer_name"] = str(Path(tokenizer_path).resolve())
    # 이전 버전은 상대 경로 artifacts를 해석하지 않으므로 동일 파일의 절대 경로를 사용한다.
    steps[0].pop("artifacts", None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/workspace/models/omx_smolvla_dualcam_lora_bs16_20k")
    parser.add_argument("--output", default="/workspace/models/omx_smolvla_dualcam_lora_bs16_20k_compat061")
    parser.add_argument("--camera-layout", choices=["dual", "top"], default="dual")
    args = parser.parse_args()
    source, output = Path(args.source), Path(args.output)
    if output.exists():
        raise FileExistsError(f"기존 실행 사본을 덮어쓰지 않습니다: {output}")
    from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
    from transformers import AutoTokenizer

    for name in ["config.json", "adapter_config.json", "adapter_model.safetensors",
                 "policy_preprocessor.json", "policy_postprocessor.json", "tokenizer/tokenizer.json"]:
        if not (source / name).is_file():
            raise FileNotFoundError(f"모델 파일이 없습니다: {source / name}")
    config = adapt_config(json.loads((source / "config.json").read_text()),
                          {f.name for f in fields(SmolVLAConfig)})
    processor = adapt_processor(json.loads((source / "policy_preprocessor.json").read_text()),
                                 output / "tokenizer",
                                 {"observation.images.top": "observation.images.camera1"}
                                 if args.camera_layout == "top" else MAPPING)
    AutoTokenizer.from_pretrained(source / "tokenizer", local_files_only=True)
    shutil.copytree(source, output)
    (output / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    (output / "policy_preprocessor.json").write_text(json.dumps(processor, ensure_ascii=False, indent=2))
    SmolVLAConfig.from_pretrained(output)
    print(f"실행 사본 준비 완료: {output}")
    print("원본 가중치·토크나이저·정규화 값은 유지했습니다. 로봇 연결·모델 추론은 수행하지 않았습니다.")


if __name__ == "__main__":
    main()
