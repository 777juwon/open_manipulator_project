function config = configure_omx_ros2()
% 노드를 만들기 전에 Docker 실행 스크립트와 같은 ROS2 통신 설정을 적용한다.

config.project_root = string(fileparts(fileparts(mfilename("fullpath"))));
config.domain_id = 0;
config.rmw = "rmw_fastrtps_cpp";
config.profile_path = fullfile(config.project_root, "tools", "fastdds_udp_only.xml");
config.log_dir = fullfile(config.project_root, "matlab_logs");

if ~isfile(config.profile_path)
    error("OMX:MissingDDSProfile", "Fast DDS 설정 파일이 없습니다: %s", config.profile_path);
end

setenv("ROS_DOMAIN_ID", string(config.domain_id));
setenv("RMW_IMPLEMENTATION", config.rmw);
setenv("FASTRTPS_DEFAULT_PROFILES_FILE", config.profile_path);
end
