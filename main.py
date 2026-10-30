"""Market Journal entry point: start the local server and show it in the app's own window."""
import ctypes
import json
import logging
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

import credentials
from server import App, serve
from store import Store

TITLE = 'Market Journal'


def resource_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def message(text, title=TITLE):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(text, file=sys.stderr)


def window_background():
    """Match the first paint to the Windows light/dark setting so the window doesn't flash."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            light = winreg.QueryValueEx(key, 'AppsUseLightTheme')[0]
    except OSError:
        light = 1
    return '#f4f6f8' if light else '#0b1016'


def focus_existing(lock):
    """If another copy is running, bring its window to the front and return True."""
    try:
        info = json.loads(lock.read_text(encoding='utf-8'))
        req = urllib.request.Request(info['url'].split('?')[0] + 'api/heartbeat?t=' + info['token'], data=b'', method='POST')
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


def main():
    credentials.migrate_old_data()
    data_dir = credentials.app_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / 'journal.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    lock = data_dir / 'running.json'
    if focus_existing(lock):
        return
    shutil.rmtree(data_dir / 'window', ignore_errors=True)  # browser profile from the old Edge-window version
    try:
        import webview
        store = Store(data_dir / 'journal.db')
        app = App(store, resource_dir() / 'web', quit=lambda: [w.destroy() for w in list(webview.windows)])
        httpd = serve(app)
    except Exception as exc:
        logging.exception('Startup failed')
        message(f'{TITLE} could not start:\n\n{exc}')
        return
    url = f'http://127.0.0.1:{httpd.server_address[1]}/?t={app.token}'
    lock.write_text(json.dumps({'url': url, 'token': app.token, 'pid': os.getpid()}), encoding='utf-8')
    logging.info('Started on port %s', httpd.server_address[1])
    if app.configured:
        try:
            app.start_sync()
        except Exception:
            pass
    threading.Thread(target=app.auto_sync_loop, daemon=True, name='auto-sync').start()
    try:
        webview.settings['ALLOW_DOWNLOADS'] = True  # CSV export
        webview.create_window(TITLE, url, width=1440, height=920, min_size=(900, 600), background_color=window_background())
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(data_dir / 'webview'))
    except Exception as exc:
        logging.exception('Window failed')
        message(f'{TITLE} could not open its window. It needs the Microsoft Edge WebView2 Runtime, '
                f'which comes with Windows 11.\n\n{exc}')
    finally:
        httpd.shutdown()
        store.close()
        lock.unlink(missing_ok=True)
        logging.info('Stopped')


if __name__ == '__main__':
    main()
