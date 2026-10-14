function report = omx_one_episode_validation()
% 별도 터미널에서 실행한 시연을 관절/FK로 기록하고 종료 후 요약한다.

config = configure_omx_ros2();
disp("관절 그래프가 갱신되면 별도 호스트 터미널에서 자동 수집기를 실행하세요.");
disp("수집기가 성공하거나 실패해 종료되면 그래프의 Stop and Save를 누르세요.");
paths = joint_state_logger_until_stop(fullfile(config.log_dir, "one_episode"));
if ~isfile(paths.csv_path)
    report = [];
    warning("OMX:NoValidationLog", "관절 기록이 저장되지 않았습니다. ROS2 통신을 확인하세요.");
    return;
end
report = analyze_omx_joint_log(paths.csv_path);
report.log_paths = paths;
end
