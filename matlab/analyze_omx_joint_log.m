function report = analyze_omx_joint_log(csv_path)
% 관절/FK 기록을 요약한다. 물체 집기 성공 여부를 판정하는 함수는 아니다.

csv_path = string(csv_path);
T = readtable(csv_path, "VariableNamingRule", "preserve");
signals = ["joint1", "joint2", "joint3", "joint4", "joint5", ...
    "gripper", "ee_x_m", "ee_y_m", "ee_z_m"];
required = ["time_sec", signals];
if ~all(ismember(required, string(T.Properties.VariableNames)))
    error("OMX:MissingLogColumns", "CSV에 시간, 관절, 그리퍼, FK 열이 있어야 합니다.");
end
if height(T) < 2
    error("OMX:TooFewSamples", "분석에는 최소 두 개의 기록 시점이 필요합니다.");
end

data = T{:, cellstr(signals)};
times = T.time_sec;
if ~isnumeric(data) || ~isnumeric(times) || ...
        any(isnan(data) | isinf(data), "all") || any(isnan(times) | isinf(times))
    error("OMX:InvalidLogValues", "시간, 관절 위치, FK 값에 NaN이나 무한대가 있으면 안 됩니다.");
end
if any(diff(times) <= 0)
    error("OMX:NonIncreasingTime", "MATLAB 기록 시간은 순서대로 증가해야 합니다.");
end

minimum = min(data, [], 1).';
maximum = max(data, [], 1).';
units = [repmat("rad", 1, 6), repmat("m", 1, 3)].';
summary = table(signals.', units, minimum, maximum, maximum - minimum, ...
    'VariableNames', ["Signal", "Unit", "Minimum", "Maximum", "Range"]);
[log_dir, stem] = fileparts(csv_path);
summary_path = fullfile(log_dir, stem + "_summary.csv");
report.csv_path = csv_path;
report.summary_path = summary_path;
report.sample_count = height(T);
report.elapsed_span_sec = times(end) - times(1);
report.mean_receive_rate_hz = (height(T) - 1) / report.elapsed_span_sec;
report.signal_summary = summary;
report.ros_time_span_sec = NaN;
report.ros_clock_backsteps = NaN;
report.ros_repeated_timestamps = NaN;

if ismember("ros_time_sec", string(T.Properties.VariableNames))
    ros_times = T.ros_time_sec;
    if ~isnumeric(ros_times) || any(isnan(ros_times) | isinf(ros_times))
        error("OMX:InvalidROSTime", "ROS 메시지 시간에 NaN이나 무한대가 있으면 안 됩니다.");
    end
    ros_dt = diff(ros_times);
    report.ros_time_span_sec = ros_times(end) - ros_times(1);
    report.ros_clock_backsteps = nnz(ros_dt < 0);
    report.ros_repeated_timestamps = nnz(ros_dt == 0);
end

writetable(summary, summary_path);
fprintf("기록 시점 수: %d\nMATLAB 기록 구간: %.3f초\n평균 수신 빈도: %.2f Hz\n", ...
    report.sample_count, report.elapsed_span_sec, report.mean_receive_rate_hz);
disp(summary);
fprintf("요약 CSV: %s\n", summary_path);
if ~isnan(report.ros_clock_backsteps)
    fprintf("ROS 시간 역행: %d회, 같은 시간 반복: %d회\n", ...
        report.ros_clock_backsteps, report.ros_repeated_timestamps);
end
disp("이 기록에는 수집 작업 전후의 대기 시간도 포함됩니다.");
disp("관절/FK 기록만으로 영상 동기화나 작업 성공을 판정하지 않습니다.");
end
