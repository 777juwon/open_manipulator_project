cmake_minimum_required(VERSION 3.8)
project(open_manipulator_lerobot)

find_package(ament_cmake REQUIRED)

install(PROGRAMS
  scripts/omx_f_camera_view
  scripts/omx_f_auto_collect
  scripts/omx_f_lerobot_record
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
