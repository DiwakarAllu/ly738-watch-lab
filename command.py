import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

VENDOR_SERVICE_UUID = "0000ffff-0000-1000-8000-00805f9b34fb"
VENDOR_WRITE_UUID = "0000ff22-0000-1000-8000-00805f9b34fb"
SCAN_TIMEOUT = 20.0
CONNECT_TIMEOUT = 30.0
TEST_DURATION = 20
LOGCAT_PATH = Path(__file__).with_name("logcat.txt")

START_FACE_UPLOAD = "开始升级表盘"
FACE_UPLOAD_DATA = "正在升级表盘数据"
FINISH_FACE_UPLOAD = "发送升级文件完成!"
COMMAND_LOG_PATTERN = re.compile(
    r"(?P<timestamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
    r".*?--(?P<name>.+?)\(ID:(?P<id>\d+)\)"
    r"命令已执行==类型write==value:(?P<value>[0-9a-fA-F]+)"
    r"==命令结果==(?P<result>\w+)"
)

FEATURES = {
    "1": (
        "Find watch (brief vibration/sound, if supported)",
        bytes.fromhex("cd000612010b000101"),
        None,
    ),
    "2": (
        "Heart-rate sensor test (20 seconds)",
        bytes.fromhex("cd000712012400020001"),
        bytes.fromhex("cd000712012400020000"),
    ),
    "3": (
        "SpO2 sensor test (20 seconds)",
        bytes.fromhex("cd000712012400020201"),
        bytes.fromhex("cd000712012400020200"),
    ),
    "4": (
        "Blood-pressure sensor test (20 seconds; not a medical reading)",
        bytes.fromhex("cd000712012400020101"),
        bytes.fromhex("cd000712012400020100"),
    ),
}


@dataclass(frozen=True)
class FaceTransfer:
    start_id: int
    finish_id: int
    start_payload: bytes
    data_packets: tuple[tuple[int, datetime, bytes], ...]
    finish_payload: bytes
    start_time: datetime
    finish_time: datetime


def load_captured_face_transfers() -> list[FaceTransfer]:
    if not LOGCAT_PATH.is_file():
        raise BleakError(f"Captured transfer log not found: {LOGCAT_PATH}")

    transfers = []
    active = None
    for line_number, line in enumerate(
        LOGCAT_PATH.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        match = COMMAND_LOG_PATTERN.search(line)
        if match is None:
            continue

        name = match.group("name")
        if name not in (START_FACE_UPLOAD, FACE_UPLOAD_DATA, FINISH_FACE_UPLOAD):
            continue
        if match.group("result") != "true":
            raise BleakError(
                f"Face-upload command on log line {line_number} did not succeed."
            )

        payload_hex = match.group("value")
        if len(payload_hex) % 2:
            raise BleakError(f"Invalid hex payload on log line {line_number}.")
        payload = bytes.fromhex(payload_hex)
        timestamp = datetime.strptime(
            f"2000 {match.group('timestamp')}", "%Y %m-%d %H:%M:%S.%f"
        )
        command_id = int(match.group("id"))

        if name == START_FACE_UPLOAD:
            if active is not None:
                raise BleakError(
                    "The log contains a new face upload before the previous "
                    "upload completed."
                )
            active = {
                "start_id": command_id,
                "start_payload": payload,
                "data_packets": [],
                "start_time": timestamp,
            }
        elif name == FACE_UPLOAD_DATA:
            if active is not None:
                active["data_packets"].append((command_id, timestamp, payload))
        elif active is not None:
            if not active["data_packets"]:
                raise BleakError(
                    f"Face upload {active['start_id']} has no captured data packets."
                )
            expected_ids = range(active["start_id"] + 1, command_id)
            actual_ids = [packet_id for packet_id, _, _ in active["data_packets"]]
            if actual_ids != list(expected_ids):
                raise BleakError(
                    f"Face upload {active['start_id']}-{command_id} has missing "
                    "or interleaved commands in the log; refusing to replay it."
                )
            if any(len(packet) > 20 for _, _, packet in active["data_packets"]):
                raise BleakError(
                    f"Face upload {active['start_id']}-{command_id} contains an "
                    "oversized BLE write; refusing to replay it."
                )
            transfers.append(
                FaceTransfer(
                    start_id=active["start_id"],
                    finish_id=command_id,
                    start_payload=active["start_payload"],
                    data_packets=tuple(active["data_packets"]),
                    finish_payload=payload,
                    start_time=active["start_time"],
                    finish_time=timestamp,
                )
            )
            active = None

    if active is not None:
        raise BleakError(
            f"Captured face upload {active['start_id']} is incomplete in logcat."
        )
    if not transfers:
        raise BleakError("No complete watch-face upload was found in logcat.txt.")
    return transfers


async def write_packet(
    client: BleakClient,
    characteristic: BleakGATTCharacteristic,
    payload: bytes,
    *,
    response: bool | None = None,
) -> None:
    if not client.is_connected:
        raise BleakError("The watch disconnected before the GATT write.")
    if response is None:
        response = "write" in characteristic.properties
    await client.write_gatt_char(characteristic, payload, response=response)


def find_vendor_write_characteristic(
    client: BleakClient,
) -> BleakGATTCharacteristic:
    service = client.services.get_service(VENDOR_SERVICE_UUID)
    characteristic = (
        service.get_characteristic(VENDOR_WRITE_UUID) if service is not None else None
    )
    if characteristic is None:
        raise BleakError(
            "The expected watch command characteristic FFFF/FF22 was not found. "
            "No packets were sent. Check that this is the LY738 watch and inspect "
            "its discovered GATT services."
        )
    if not (
        "write" in characteristic.properties
        or "write-without-response" in characteristic.properties
    ):
        raise BleakError(
            f"Watch command characteristic {VENDOR_WRITE_UUID} is not writable."
        )
    return characteristic


async def run_feature(
    client: BleakClient,
    characteristic: BleakGATTCharacteristic,
    feature_name: str,
    start_packet: bytes,
    stop_packet: bytes | None,
) -> None:
    await write_packet(client, characteristic, start_packet)
    print(f"Sent {start_packet.hex()}.")
    if stop_packet is None:
        return

    try:
        print(f"Test running for {TEST_DURATION} seconds...")
        await asyncio.sleep(TEST_DURATION)
    finally:
        print("Stopping sensor test...")
        await write_packet(client, characteristic, stop_packet)
        print(f"Sent {stop_packet.hex()}.")


async def replay_face_transfer(
    client: BleakClient,
    characteristic: BleakGATTCharacteristic,
    transfer: FaceTransfer,
) -> None:
    byte_count = sum(len(packet) for _, _, packet in transfer.data_packets)
    print(
        f"Captured transfer IDs {transfer.start_id}-{transfer.finish_id}: "
        f"{len(transfer.data_packets)} data writes, {byte_count:,} data bytes."
    )
    print(
        "This replays the exact face upload recorded in logcat and replaces "
        "the watch's current face. Keep the watch awake and charged; do not "
        "disconnect during upload. The transfer cannot be rolled back by this script."
    )

    try:
        await write_packet(client, characteristic, transfer.start_payload)
    except (BleakError, TimeoutError, OSError) as error:
        raise BleakError(
            "The watch disconnected or rejected the face-upload start packet. "
            "The upload did not start successfully; reconnect before retrying."
        ) from error
    previous_time = transfer.start_time
    print("Upload started; sending captured image data...")
    for index, (_, timestamp, payload) in enumerate(transfer.data_packets, start=1):
        delay = max((timestamp - previous_time).total_seconds(), 0.0)
        if delay:
            await asyncio.sleep(delay)
        try:
            await write_packet(client, characteristic, payload, response=False)
        except (BleakError, TimeoutError, OSError) as error:
            raise BleakError(
                f"Face upload stopped at data packet {index}/"
                f"{len(transfer.data_packets)}. The watch may contain an "
                "incomplete transfer. Reconnect and restart the entire upload "
                "only after confirming the watch is responsive."
            ) from error
        previous_time = timestamp
        if index % 100 == 0 or index == len(transfer.data_packets):
            print(f"Sent {index}/{len(transfer.data_packets)} data writes.")

    delay = max((transfer.finish_time - previous_time).total_seconds(), 0.0)
    if delay:
        await asyncio.sleep(delay)
    try:
        await write_packet(client, characteristic, transfer.finish_payload)
    except (BleakError, TimeoutError, OSError) as error:
        raise BleakError(
            "All face data was sent, but the finalization packet failed. "
            "The watch may not have applied the new face."
        ) from error
    print(
        "Captured upload sequence sent. The watch may take a few seconds "
        "to validate and apply the face."
    )


def choose_action() -> (
    tuple[str, FaceTransfer | tuple[str, bytes, bytes | None]] | None
):
    while True:
        print("\nAvailable watch actions:")
        for key, (name, _, _) in FEATURES.items():
            print(f"  {key}. {name}")
        print("  f. Replay a captured watch face")
        print("  q. Quit")

        choice = input("Select an action: ").strip().lower()
        if choice == "q":
            return None
        if choice == "f":
            transfers = load_captured_face_transfers()
            print("\nCaptured watch-face uploads:")
            for index, transfer in enumerate(transfers, start=1):
                print(
                    f"  {index}. IDs {transfer.start_id}-{transfer.finish_id}, "
                    f"{len(transfer.data_packets)} data writes"
                )
            selection = input("Choose a face number (or q to cancel): ").strip()
            if selection.lower() == "q":
                continue
            if not selection.isdecimal() or not 1 <= int(selection) <= len(transfers):
                print("Invalid face number.")
                continue

            transfer = transfers[int(selection) - 1]
            byte_count = sum(len(packet) for _, _, packet in transfer.data_packets)
            print(
                f"Selected {len(transfer.data_packets)} data writes "
                f"({byte_count:,} data bytes)."
            )
            print(
                "This replaces the current watch face. Keep the watch awake "
                "and charged; do not disconnect during upload. The transfer "
                "cannot be rolled back by this script."
            )
            if input("Type 'UPLOAD' to confirm: ").strip() == "UPLOAD":
                return ("face", transfer)
            print("Upload cancelled.")
            continue

        feature = FEATURES.get(choice)
        if feature is None:
            print("Choose one of the listed actions.")
            continue
        if input(
            f"Run '{feature[0]}'? Type 'yes' to continue: "
        ).strip().lower() == "yes":
            return ("feature", feature)
        print("Skipped.")


async def run_action(
    client: BleakClient,
    action: tuple[str, FaceTransfer | tuple[str, bytes, bytes | None]],
) -> None:
    print(f"Connected to {client.address}.")
    print("\nDiscovered GATT services:")
    for service in client.services:
        print(f"  {service.uuid} ({service.description})")

    characteristic = find_vendor_write_characteristic(client)
    print(
        f"\nUsing watch command channel {VENDOR_SERVICE_UUID} / "
        f"{characteristic.uuid} (handle 0x{characteristic.handle:04x})."
    )

    action_type, payload = action
    if action_type == "face":
        if not isinstance(payload, FaceTransfer):
            raise BleakError("Invalid watch-face selection.")
        await replay_face_transfer(client, characteristic, payload)
    else:
        if isinstance(payload, FaceTransfer):
            raise BleakError("Invalid feature selection.")
        await run_feature(client, characteristic, *payload)


async def main() -> None:
    device_address = os.environ.get("WATCH_MAC", "").strip() or input(
        "Watch BLE address (WATCH_MAC): "
    ).strip()
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", device_address):
        raise ValueError("Enter a valid Bluetooth MAC address.")
    device_address = device_address.upper()

    action = choose_action()
    if action is None:
        print("Cancelled.")
        return

    print(f"Scanning for watch at {device_address}...")
    device = await BleakScanner.find_device_by_address(
        device_address,
        timeout=SCAN_TIMEOUT,
    )
    if device is None:
        raise BleakError(
            "Watch not found. Keep it awake and nearby, and make sure its "
            "companion phone app is disconnected."
        )

    print(f"Found {device.name or 'watch'}; connecting...")
    async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
        await run_action(client, action)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
    except TimeoutError:
        print(
            "\nBluetooth connection timed out. Make sure the watch is awake, "
            "nearby, and disconnected from its companion phone app."
        )
    except (BleakError, OSError) as error:
        print(f"\nBluetooth operation failed ({type(error).__name__}): {error}")
