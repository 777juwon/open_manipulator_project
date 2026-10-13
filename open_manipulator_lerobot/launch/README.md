# OMX-F Gazebo to LeRobotDataset

Start the camera-enabled simulation:

```bash
ros2 launch open_manipulator_bringup omx_f_pick_place_gazebo.launch.py
```

In a second container terminal, start the recorder:

```bash
source /opt/ros/jazzy/setup.bash
source /root/ros2_ws/install/setup.bash
ros2 run open_manipulator_lerobot omx_f_lerobot_record
```

Use `r`, `s`, `d`, and `q` followed by Enter to control episodes. The default
dataset is stored persistently at `/workspace/lerobot_datasets/omx_f_pick_place`.

## Automatic 50-episode Gazebo collection

The automatic collector records 50 successful pick-and-place episodes. Every
episode randomizes the cube position, cube color, and light intensity within a
safe reachable range. Failed grasps or placements are discarded and retried.
It is for Gazebo only.

Build once in the container:

```bash
cd /root/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select open_manipulator_lerobot
source install/setup.bash
```

Start Gazebo in terminal 1:

```bash
ros2 launch open_manipulator_bringup omx_f_pick_place_gazebo.launch.py show_camera:=false
```

After all controllers are active, run the collector in terminal 2:

```bash
source /opt/ros/jazzy/setup.bash
source /root/ros2_ws/install/setup.bash
ros2 run open_manipulator_lerobot omx_f_auto_collect
```

The resulting LeRobotDataset v3 is written to
`/workspace/lerobot_datasets/omx_f_auto_pick_place_50`. If collection is
interrupted, run the last command again with `--resume`.
