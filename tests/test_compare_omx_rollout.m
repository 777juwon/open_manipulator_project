function tests = test_compare_omx_rollout
tests = functiontests(localfunctions);
end

function setupOnce(testCase)
testCase.TestData.old_path = path;
addpath(fullfile(fileparts(fileparts(mfilename("fullpath"))), "matlab"));
end

function teardownOnce(testCase)
path(testCase.TestData.old_path);
end

function setup(testCase)
testCase.TestData.root = string(tempname);
mkdir(testCase.TestData.root);
make_sample(testCase.TestData.root);
testCase.TestData.warning = warning("off", "OMX:ReferenceCalibrationAssumed");
end

function teardown(testCase)
warning(testCase.TestData.warning);
close all force;
rmdir(testCase.TestData.root, "s");
end

function test_previous_sent_goal_not_same_row_target(testCase)
root = testCase.TestData.root;
report = compare_omx_rollout(root, fullfile(root,"tele_reference.csv"), 0, ...
    Visible="off", OutputDir=fullfile(root,"analysis"), Outcome="lift_failed");
G = report.active_goal_gap;
verifyTrue(testCase, isnan(G.active_sent_elbow_flex(1)));
verifyEqual(testCase, G.active_sent_elbow_flex(2), 1);
verifyEqual(testCase, G.active_sent_elbow_flex(3), 2);
verifyEqual(testCase, height(report.observations), 3);
verifyEqual(testCase, report.notes.outcome_user_label, "lift_failed");
verifyFalse(testCase, report.notes.tele_path_rmse_computed);
verifyTrue(testCase, isfile(fullfile(report.output_dir,"command_response.png")));
verifyTrue(testCase, isfile(fullfile(report.output_dir,"tele_progress.png")));
end

function test_failed_command_invalidates_active_goal(testCase)
root = testCase.TestData.root;
A = readtable(fullfile(root,"actions.csv"), TextType="string");
A.status(2) = "error";
writetable(A,fullfile(root,"actions.csv"));
r = compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis"));
verifyTrue(testCase,isnan(r.active_goal_gap.active_sent_elbow_flex(3)));
end

function test_overlap_measurement_is_not_assigned(testCase)
root = testCase.TestData.root;
A = readtable(fullfile(root,"actions.csv"), TextType="string");
A.start_ns(2) = 1190000000; A.goal_write_start_ns(2) = 1190000000;
A.goal_write_end_ns(2) = 1201000000; A.end_ns(2) = 1202000000;
writetable(A, fullfile(root,"actions.csv"));
r = compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis"));
verifyTrue(testCase,isnan(r.active_goal_gap.active_sent_elbow_flex(3)));
end

function test_task_mismatch_is_rejected_before_output(testCase)
root = testCase.TestData.root;
D = readtable(fullfile(root,"tele_reference.csv"), TextType="string");
D.task(:) = "다른 작업"; writetable(D,fullfile(root,"tele_reference.csv"));
verifyError(testCase,@() compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis")),"OMX:ComparisonTask");
verifyFalse(testCase,isfolder(fullfile(root,"analysis")));
end

function test_invalid_time_rejected(testCase)
root = testCase.TestData.root;
O = readtable(fullfile(root,"observations.csv"), TextType="string");
O.state_end_ns(2) = O.state_start_ns(2)-1;
writetable(O, fullfile(root,"observations.csv"));
verifyError(testCase,@() compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis")),"OMX:ComparisonTime");
end

function test_missing_successful_write_start_is_rejected(testCase)
root = testCase.TestData.root;
A = readtable(fullfile(root,"actions.csv"), TextType="string");
A.goal_write_start_ns(1) = NaN;
writetable(A,fullfile(root,"actions.csv"));
verifyError(testCase,@() compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis")),"OMX:ComparisonTime");
end

function test_unknown_normalization_is_rejected(testCase)
root = testCase.TestData.root;
meta = jsondecode(fileread(fullfile(root,"run_metadata.json")));
meta.motor_configuration.elbow_flex.norm_mode = "degrees";
write_json(fullfile(root,"run_metadata.json"),meta);
verifyError(testCase,@() compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis")),"OMX:ComparisonUnits");
end

function test_no_causal_samples_is_rejected(testCase)
root = testCase.TestData.root;
A = readtable(fullfile(root,"actions.csv"), TextType="string");
for name = ["start_ns","end_ns","goal_write_start_ns","goal_write_end_ns"]
    A.(name) = A.(name)+1e9;
end
writetable(A,fullfile(root,"actions.csv"));
verifyError(testCase,@() compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis")),"OMX:NoValidGoalObservations");
end

function test_window_keeps_goal_sent_before_window(testCase)
root = testCase.TestData.root;
r = compare_omx_rollout(root,fullfile(root,"tele_reference.csv"),0, ...
    Visible="off",OutputDir=fullfile(root,"analysis"),RunWindow=[0.09 0.21]);
verifyEqual(testCase,r.summary.ValidObservations,repmat(2,6,1));
verifyEqual(testCase,r.active_goal_gap.active_sent_elbow_flex(2),1);
end

function make_sample(root)
j = ["shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper"];
O = table((0:3).', ["control";"control";"control";"shutdown"], ...
    [1;1.1;1.2;1.3]*1e9, [1.002;1.102;1.202;1.302]*1e9, ...
    'VariableNames',{'observation_index','phase','state_start_ns','state_end_ns'});
A = table(["control";"control";"shutdown"], [1.01;1.11;1.31]*1e9, ...
    [1.02;1.12;1.32]*1e9, [1.015;1.115;1.315]*1e9, [1.019;1.119;1.319]*1e9, ...
    repmat("api_returned",3,1), 'VariableNames', ...
    {'phase','start_ns','end_ns','goal_write_start_ns','goal_write_end_ns','status'});
D = table(zeros(4,1),(0:3).',repmat("가상 작업",4,1),[0;0.08;0.16;0.24], ...
    'VariableNames',{'episode_index','frame_index','task','time_sec'});
for name = j
    O.("actual_"+name) = [0;0.5;1.5;100];
    A.("requested_"+name) = [2;3;100];
    A.("sent_"+name) = [1;2;100];
    D.("state_"+name) = [0;0.4;1.4;2];
end
writetable(O,fullfile(root,"observations.csv"));
writetable(A,fullfile(root,"actions.csv"));
writetable(D,fullfile(root,"tele_reference.csv"));
meta = struct("task","가상 작업","use_degrees",false,"recording_complete",true);
for k = 1:numel(j)
    mode = "range_m100_100";
    if j(k) == "gripper"; mode = "range_0_100"; end
    meta.motor_configuration.(j(k)) = struct("id",k+10,"norm_mode",mode);
end
write_json(fullfile(root,"run_metadata.json"),meta);
write_json(fullfile(root,"tele_reference_metadata.json"),struct("time_basis","measured_joint_read_midpoint"));
end

function write_json(file, value)
f = fopen(file,"w","n","UTF-8");
cleanup = onCleanup(@() fclose(f));
fprintf(f,"%s",jsonencode(value));
end
