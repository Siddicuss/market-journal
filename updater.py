"""Checks GitHub for a newer Market Journal and installs it.

Releases come from the public GitHub repo, so the check needs no GitHub login.
The installer is run with /UPDATE=<pid>: it waits for this app to close, installs over it and reopens it.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from version import VERSION

REPO = 'Siddicuss/market-journal'
LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'MarketJournal-Setup.exe'
ALLOWED_HOSTS = ('https://github.com/', 'https://objects.githubusercontent.com/',
                 'https://release-assets.githubusercontent.com/')


class UpdateError(Exception):
    pass


def parse_version(text):
    parts = str(text).strip().lstrip('vV').split('.')
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        raise UpdateError(f'Unrecognised version "{text}" on GitHub.') from None


def _open(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': f'MarketJournal/{VERSION}',
                                               'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(req, timeout=timeout)


def latest(fetch=None):
    """The newest release: {'version', 'notes', 'url', 'size', 'sha256'}."""
    try:
        if fetch:
            data = fetch(LATEST)
        else:
            with _open(LATEST) as r:
                data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError('No releases have been published yet.') from None
        raise UpdateError(f'GitHub answered {exc.code}. Try again later.') from None
    except (OSError, ValueError):
        raise UpdateError("Couldn't reach GitHub. Check your internet connection.") from None
    asset = next((a for a in data.get('assets') or [] if a.get('name') == ASSET), None)
    if not asset:
        raise UpdateError(f'The latest release has no {ASSET}.')
    digest = asset.get('digest') or ''
    return {'version': '.'.join(map(str, parse_version(data.get('tag_name', '')))),
            'notes': (data.get('body') or '').strip(), 'url': asset['browser_download_url'],
            'size': asset.get('size') or 0, 'sha256': digest[7:] if digest.startswith('sha256:') else None}


def check(fetch=None):
    release = latest(fetch)
    return {'current': VERSION, 'latest': release['version'],
            'available': parse_version(release['version']) > parse_version(VERSION), 'release': release}


def download(release, progress=lambda done, total: None, folder=None):
    url = release['url']
    if not url.startswith(ALLOWED_HOSTS):
        raise UpdateError('The download link is not on GitHub.')
    folder = Path(folder or Path(tempfile.gettempdir()) / 'MarketJournal-update')
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f'MarketJournal-Setup-{release["version"]}.exe'
    part = dest.with_suffix('.part')
    sha = hashlib.sha256()
    try:
        with _open(url, timeout=60) as r, open(part, 'wb') as f:
            total = int(r.headers.get('Content-Length') or release.get('size') or 0)
            done = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                progress(done, total)
    except OSError:
        part.unlink(missing_ok=True)
        raise UpdateError('The download failed. Check your internet connection and try again.') from None
    if release.get('size') and part.stat().st_size != release['size']:
        part.unlink(missing_ok=True)
        raise UpdateError('The download was incomplete. Try again.')
    if release.get('sha256') and sha.hexdigest() != release['sha256'].lower():
        part.unlink(missing_ok=True)
        raise UpdateError("The download didn't match GitHub's checksum, so it wasn't installed.")
    os.replace(part, dest)
    return dest


def installed():
    """Only the installed app can replace itself; running from source just checks."""
    return bool(getattr(sys, 'frozen', False))


def launch_installer(setup):
    # Keep the user's choice of a Desktop shortcut (same path as installer.desktop_link, without bundling Tk).
    desktop = (Path(os.environ.get('USERPROFILE', Path.home())) / 'Desktop' / 'Market Journal.lnk').exists()
    args = [str(setup), '/S', f'/UPDATE={os.getpid()}'] + ([] if desktop else ['/NODESKTOP'])
    subprocess.Popen(args, close_fds=True, creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
