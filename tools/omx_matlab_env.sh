#!/usr/bin/env bash
# MATLAB과 통신하는 ROS2 프로세스를 시작하기 전에 source로 적용한다.

_omx_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
_omx_dds_profile="${_omx_project_root}/tools/fastdds_udp_only.xml"

if [[ ! -r "${_omx_dds_profile}" ]]; then
    printf 'OMX:MissingDDSProfile: Fast DDS 설정 파일이 없습니다: %s\n' "${_omx_dds_profile}" >&2
    unset _omx_project_root _omx_dds_profile
    return 1 2>/dev/null || exit 1
fi

export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE="${_omx_dds_profile}"

unset _omx_project_root _omx_dds_profile
