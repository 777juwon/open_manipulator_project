function paths = export_omx_joint_log(mat_path)
% MAT 원본에서 CSV와 관절/FK 그래프를 만든다. ROS 연결 없이 복원할 수 있다.

mat_path = string(mat_path);
S = load(mat_path);
required = ["time_log", "joint_log", "gripper_log"];
if ~all(isfield(S, cellstr(required)))
    error("OMX:MissingBackupFields", "MAT에 시간, 관절, 그리퍼 기록이 있어야 합니다.");
end
sample_count = numel(S.time_log);
if sample_count == 0 || size(S.joint_log, 1) ~= sample_count || ...
        size(S.joint_log, 2) ~= 5 || numel(S.gripper_log) ~= sample_count
    error("OMX:LogSizeMismatch", "시간, 관절 5개, 그리퍼의 기록 개수가 일치해야 합니다.");
end
if isfield(S, "ros_time_log") && numel(S.ros_time_log) ~= sample_count
    error("OMX:LogSizeMismatch", "ROS 시간 기록 개수가 관절 기록과 다릅니다.");
end

[log_dir, stem] = fileparts(mat_path);
paths.mat_path = mat_path;
paths.csv_path = fullfile(log_dir, stem + ".csv");
plot_stem = replace(stem, "joint_log_", "joint_plot_");
ee_stem = replace(stem, "joint_log_", "ee_position_plot_");
paths.joint_png_path = fullfile(log_dir, plot_stem + ".png");
paths.ee_png_path = fullfile(log_dir, ee_stem + ".png");

disp("FK로 로봇 손끝 XYZ 좌표를 계산합니다.");
S.ee_xyz = compute_ee_positions_from_joints(S.joint_log, S.gripper_log);

% table은 문자열 데이터도 받으므로 옵션 이름은 문자 벡터로 명확히 구분한다.
T = table(S.time_log(:), ...
    S.joint_log(:, 1), S.joint_log(:, 2), S.joint_log(:, 3), ...
    S.joint_log(:, 4), S.joint_log(:, 5), S.gripper_log(:), ...
    S.ee_xyz(:, 1), S.ee_xyz(:, 2), S.ee_xyz(:, 3), ...
    'VariableNames', {'time_sec', 'joint1', 'joint2', 'joint3', ...
    'joint4', 'joint5', 'gripper', 'ee_x_m', 'ee_y_m', 'ee_z_m'});
if isfield(S, "ros_time_log")
    T.ros_time_sec = S.ros_time_log(:);
end
writetable(T, paths.csv_path);
save(mat_path, '-struct', 'S');

joint_fig = figure("Visible", "off");
joint_cleanup = onCleanup(@() close_if_valid(joint_fig)); %#ok<NASGU>
plot(S.time_log, S.joint_log);
grid on;
xlabel("Time [s]");
ylabel("Joint position [rad]");
legend("joint1", "joint2", "joint3", "joint4", "joint5");
saveas(joint_fig, paths.joint_png_path);

ee_fig = figure("Visible", "off");
ee_cleanup = onCleanup(@() close_if_valid(ee_fig)); %#ok<NASGU>
tiledlayout(2, 1);
nexttile;
plot(S.time_log, S.ee_xyz);
grid on;
xlabel("Time [s]");
ylabel("Position [m]");
legend("x", "y", "z");
title("End-effector position");
nexttile;
plot3(S.ee_xyz(:, 1), S.ee_xyz(:, 2), S.ee_xyz(:, 3), "LineWidth", 1.5);
grid on;
xlabel("X [m]");
ylabel("Y [m]");
zlabel("Z [m]");
title("End-effector 3D path");
saveas(ee_fig, paths.ee_png_path);
fprintf("CSV 저장: %s\n관절 그래프 저장: %s\n손끝 그래프 저장: %s\n", ...
    paths.csv_path, paths.joint_png_path, paths.ee_png_path);
end

function close_if_valid(fig)
if isgraphics(fig)
    close(fig);
end
end
