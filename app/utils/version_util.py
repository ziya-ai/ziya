import requests
from packaging import version
import subprocess
import sys
from typing import Optional


def get_latest_version() -> Optional[str]:
    try:
        response = requests.get('https://pypi.org/pypi/ziya/json')
        response.raise_for_status()
        return str(version.parse(response.json()['info']['version']))
    except requests.exceptions.RequestException:
        return None


def update_package(package_name: str) -> None:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', package_name])


def get_current_version() -> str:
    from importlib.metadata import version
    return str(version('ziya'))
