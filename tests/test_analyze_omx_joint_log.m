function tests = test_analyze_omx_joint_log
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

function test_summary_and_legacy_columns(testCase)
T = sample_log();
csv_path = fullfile(testCase.TestData.folder, "legacy.csv");
writetable(T, csv_path);
report = analyze_omx_joint_log(csv_path);
verifyEqual(testCase, report.sample_count, 4);
verifyEqual(testCase, report.elapsed_span_sec, 0.3, "AbsTol", 1e-12);
verifyEqual(testCase, report.mean_receive_rate_hz, 10, "AbsTol", 1e-12);
verifyEqual(testCase, report.signal_summary.Range(1), 0.3, "AbsTol", 1e-12);
verifyTrue(testCase, isfile(report.summary_path));
verifyTrue(testCase, isnan(report.ros_time_span_sec));
end

function test_ros_clock_diagnostics(testCase)
T = sample_log();
T.ros_time_sec = [8; 8.1; 8.1; 7.9];
csv_path = fullfile(testCase.TestData.folder, "clock.csv");
writetable(T, csv_path);
report = analyze_omx_joint_log(csv_path);
verifyEqual(testCase, report.ros_clock_backsteps, 1);
verifyEqual(testCase, report.ros_repeated_timestamps, 1);
end

function test_nonfinite_values_are_rejected(testCase)
T = sample_log();
T.joint1(2) = NaN;
csv_path = fullfile(testCase.TestData.folder, "invalid.csv");
writetable(T, csv_path);
verifyError(testCase, @() analyze_omx_joint_log(csv_path), "OMX:InvalidLogValues");
end

function test_missing_column_is_rejected(testCase)
T = sample_log();
T.gripper = [];
csv_path = fullfile(testCase.TestData.folder, "missing.csv");
writetable(T, csv_path);
verifyError(testCase, @() analyze_omx_joint_log(csv_path), "OMX:MissingLogColumns");
end

function test_nonincreasing_time_is_rejected(testCase)
T = sample_log();
T.time_sec = [0; 0.1; 0.1; 0.3];
csv_path = fullfile(testCase.TestData.folder, "time.csv");
writetable(T, csv_path);
verifyError(testCase, @() analyze_omx_joint_log(csv_path), "OMX:NonIncreasingTime");
end

function test_invalid_ros_time_is_rejected_without_a_summary(testCase)
T = sample_log();
T.ros_time_sec = [0; 0.1; NaN; 0.3];
csv_path = fullfile(testCase.TestData.folder, "invalid_ros_time.csv");
writetable(T, csv_path);
verifyError(testCase, @() analyze_omx_joint_log(csv_path), "OMX:InvalidROSTime");
verifyFalse(testCase, isfile(fullfile(testCase.TestData.folder, "invalid_ros_time_summary.csv")));
end

function test_single_sample_is_rejected(testCase)
T = sample_log();
csv_path = fullfile(testCase.TestData.folder, "single.csv");
writetable(T(1, :), csv_path);
verifyError(testCase, @() analyze_omx_joint_log(csv_path), "OMX:TooFewSamples");
end

function T = sample_log()
time_sec = [0; 0.1; 0.2; 0.3];
names = ["time_sec", "joint1", "joint2", "joint3", "joint4", "joint5", ...
    "gripper", "ee_x_m", "ee_y_m", "ee_z_m"];
T = array2table([time_sec, time_sec, zeros(4, 4), time_sec, zeros(4, 3)], ...
    "VariableNames", names);
end
