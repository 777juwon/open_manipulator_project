#!/usr/bin/env python3
"""목표값 전송과 추론 없이 OMX 팔로워의 동기 읽기만 일정 주기로 기록한다."""

import argparse
import csv
from pathlib import Path
import time


MOTOR_IDS = (11, 12, 13, 14, 15, 16)
POSITION_ADDRESS = 132
POSITION_LENGTH = 4
FIELDS = ["iteration", "time_sec", "interval_ms", "read_ms", "comm_result", "waiting_id", "responses"]


def make_traced_handler(handler_class):
    """SDK가 반환한 결과를 변경하지 않고 ID별 응답 결과만 수집한다."""
    class TracedHandler(handler_class):
        def __init__(self):
            super().__init__()
            self.responses = []

        def readRx(self, port, dxl_id, length):
            response = super().readRx(port, dxl_id, length)
            self.responses.append((dxl_id, response[1], response[2]))
            return response

    return TracedHandler()


def read_once(reader, handler, iteration, origin, previous_start, clock=time.perf_counter):
    """그룹 조회 한 번을 수행한다. 실패 응답도 숫자 코드 그대로 기록한다."""
    handler.responses.clear()
    started = clock()
    result = reader.txRxPacket()
    ended = clock()
    waiting_id = next((motor_id for motor_id, code, _ in handler.responses if code != 0), "")
    row = dict(zip(FIELDS, [
        iteration, started - origin,
        "" if previous_start is None else (started - previous_start) * 1000,
        (ended - started) * 1000, result, waiting_id,
        ";".join(f"{motor_id}:{code}:0x{status:02x}" for motor_id, code, status in handler.responses),
    ]))
    return row, started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="팔로워의 고정 장치 경로")
    parser.add_argument("--log-dir", required=True, help="새 검사 로그 폴더")
    parser.add_argument("--fps", type=float, default=30, help="목표 조회 주기")
    parser.add_argument("--duration", type=float, default=30, help="검사 시간, 초")
    args = parser.parse_args()
    if not (0 < args.fps <= 100 and 0 < args.duration <= 300):
        parser.error("fps는 0 초과 100 이하, duration은 0 초과 300 이하로 지정하세요.")

    from dynamixel_sdk import GroupSyncRead, PortHandler
    from dynamixel_sdk.protocol2_packet_handler import Protocol2PacketHandler

    directory = Path(args.log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sync_read.csv"
    port = PortHandler(args.port)
    handler = make_traced_handler(Protocol2PacketHandler)
    reader = GroupSyncRead(port, handler, POSITION_ADDRESS, POSITION_LENGTH)
    count = failures = 0
    started = None
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        try:
            if not port.openPort():
                raise ConnectionError("팔로워 포트를 열 수 없습니다.")
            if not port.setBaudRate(1000000):
                raise ConnectionError("통신 속도를 설정할 수 없습니다.")
            for motor_id in MOTOR_IDS:
                if not reader.addParam(motor_id):
                    raise RuntimeError(f"조회 ID를 등록할 수 없습니다: {motor_id}")
            print("위치 조회만 수행합니다. 목표 관절값·토크·모터 설정을 전송하지 않습니다.", flush=True)
            started = time.perf_counter()
            previous_start = None
            last_flush = started
            while time.perf_counter() - started < args.duration:
                count += 1
                row, previous_start = read_once(reader, handler, count, started, previous_start)
                writer.writerow(row)
                if row["comm_result"] != 0:
                    failures += 1
                    stream.flush()
                    if failures <= 5 or failures % 20 == 0:
                        print(f"조회 {count}: 응답 대기 ID={row['waiting_id']}, "
                              f"결과={row['comm_result']}, 시간={row['read_ms']:.2f} ms", flush=True)
                now = time.perf_counter()
                if now - last_flush >= 1:
                    stream.flush()
                    last_flush = now
                remaining = 1 / args.fps - (now - previous_start)
                if remaining > 0:
                    time.sleep(remaining)
        except KeyboardInterrupt:
            print("사용자 요청으로 조회를 종료합니다.", flush=True)
        finally:
            reader.clearParam()
            port.closePort()
            print(f"조회 {count}회, 통신 결과 실패 {failures}회", flush=True)
            if started is not None:
                elapsed = time.perf_counter() - started
                print(f"경과시간 {elapsed:.3f}초, 평균 조회 빈도 {count / elapsed:.2f} Hz", flush=True)
            print(f"조회 로그: {path}", flush=True)


if __name__ == "__main__":
    main()
