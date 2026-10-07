function joint_state_logger_until_stop()
% Log OMX-F joint states from ROS 2 until the Stop button is pressed.

setenv("ROS_DOMAIN_ID", "0");
setenv( ...
    "FASTRTPS_DEFAULT_PROFILES_FILE", ...
    "/home/parkjuwon/open_manipulator/tools/fastdds_udp_only.xml");

log_dir = "/home/parkjuwon/open_manipulator/matlab_logs";
if ~exist(log_dir, "dir")
    mkdir(log_dir);
end

stamp = string(datetime("now", "Format", "yyyyMMdd_HHmmss"));
csv_path = fullfile(log_dir, "joint_log_" + stamp + ".csv");
png_path = fullfile(log_dir, "joint_plot_" + stamp + ".png");
ee_png_path = fullfile(log_dir, "ee_position_plot_" + stamp + ".png");
mat_path = fullfile(log_dir, "joint_log_" + stamp + ".mat");

node = ros2node("/matlab_joint_logger", 0);
sub = ros2subscriber(node, "/joint_states", "sensor_msgs/JointState");

time_log = [];
joint_log = [];
gripper_log = [];

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

disp("Logging started.");
disp("Move the robot with teleop.");
disp("Press the 'Stop and Save' button or close the plot window.");

t0 = tic;
last_autosave_t = 0;

while ishandle(fig) && ~getappdata(fig, "stop_requested")
    msg = receive(sub, 2);

    names = string(msg.name);
    pos = msg.position;

    q = zeros(1, 5);
    for j = 1:5
        joint_name = "joint" + string(j);
        idx = find(names == joint_name, 1);
        if isempty(idx)
            error("Missing " + joint_name + " in /joint_states");
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

    for j = 1:5
        set(h(j), "XData", time_log, "YData", joint_log(:, j));
    end

    xlim([max(0, now_t - 10), max(10, now_t)]);
    drawnow limitrate;

    if now_t - last_autosave_t > 5
        save(mat_path, "time_log", "joint_log", "gripper_log");
        last_autosave_t = now_t;
    end
end

if ishandle(fig)
    save_joint_log( ...
        time_log, joint_log, gripper_log, ...
        csv_path, png_path, ee_png_path, mat_path, fig);
else
    save_joint_log( ...
        time_log, joint_log, gripper_log, ...
        csv_path, png_path, ee_png_path, mat_path, []);
end
disp("Logging stopped.");
disp("Saved CSV:");
disp(csv_path);
disp("Saved plot:");
disp(png_path);
disp("Saved end-effector plot:");
disp(ee_png_path);
disp("Saved MAT backup:");
disp(mat_path);

end

function save_joint_log( ...
    time_log, joint_log, gripper_log, ...
    csv_path, png_path, ee_png_path, mat_path, fig)
if isempty(time_log)
    warning("No joint data was recorded. Nothing was saved to CSV.");
    return;
end

disp("Computing end-effector XYZ positions with MATLAB FK...");
ee_xyz = compute_ee_positions_from_joints(joint_log, gripper_log);

T = table( ...
    time_log, ...
    joint_log(:, 1), ...
    joint_log(:, 2), ...
    joint_log(:, 3), ...
    joint_log(:, 4), ...
    joint_log(:, 5), ...
    gripper_log, ...
    ee_xyz(:, 1), ...
    ee_xyz(:, 2), ...
    ee_xyz(:, 3), ...
    "VariableNames", ...
    { ...
        'time_sec', ...
        'joint1', 'joint2', 'joint3', 'joint4', 'joint5', ...
        'gripper', ...
        'ee_x_m', 'ee_y_m', 'ee_z_m' ...
    });

writetable(T, csv_path);
save(mat_path, "time_log", "joint_log", "gripper_log", "ee_xyz");

if ishandle(fig)
    saveas(fig, png_path);
else
    joint_fig = figure("Visible", "off");
    plot(time_log, joint_log);
    grid on;
    xlabel("Time [s]");
    ylabel("Joint position [rad]");
    legend("joint1", "joint2", "joint3", "joint4", "joint5");
    saveas(joint_fig, png_path);
    close(joint_fig);
end

ee_fig = figure("Visible", "off");
tiledlayout(2, 1);

nexttile;
plot(time_log, ee_xyz);
grid on;
xlabel("Time [s]");
ylabel("Position [m]");
legend("x", "y", "z");
title("End-effector position");

nexttile;
plot3(ee_xyz(:, 1), ee_xyz(:, 2), ee_xyz(:, 3), "LineWidth", 1.5);
grid on;
xlabel("X [m]");
ylabel("Y [m]");
zlabel("Z [m]");
title("End-effector 3D path");

saveas(ee_fig, ee_png_path);
close(ee_fig);
end
