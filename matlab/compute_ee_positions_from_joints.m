function ee_xyz = compute_ee_positions_from_joints(joint_log, gripper_log)
% 관절 기록과 OMX-F 모델로 로봇 손끝의 XYZ 좌표를 계산한다.
%
% joint_log는 joint1부터 joint5까지를 열로 갖는 N행 5열 배열이다.
% gripper_log는 선택 입력이며 그리퍼에만 적용되고 link5의 FK에는 영향을 주지 않는다.

project_root = fileparts(fileparts(mfilename("fullpath")));
urdf_path = fullfile(project_root, "open_manipulator_description", "urdf", "omx_f", "omx_f.urdf");
ee_link = "end_effector_link";

robot = importrobot(urdf_path);
cfg = homeConfiguration(robot);
joint_names = string({cfg.JointName});

joint_indices = zeros(1, 5);
for j = 1:5
    joint_name = "joint" + string(j);
    idx = find(joint_names == joint_name, 1);
    if isempty(idx)
        error("URDF에 " + joint_name + " 관절이 없습니다.");
    end
    joint_indices(j) = idx;
end

gripper1_idx = find(joint_names == "gripper_joint_1", 1);
gripper2_idx = find(joint_names == "gripper_joint_2", 1);

sample_count = size(joint_log, 1);
ee_xyz = zeros(sample_count, 3);

for i = 1:sample_count
    qi = cfg;

    for j = 1:5
        qi(joint_indices(j)).JointPosition = joint_log(i, j);
    end

    if nargin >= 2 && ~isempty(gripper_log) && ~isnan(gripper_log(i))
        if ~isempty(gripper1_idx)
            qi(gripper1_idx).JointPosition = gripper_log(i);
        end
        if ~isempty(gripper2_idx)
            qi(gripper2_idx).JointPosition = -gripper_log(i);
        end
    end

    transform = getTransform(robot, qi, ee_link);
    ee_xyz(i, :) = transform(1:3, 4).';
end
end
