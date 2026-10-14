function log_paths = joint_state_logger_until_stop(log_dir)
% Stop and Save 버튼을 누를 때까지 OMX-F 관절 상태를 기록한다.

config = configure_omx_ros2();
if nargin < 1
    log_dir = config.log_dir;
end
if ~exist(log_dir, "dir")
    mkdir(log_dir);
end

stamp = string(datetime("now", "Format", "yyyyMMdd_HHmmss"));
csv_path = fullfile(log_dir, "joint_log_" + stamp + ".csv");
png_path = fullfile(log_dir, "joint_plot_" + stamp + ".png");
ee_png_path = fullfile(log_dir, "ee_position_plot_" + stamp + ".png");
mat_path = fullfile(log_dir, "joint_log_" + stamp + ".mat");
log_paths = struct( ...
    "csv_path", csv_path, "joint_png_path", png_path, ...
    "ee_png_path", ee_png_path, "mat_path", mat_path);

node = ros2node("/matlab_joint_logger", config.domain_id);
sub = ros2subscriber(node, "/joint_states", "sensor_msgs/JointState");

time_log = [];
joint_log = [];
gripper_log = [];
ros_time_log = [];

fig = figure("Name", "OMX-F Joint Logger", "NumberTitle", "off");
setappdata(fig, "stop_requested", false);
uicontrol( ...
    "Style", "pushbutton", ...
    "String", "Stop and Save", ...
    "Units", "normalized", ...
    "Position", [0.82 0.92 0.16 0.06], ...
    "Callback", @(src, event) setappdata(fig, "stop_requested", true));

h = plot(nan, nan, nan, nan, nan, nan, nan, nan, nan, nan);
grid on;
xlabel("Time [s]");
ylabel("Joint position [rad]");
legend("joint1", "joint2", "joint3", "joint4", "joint5");
ylim([-3.2 3.2]);

disp("관절 기록을 시작했습니다.");
disp("키보드로 로봇을 조작하거나 자동 수집기를 실행하세요.");
disp("종료하려면 그래프의 Stop and Save 버튼을 누르거나 창을 닫으세요.");

t0 = tic;
last_autosave_t = 0;

while ishandle(fig) && ~getappdata(fig, "stop_requested")
    try
        msg = receive(sub, 2);
    catch ME
        warning("OMX:JointReceiveStopped", ...
            "관절 메시지 수신이 중단되어 현재 기록을 저장합니다: %s", ME.message);
        break;
    end

    names = string(msg.name);
    pos = msg.position;

    q = zeros(1, 5);
    for j = 1:5
        joint_name = "joint" + string(j);
        idx = find(names == joint_name, 1);
        if isempty(idx)
            error("/joint_states에 " + joint_name + " 관절이 없습니다.");
        end
        q(j) = pos(idx);
    end

    grip_idx = find(names == "gripper_joint_1", 1);
    if isempty(grip_idx)
        gripper = NaN;
    else
        gripper = pos(grip_idx);
    end

    now_t = toc(t0);

    time_log(end + 1, 1) = now_t; %#ok<AGROW>
    joint_log(end + 1, :) = q; %#ok<AGROW>
    gripper_log(end + 1, 1) = gripper; %#ok<AGROW>
    ros_time_log(end + 1, 1) = ...
        double(msg.header.stamp.sec) + double(msg.header.stamp.nanosec) * 1e-9; %#ok<AGROW>

    for j = 1:5
        set(h(j), "XData", time_log, "YData", joint_log(:, j));
    end

    xlim([max(0, now_t - 10), max(10, now_t)]);
    drawnow limitrate;

    if now_t - last_autosave_t > 5
        save(mat_path, "time_log", "joint_log", "gripper_log", "ros_time_log");
        last_autosave_t = now_t;
    end
end

if isempty(time_log)
    warning("OMX:NoJointSamples", "관절 데이터를 받지 못해 저장할 기록이 없습니다.");
    return;
end

% FK나 그래프 저장에 문제가 생겨도 마지막 원본 기록은 MAT에 남긴다.
save(mat_path, "time_log", "joint_log", "gripper_log", "ros_time_log");
log_paths = export_omx_joint_log(mat_path);
disp("관절 기록을 종료했습니다.");
disp("저장된 CSV:");
disp(csv_path);
disp("저장된 관절 그래프:");
disp(png_path);
disp("저장된 로봇 손끝 그래프:");
disp(ee_png_path);
disp("저장된 MAT 원본:");
disp(mat_path);
end
