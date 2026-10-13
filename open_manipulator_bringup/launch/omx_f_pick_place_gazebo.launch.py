#!/usr/bin/env python3

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.actions import RegisterEventHandler, SetEnvironmentVariable, TimerAction
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import xacro


def generate_launch_description():
    description_share = get_package_share_directory('open_manipulator_description')
    bringup_share = get_package_share_directory('open_manipulator_bringup')

    resource_path = SetEnvironmentVariable(
        name='GZ_SIM_RESOURCE_PATH',
        value=[
            os.path.join(bringup_share, 'worlds'),
            ':' + str(Path(description_share).parent.resolve()),
        ],
    )

    show_camera = LaunchConfiguration('show_camera')
    arguments = DeclareLaunchArgument(
        'show_camera', default_value='true',
        description='Open an rqt window showing the simulated wrist camera',
    )

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('ros_gz_sim'), 'launch'),
            '/gz_sim.launch.py',
        ]),
        launch_arguments={'gz_args': 'omx_f_pick_place.sdf -v 1 -r'}.items(),
    )

    camera_xacro = os.path.join(
        bringup_share, 'worlds', 'omx_f_with_wrist_camera.urdf.xacro'
    )
    robot_description = xacro.process_file(
        camera_xacro,
        mappings={'use_sim': 'true', 'config_type': 'omx_f_pick_place'}
    ).toprettyxml(indent='  ')

    robot_state_publisher = Node(
        package='robot_state_publisher', executable='robot_state_publisher',
        output='screen', parameters=[{'robot_description': robot_description}],
    )
    spawn_robot = Node(
        package='ros_gz_sim', executable='create', output='screen',
        arguments=[
            '-string', robot_description,
            '-x', '0.0', '-y', '0.0', '-z', '0.0',
            '-R', '0.0', '-P', '0.0', '-Y', '0.0',
            '-name', 'omx_f', '-allow_renaming', 'true', '-use_sim', 'true',
        ],
    )

    joint_state_spawner = Node(
        package='controller_manager', executable='spawner',
        arguments=['joint_state_broadcaster', '--controller-manager', '/controller_manager'],
        output='screen',
    )
    arm_spawner = Node(
        package='controller_manager', executable='spawner',
        arguments=['arm_controller'], output='screen',
    )
    gripper_spawner = Node(
        package='controller_manager', executable='spawner',
        arguments=['gripper_controller'], output='screen',
    )

    bridge = Node(
        package='ros_gz_bridge', executable='parameter_bridge', output='screen',
        arguments=[
            '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock',
            '/omx_f/wrist_camera/image_raw@sensor_msgs/msg/Image[gz.msgs.Image',
            '/omx_f/wrist_camera/camera_info@sensor_msgs/msg/CameraInfo[gz.msgs.CameraInfo',
        ],
    )
    camera_view = Node(
        package='open_manipulator_lerobot', executable='omx_f_camera_view',
        name='wrist_camera_view',
        condition=IfCondition(show_camera), output='screen',
    )

    return LaunchDescription([
        arguments,
        RegisterEventHandler(OnProcessExit(
            target_action=spawn_robot,
            on_exit=[TimerAction(period=3.0, actions=[joint_state_spawner])],
        )),
        RegisterEventHandler(OnProcessExit(
            target_action=joint_state_spawner,
            on_exit=[arm_spawner, gripper_spawner],
        )),
        resource_path,
        gazebo,
        robot_state_publisher,
        spawn_robot,
        bridge,
        camera_view,
    ])
