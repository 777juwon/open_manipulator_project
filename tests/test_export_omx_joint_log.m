function tests = test_export_omx_joint_log
% 실제 ROS 연결 없이 표 생성과 MAT 복원 저장을 검증한다.
tests = functiontests(localfunctions);
end

function setupOnce(testCase)
testCase.TestData.old_path = path;
addpath(fullfile(fileparts(fileparts(mfilename("fullpath"))), "matlab"));
testCase.TestData.folder = tempname;
mkdir(testCase.TestData.folder);
end

function teardownOnce(testCase)
path(testCase.TestData.old_path);
rmdir(testCase.TestData.folder, "s");
end

function test_export_recovers_csv_mat_and_plots(testCase)
time_log = [0; 0.1; 0.2];
joint_log = zeros(3, 5);
gripper_log = zeros(3, 1);
ros_time_log = [8; 8.1; 8.2];
mat_path = fullfile(testCase.TestData.folder, "joint_log_test.mat");
save(mat_path, "time_log", "joint_log", "gripper_log", "ros_time_log");
paths = export_omx_joint_log(mat_path);
T = readtable(paths.csv_path);
verifyEqual(testCase, height(T), 3);
verifyEqual(testCase, width(T), 11);
verifyEqual(testCase, T.time_sec, time_log, "AbsTol", 1e-12);
verifyEqual(testCase, T.ros_time_sec, ros_time_log, "AbsTol", 1e-12);
verifyTrue(testCase, isfile(paths.joint_png_path));
verifyTrue(testCase, isfile(paths.ee_png_path));
S = load(mat_path);
verifyEqual(testCase, size(S.ee_xyz), [3, 3]);
verifyEqual(testCase, S.joint_log, joint_log);
end

function test_legacy_backup_without_ros_time_is_supported(testCase)
time_log = [0; 0.1];
joint_log = zeros(2, 5);
gripper_log = zeros(2, 1);
mat_path = fullfile(testCase.TestData.folder, "joint_log_legacy.mat");
save(mat_path, "time_log", "joint_log", "gripper_log");
paths = export_omx_joint_log(mat_path);
T = readtable(paths.csv_path);
verifyEqual(testCase, height(T), 2);
verifyEqual(testCase, width(T), 10);
verifyFalse(testCase, ismember("ros_time_sec", string(T.Properties.VariableNames)));
end

function test_mismatched_rows_are_rejected(testCase)
time_log = [0; 0.1];
joint_log = zeros(3, 5);
gripper_log = zeros(2, 1);
mat_path = fullfile(testCase.TestData.folder, "joint_log_invalid.mat");
save(mat_path, "time_log", "joint_log", "gripper_log");
verifyError(testCase, @() export_omx_joint_log(mat_path), "OMX:LogSizeMismatch");
verifyFalse(testCase, isfile(fullfile(testCase.TestData.folder, "joint_log_invalid.csv")));
end

function test_missing_fields_are_rejected(testCase)
time_log = [0; 0.1];
mat_path = fullfile(testCase.TestData.folder, "joint_log_missing.mat");
save(mat_path, "time_log");
verifyError(testCase, @() export_omx_joint_log(mat_path), "OMX:MissingBackupFields");
end
