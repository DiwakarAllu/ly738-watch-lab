import asyncio

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

MAC_ADDRESS = "F4:74:D7:01:53:5B"
SCAN_TIMEOUT = 15.0
CONNECT_TIMEOUT = 30.0
MAX_ATTEMPTS = 3


async def connect_and_discover():
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Attempt {attempt}/{MAX_ATTEMPTS}: scanning for Watch 11...")

        try:
            device = await BleakScanner.find_device_by_address(
                MAC_ADDRESS,
                timeout=SCAN_TIMEOUT,
            )
            if device is None:
                raise BleakError(
                    f"Watch {MAC_ADDRESS} was not found during the scan."
                )

            print(f"Found {device.name or 'watch'} at {device.address}; connecting...")
            async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
                print("Connected. GATT services and characteristics:")
                for service in client.services:
                    print(f"\n[Service] {service.uuid} ({service.description})")
                    for characteristic in service.characteristics:
                        print(
                            f"  [Characteristic] {characteristic.uuid} | "
                            f"Properties: {characteristic.properties}"
                        )
                return
        except (BleakError, TimeoutError) as error:
            if isinstance(error, TimeoutError):
                print(f"Attempt {attempt} failed: GATT connection timed out.")
            else:
                print(f"Attempt {attempt} failed ({type(error).__name__}): {error}")
            if attempt < MAX_ATTEMPTS:
                print(
                    "Retrying shortly. If the watch is connected to its phone app, "
                    "disconnect it there first."
                )
                await asyncio.sleep(2)

    raise BleakError(
        "Could not connect after 3 attempts. Disconnect the watch from its "
        "companion phone app, keep it awake and nearby, then run this script again."
    )


if __name__ == "__main__":
    try:
        asyncio.run(connect_and_discover())
    except KeyboardInterrupt:
        print("\nStopped.")
    except BleakError as error:
        print(f"\nConnection failed: {error}")
