import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

LOGCAT_PATH = Path(__file__).with_name("logcat.txt")
OUTPUT_DIR = Path(__file__).with_name("watch_assets")
ALLOWED_HOST = "cdn.jusonsmart.com"
MAX_ASSET_SIZE = 10 * 1024 * 1024

THEME_PATTERN = re.compile(
    r"(?P<timestamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}).*?"
    r"开始升级自定义表盘:WatchThemeDetailsResponse\{id=(?P<theme_id>\d+).*?"
    r"materialList=\[(?P<materials>.*?)\]"
)
MATERIAL_PATTERN = re.compile(
    r"MaterialListBean\{name='(?P<name>[^']+)', "
    r"url='(?P<url>https://[^']+)', size='(?P<size>\d+)'\}"
)


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    expected_size: int


@dataclass(frozen=True)
class Theme:
    theme_id: str
    timestamp: str
    assets: tuple[Asset, ...]


def read_themes() -> list[Theme]:
    if not LOGCAT_PATH.is_file():
        raise FileNotFoundError(f"Logcat file not found: {LOGCAT_PATH}")

    themes = []
    text = LOGCAT_PATH.read_text(encoding="utf-8", errors="replace")
    for match in THEME_PATTERN.finditer(text):
        assets = []
        for asset_match in MATERIAL_PATTERN.finditer(match.group("materials")):
            name = asset_match.group("name")
            url = asset_match.group("url")
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != ALLOWED_HOST
                or not parsed.path.startswith("/band/")
                or Path(name).name != name
            ):
                raise ValueError(f"Unexpected asset URL or filename in log: {url}")

            expected_size = int(asset_match.group("size"))
            if not 0 < expected_size <= MAX_ASSET_SIZE:
                raise ValueError(f"Unexpected declared asset size: {expected_size}")
            assets.append(Asset(name, url, expected_size))

        if assets:
            themes.append(
                Theme(
                    theme_id=match.group("theme_id"),
                    timestamp=match.group("timestamp"),
                    assets=tuple(assets),
                )
            )

    if not themes:
        raise ValueError("No custom watch-face material URLs found in logcat.txt.")
    return themes


def download_theme_assets(theme: Theme, session: requests.Session) -> None:
    destination = OUTPUT_DIR / f"theme-{theme.theme_id}"
    destination.mkdir(parents=True, exist_ok=True)

    for asset in theme.assets:
        response = session.get(asset.url, timeout=(5, 20), stream=True)
        response.raise_for_status()

        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_ASSET_SIZE:
                raise ValueError(f"{asset.name} exceeds the {MAX_ASSET_SIZE}-byte limit.")
        if len(data) != asset.expected_size:
            raise ValueError(
                f"{asset.name}: expected {asset.expected_size} bytes, "
                f"received {len(data)}."
            )

        output_path = destination / asset.name
        output_path.write_bytes(data)
        print(f"Downloaded {output_path.relative_to(Path(__file__).parent)} ({len(data)} bytes)")


def main() -> None:
    themes = read_themes()
    print("Watch-face material sets found in logcat:")
    for index, theme in enumerate(themes, start=1):
        print(
            f"  {index}. theme {theme.theme_id}, {theme.timestamp}, "
            f"{len(theme.assets)} assets"
        )

    selection = input("Select a theme number, or q to quit: ").strip().lower()
    if selection == "q":
        return
    if not selection.isdecimal() or not 1 <= int(selection) <= len(themes):
        raise ValueError("Invalid theme selection.")

    selected = themes[int(selection) - 1]
    with requests.Session() as session:
        session.headers["User-Agent"] = "my-watch-asset-fetcher/1.0"
        download_theme_assets(selected, session)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, requests.RequestException) as error:
        raise SystemExit(f"Asset fetch failed: {error}") from error
