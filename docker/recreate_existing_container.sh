#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="open_manipulator"
IMAGE_NAME="robotis/open-manipulator:5.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}/workspace"
AGENT_SOCKET_DIR="/tmp/open_manipulator_agent_socket"

mkdir -p "${WORKSPACE_DIR}"
mkdir -p "${AGENT_SOCKET_DIR}"

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "Required local image not found: ${IMAGE_NAME}" >&2
  echo "Pull or build that image first, then run this script again." >&2
  exit 1
fi

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "Removing existing container: ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}"
fi

if [[ -n "${DISPLAY:-}" ]] && command -v xhost >/dev/null 2>&1; then
  xhost +local:docker >/dev/null 2>&1 || true
fi

echo "Creating ${CONTAINER_NAME} from existing image ${IMAGE_NAME}"
echo "Project mount: ${PROJECT_DIR} -> /root/ros2_ws/src/open_manipulator"

docker run -d \
  --name "${CONTAINER_NAME}" \
  --tty \
  --gpus all \
  --restart always \
  --privileged \
  --network host \
  --ipc host \
  --cap-add SYS_NICE \
  --ulimit rtprio=99 \
  --ulimit rttime=-1 \
  --ulimit memlock=8428281856 \
  --env "DISPLAY=${DISPLAY:-}" \
  --env QT_X11_NO_MITSHM=1 \
  --volume /dev:/dev \
  --volume /dev/shm:/dev/shm \
  --volume /tmp/.X11-unix:/tmp/.X11-unix:rw \
  --volume /tmp/.docker.xauth:/tmp/.docker.xauth:rw \
  --volume "${WORKSPACE_DIR}:/workspace" \
  --volume "${PROJECT_DIR}:/root/ros2_ws/src/open_manipulator" \
  --volume "${AGENT_SOCKET_DIR}:/var/run/agent" \
  "${IMAGE_NAME}"

echo
echo "Container started. Enter it with:"
echo "  docker exec -it ${CONTAINER_NAME} bash"
