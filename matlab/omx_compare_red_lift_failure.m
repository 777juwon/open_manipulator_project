function report = omx_compare_red_lift_failure(episode_index, options)
% 11월 26일 red 들어 올리기 실패 기록을 선택한 red 시연과 비교한다.
arguments
    episode_index (1,1) double {mustBeInteger,mustBeNonnegative} = 0
    options.RunWindow (1,2) double = [0 Inf]
    options.TeleWindow (1,2) double = [0 Inf]
    options.Visible (1,1) string = "on"
    options.OutputDir (1,1) string = ""
end
project = fileparts(fileparts(mfilename("fullpath")));
workspace = fullfile(project,"docker","workspace");
run_dir = fullfile(workspace,"rollout_logs","toponly_red_rtc_20hz_20261126_071948_395184006");
tele_csv = fullfile(workspace,"validation_references", ...
    "omx_red_dualcam_20261124_081757_314161051","tele_reference.csv");
report = compare_omx_rollout(run_dir,tele_csv,episode_index, ...
    RunWindow=options.RunWindow,TeleWindow=options.TeleWindow, ...
    Visible=options.Visible,OutputDir=options.OutputDir,Outcome="lift_failed");
end
