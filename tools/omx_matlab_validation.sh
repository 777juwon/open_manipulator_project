#!/usr/bin/env bash
set -eo pipefail

usage() {
    printf '%s\n' \
        '사용법: bash tools/omx_matlab_validation.sh {env|gazebo|collect}' \
        '  env      MATLAB과 호환되는 ROS2 통신 설정을 출력합니다.' \
        '  gazebo   Fast DDS로 손목 카메라가 있는 Gazebo 환경을 실행합니다.' \
        '  collect  성공 에피소드 1개를 기록합니다. 첫 시도가 실패하면 중단합니다.'
}

mode="${1:-help}"
if [[ "$mode" == help || "$mode" == --help || "$mode" == -h ]]; then
    usage
    exit 0
fi
case "$mode" in
    env|gazebo|collect) ;;
    *) usage >&2; exit 2 ;;
esac
if [[ $# -ne 1 ]]; then
    usage >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$mode" != env ]]; then
    for setup_file in /opt/ros/jazzy/setup.bash /root/ros2_ws/install/setup.bash; do
        if [[ ! -r "$setup_file" ]]; then
            printf '빌드된 open_manipulator 컨테이너 안에서 실행하세요. 없는 파일: %s\n' \
                "$setup_file" >&2
            exit 1
        fi
        # shellcheck disable=SC1090
        source "$setup_file"
    done
fi

# ROS 환경을 불러온 뒤 컨테이너 기본값인 Domain 30과 Zenoh를 덮어쓴다.
# shellcheck source=tools/omx_matlab_env.sh
source "${script_dir}/omx_matlab_env.sh"
printf 'ROS_DOMAIN_ID=%s\nRMW_IMPLEMENTATION=%s\nFASTRTPS_DEFAULT_PROFILES_FILE=%s\n' \
    "$ROS_DOMAIN_ID" "$RMW_IMPLEMENTATION" "$FASTRTPS_DEFAULT_PROFILES_FILE"

case "$mode" in
    env)
        exit 0
        ;;
    gazebo)
        exec ros2 launch open_manipulator_bringup omx_f_pick_place_gazebo.launch.py \
            show_camera:=false
        ;;
    collect)
        if [[ ! -x /opt/lerobot_venv/bin/python ]]; then
            printf '/opt/lerobot_venv에 LeRobot 환경을 먼저 설치하세요.\n' >&2
            exit 1
        fi
        export PATH="/opt/lerobot_venv/bin:${PATH}"
        run_name="omx_f_validation_1_$(date +%Y%m%d_%H%M%S)_$$"
        dataset_root="/workspace/lerobot_datasets/${run_name}"
        printf '\nMATLAB 로거가 기록 중인지 확인하세요. 지금부터 자동 수집을 시작합니다.\n'
        printf '데이터셋 저장 위치: %s\n' "$dataset_root"
        printf '목표는 성공 에피소드 1개, 최대 시도 1회입니다. 기존 데이터셋은 사용하지 않습니다.\n\n'
        exec ros2 run open_manipulator_lerobot omx_f_auto_collect \
            --episodes 1 \
            --max-attempts 1 \
            --dataset-root "$dataset_root" \
            --repo-id "local/${run_name}" \
            --task 'Pick up the cube and place it in the blue box'
        ;;
esac
