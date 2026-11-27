function report = compare_omx_rollout(run_dir, tele_csv, episode_index, options)
% 실물 모델의 명령 응답과 같은 작업의 tele 시연을 별도 기준으로 비교한다.
% 진행률 비교는 시간 비율을 맞춘 것이며 집기 단계 자동 정렬이나 성공 판정이 아니다.
arguments
    run_dir (1,1) string
    tele_csv (1,1) string
    episode_index (1,1) double {mustBeInteger, mustBeNonnegative} = 0
    options.RunWindow (1,2) double = [0 Inf]
    options.TeleWindow (1,2) double = [0 Inf]
    options.Outcome (1,1) string = "not_assessed"
    options.Visible (1,1) string {mustBeMember(options.Visible,["on","off"])} = "on"
    options.OutputDir (1,1) string = ""
end

joints = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"];
meta = jsondecode(fileread(fullfile(run_dir, "run_metadata.json")));
if meta.use_degrees
    error("OMX:ComparisonUnits", "현재 비교기는 정규화 관절값 실행용입니다. 도 단위 실행과 섞지 마세요.");
end
if ~meta.recording_complete
    warning("OMX:IncompleteRecording", "불완전 기록입니다. 분석 결과의 누락 여부를 확인하세요.");
end
motor_ids = zeros(numel(joints),1);
for j = 1:numel(joints)
    motor = meta.motor_configuration.(joints(j));
    mode = "range_m100_100";
    if joints(j) == "gripper"; mode = "range_0_100"; end
    if string(motor.norm_mode) ~= mode
        error("OMX:ComparisonUnits", "모터 정규화 모드가 예상과 다릅니다: %s", joints(j));
    end
    motor_ids(j) = motor.id;
end
reference_meta = jsondecode(fileread(fullfile(fileparts(tele_csv), "tele_reference_metadata.json")));
if ~strcmp(reference_meta.time_basis, "measured_joint_read_midpoint")
    error("OMX:ReferenceTime", "시연의 실측 관절 시각이 필요합니다.");
end
O = readtable(fullfile(run_dir, "observations.csv"), TextType="string", VariableNamingRule="preserve");
A = readtable(fullfile(run_dir, "actions.csv"), TextType="string", VariableNamingRule="preserve");
D = readtable(tele_csv, TextType="string", VariableNamingRule="preserve");
require_columns(O, ["observation_index","phase","state_start_ns","state_end_ns", "actual_"+joints]);
require_columns(A, ["phase","start_ns","end_ns","goal_write_start_ns","goal_write_end_ns","status", ...
    "requested_"+joints, "sent_"+joints]);
require_columns(D, ["episode_index","frame_index","task","time_sec","state_"+joints]);
O = O(O.phase == "control", :);
A = A(A.phase == "control", :);
D = D(D.episode_index == episode_index, :);
if height(O) < 2 || height(A) < 1 || height(D) < 2
    error("OMX:ComparisonSamples", "실행 관측·명령 또는 선택 시연의 기록 수가 부족합니다.");
end
if any(D.task ~= string(meta.task))
    error("OMX:ComparisonTask", "실행과 시연의 작업 지시문이 다릅니다. 같은 색상의 시연을 선택하세요.");
end
numeric_finite(O{:, cellstr(["state_start_ns","state_end_ns","actual_"+joints])});
numeric_finite(D{:, cellstr(["time_sec","state_"+joints])});
numeric_finite(A{:, cellstr(["start_ns","end_ns","requested_"+joints])});
if any(O.state_end_ns < O.state_start_ns) || any(A.end_ns < A.start_ns) || ...
        any(diff(O.state_start_ns) <= 0) || any(diff(A.start_ns) <= 0) || ...
        any(diff(D.time_sec) <= 0)
    error("OMX:ComparisonTime", "기록 시각이 증가하지 않거나 조회·전송 구간 순서가 잘못됐습니다.");
end
if any(O.state_start_ns(2:end) <= O.state_end_ns(1:end-1))
    error("OMX:ComparisonTime", "서로 겹치는 관절 관측 구간입니다.");
end
if numel(unique(O.observation_index)) ~= height(O) || numel(unique(D.frame_index)) ~= height(D)
    error("OMX:ComparisonSamples", "관측 또는 시연 프레임 번호가 중복됐습니다.");
end
success = A.status == "api_returned";
sent = A{:, cellstr("sent_"+joints)};
if ~isnumeric(sent)
    error("OMX:ComparisonValues", "전송 목표값 열은 숫자여야 합니다.");
end
if any(~isfinite(sent(success,:)), "all") || ...
        any(~isfinite(A.goal_write_start_ns(success))) || ...
        any(~isfinite(A.goal_write_end_ns(success))) || ...
        any(A.goal_write_start_ns(success) < A.start_ns(success)) || ...
        any(A.goal_write_end_ns(success) < A.goal_write_start_ns(success)) || ...
        any(A.goal_write_end_ns(success) > A.end_ns(success))
    error("OMX:ComparisonTime", "성공 반환된 전송의 목표값 또는 시각을 확인할 수 없습니다.");
end

% 관절 조회 구간 전체가 전송 완료 이후이고 다음 명령 시작 전인 관측만 연결한다.
active_sent = nan(height(O), numel(joints));
active_action = nan(height(O), 1);
for i = 1:height(O)
    k = find(A.start_ns <= O.state_end_ns(i), 1, "last");
    if isempty(k) || ~success(k) || A.goal_write_end_ns(k) > O.state_start_ns(i) || ...
            A.end_ns(k) > O.state_start_ns(i)
        continue
    end
    active_sent(i,:) = sent(k,:);
    active_action(i) = k - 1;
end
origin_ns = (O.state_start_ns(1) + O.state_end_ns(1)) / 2;
run_time = ((O.state_start_ns + O.state_end_ns) / 2 - origin_ns) / 1e9;
tele_time = D.time_sec - D.time_sec(1);
run_mask = select_window(run_time, options.RunWindow);
tele_mask = select_window(tele_time, options.TeleWindow);
actual = O{:, cellstr("actual_"+joints)};
tele = D{:, cellstr("state_"+joints)};
gap = active_sent - actual;
selected_gap = gap(run_mask,:);
valid_count = sum(isfinite(selected_gap), 1).';
if ~any(valid_count)
    error("OMX:NoValidGoalObservations", "선택 구간에 전송 이후로 연결할 수 있는 관절 관측이 없습니다.");
end
gap_rmse = sqrt(mean(selected_gap.^2, 1, "omitnan")).';
max_gap = max(abs(selected_gap), [], 1, "omitnan").';
clipped = abs(A{:, cellstr("requested_"+joints)} - sent) > 1e-4;
action_time = (A.start_ns-origin_ns)/1e9;
action_mask = action_time >= options.RunWindow(1) & action_time <= options.RunWindow(2);
clipped_count = sum(clipped(action_mask & success,:), 1).';
summary = table(joints.', motor_ids, valid_count, gap_rmse, max_gap, clipped_count, ...
    'VariableNames', {'Joint','MotorID','ValidObservations','SampledGoalGapRMSE', ...
    'MaxAbsSampledGoalGap','ClippedCommands'});
warning("OMX:ReferenceCalibrationAssumed", ...
    "시연 데이터에는 수집 당시 캘리브레이션이 없습니다. 같은 정규화·영점이라는 가정 아래 경로를 표시합니다.");

output = options.OutputDir;
if strlength(output) == 0
    project = fileparts(fileparts(mfilename("fullpath")));
    [~, run_name] = fileparts(run_dir);
    stamp = string(datetime("now", Format="yyyyMMdd_HHmmss_SSS"));
    output = fullfile(project, "matlab_logs", "real_rollout", run_name+"_"+stamp);
end
if isfolder(output) || isfile(output)
    error("OMX:ComparisonOutputExists", "기존 분석 결과를 덮어쓰지 않습니다: %s", output);
end
mkdir(output);
figures = gobjects(1,4);
figures(1) = make_figure("관절 명령과 실제 응답", options.Visible);
layout = tiledlayout(3,2);
for j = 1:numel(joints)
    nexttile;
    plot(run_time, actual(:,j), "LineWidth", 1.3); hold on;
    stairs(action_time, A{:, "requested_"+joints(j)}, "--");
    plot(run_time, active_sent(:,j), ".", "MarkerSize", 6);
    xlim([run_time(find(run_mask,1)), run_time(find(run_mask,1,"last"))]);
    decorate(joints(j));
    if j == 1; legend("실제 관절값", "요청 목표", "관측 시점 유효 목표", Location="best"); end
end
title(layout, "모델 수행 구간: 목표와 실제 관절값 (정규화 단위)");
exportgraphics(figures(1), fullfile(output,"command_response.png"));

figures(2) = make_figure("전송 목표와 이후 관측의 차이", options.Visible);
layout = tiledlayout(3,2);
for j = 1:numel(joints)
    nexttile; plot(run_time(run_mask), gap(run_mask,j)); yline(0, "--");
    decorate(joints(j));
end
title(layout, "유효 전송 목표 - 이후 관측값: 동적 차이이며 정상상태 제어 오차가 아님");
exportgraphics(figures(2), fullfile(output,"active_goal_gap.png"));

r = run_time(run_mask); r = r-r(1);
d = tele_time(tele_mask); d = d-d(1);
for progress = 0:1
    figures(3+progress) = make_figure("tele 시연과 모델 실행 비교", options.Visible);
    layout = tiledlayout(3,2);
    rx = r; dx = d;
    if progress; rx = 100*r/r(end); dx = 100*d/d(end); end
    for j = 1:numel(joints)
        nexttile; plot(dx, tele(tele_mask,j), "--", "LineWidth", 1.2); hold on;
        plot(rx, actual(run_mask,j), "LineWidth", 1.2); grid on;
        title(joints(j), Interpreter="none"); ylabel("정규화 관절값");
        if progress
            xlabel("구간 경과시간 비율 [%]"); xlim([0 100]);
        else
            xlabel("구간 시작 후 시간 [s]"); xlim([0 max(r(end),d(end))]);
        end
        if j == 1; legend("tele 시연", "모델 실제 동작", Location="best"); end
    end
    label = "실제 경과시간 비교";
    filename = "tele_elapsed.png";
    if progress
        label = "시간 비율 비교: 접근·집기 단계가 자동으로 정렬된 것은 아님";
        filename = "tele_progress.png";
    end
    title(layout, sprintf("%s | 실행 %.2fs, 시연 %.2fs, episode %d", label, r(end), d(end), episode_index));
    exportgraphics(figures(3+progress), fullfile(output,filename));
end

gap_table = array2table([O.observation_index,run_time,active_action,actual,active_sent,gap], ...
    'VariableNames', ["observation_index","time_sec","active_action_row_zero_based", ...
    "actual_"+joints,"active_sent_"+joints,"gap_"+joints]);
writetable(gap_table(run_mask,:), fullfile(output,"active_goal_gap.csv"));
writetable(summary, fullfile(output,"joint_summary.csv"));
notes = struct("run_dir",run_dir,"tele_csv",tele_csv,"episode_index",episode_index, ...
    "outcome_user_label",options.Outcome,"task",string(meta.task), ...
    "run_window_sec",options.RunWindow,"tele_window_sec",options.TeleWindow, ...
    "run_selected_span_sec",r(end),"tele_selected_span_sec",d(end), ...
    "run_recording_complete",meta.recording_complete, ...
    "selected_observations",nnz(run_mask),"valid_goal_observations",valid_count.', ...
    "goal_observation_coverage",valid_count.'/nnz(run_mask), ...
    "tele_calibration_verified",false,"tele_path_rmse_computed",false, ...
    "note","시간 비율은 작업 단계 정렬이 아닙니다. 목표 차이는 전송 이후 관측만 사용하며 도·라디안 오차가 아닙니다.");
file = fopen(fullfile(output,"comparison_metadata.json"), "w", "n", "UTF-8");
clean = onCleanup(@() fclose(file));
fprintf(file, "%s", jsonencode(notes, PrettyPrint=true));
report = struct("output_dir",output,"summary",summary,"figures",figures, ...
    "observations",O,"actions",A,"tele",D,"active_goal_gap",gap_table, ...
    "run_time",run_time,"tele_time",tele_time,"notes",notes);
disp(summary);
fprintf("분석 저장: %s\n", output);
disp("원래 시연과 경로가 다르다는 사실만으로 실패라고 판정하지 않습니다.");
end

function require_columns(T, names)
if ~all(ismember(names, string(T.Properties.VariableNames)))
    error("OMX:ComparisonColumns", "필요한 시간·관절 열이 없습니다.");
end
end

function numeric_finite(values)
if ~isnumeric(values) || any(~isfinite(values), "all")
    error("OMX:ComparisonValues", "필수 기록에 숫자가 아닌 값, NaN 또는 무한대가 있습니다.");
end
end

function selected = select_window(time, window)
if any(isnan(window)) || window(1) < 0 || window(2) <= window(1)
    error("OMX:ComparisonWindow", "비교 구간은 0 이상인 시작과 더 큰 종료 시각으로 지정하세요.");
end
selected = time >= window(1) & time <= window(2);
if nnz(selected) < 2
    error("OMX:ComparisonWindow", "선택 구간에 관측이 두 개 이상 필요합니다.");
end
end

function f = make_figure(name, visible)
f = figure(Name=name, Visible=visible, Position=[100 100 1100 850]);
end

function decorate(joint)
grid on; title(joint, Interpreter="none");
xlabel("실행 관측 시작 후 시간 [s]"); ylabel("정규화 관절값");
end
