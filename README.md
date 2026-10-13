# ROBOTIS OpenMANIPULATOR ROS 2 Packages

This repository contains the official ROS 2 packages for the ROBOTIS OpenMANIPULATOR platform. These packages provide the necessary interfaces and tools to control the robot, integrate with its sensors, and develop physical AI applications, including those utilizing frameworks like LeRobot. For detailed usage instructions, please refer to the documentation below.
  - [Documentation for OMY](https://ai.robotis.com/omy/introduction_omy.html)
  - [Documentation for OpenMANIPULATOR-X](https://emanual.robotis.com/docs/en/platform/openmanipulator_x/overview/)

To learn more about the Physical AI Tools, visit:
  - [Physical AI Tools](https://github.com/ROBOTIS-GIT/physical_ai_tools)

To explore our open-source platforms in a simulation environment, visit:
  - [Simulation Models](https://github.com/ROBOTIS-GIT/robotis_mujoco_menagerie)

For usage instructions and demonstrations of the OpenMANIPULATOR, check out:
  - [Tutorial Videos](https://www.youtube.com/@ROBOTISOpenSourceTeam)

To access datasets and pre-trained models for our open-source platforms, see:
  - [AI Models & Datasets](https://huggingface.co/ROBOTIS)

To use the Docker image for running ROS packages and Physical AI tools with the OpenMANIPULATOR, visit:
  - [Docker Images](https://hub.docker.com/r/robotis/ros/tags)

## OMX-F Gazebo LeRobot dataset collection

This repository includes a reproducible ROS 2 Jazzy, Gazebo, and LeRobot 0.6.1
environment for collecting successful OMX-F pick-and-place demonstrations.
Every saved episode randomizes the cube position, cube color, and light
intensity. Failed grasps and failed placements are discarded automatically.

### 1. Clone and build the container

```bash
git clone https://github.com/777juwon/open_manipulator_project.git
cd open_manipulator_project
git pull origin main

xhost +local:docker
cd docker
docker compose build
docker compose up -d
```

The first build installs ROS 2 Jazzy, Gazebo, LeRobot 0.6.1, PyTorch 2.11.0,
and the project packages, so it can take a while.

### 2. Start Gazebo

Open terminal 1 and enter the container:

```bash
docker exec -it open_manipulator bash
source /opt/ros/jazzy/setup.bash
source /root/ros2_ws/install/setup.bash
ros2 launch open_manipulator_bringup omx_f_pick_place_gazebo.launch.py
```

Wait until `arm_controller`, `gripper_controller`, and
`joint_state_broadcaster` are active.

### 3. Collect 50 successful randomized episodes

Keep Gazebo running. Open terminal 2:

```bash
docker exec -it open_manipulator bash
source /opt/ros/jazzy/setup.bash
source /root/ros2_ws/install/setup.bash
ros2 run open_manipulator_lerobot omx_f_auto_collect \
  --episodes 50 \
  --max-attempts 10
```

Only episodes that pass both the cube-lift and in-bin checks are saved. The
LeRobotDataset v3 output is available inside the container at:

```text
/workspace/lerobot_datasets/omx_f_auto_pick_place_50
```

The same files persist on the host at:

```text
docker/workspace/lerobot_datasets/omx_f_auto_pick_place_50
```

If collection is interrupted, keep the same dataset directory and resume with:

```bash
ros2 run open_manipulator_lerobot omx_f_auto_collect \
  --episodes 50 \
  --max-attempts 10 \
  --resume
```

To hide only the separate wrist-camera viewer while still recording images:

```bash
ros2 launch open_manipulator_bringup \
  omx_f_pick_place_gazebo.launch.py show_camera:=false
```

After finishing, restore X server access control on the host:

```bash
xhost -local:docker
```
