"""MarketJournal-Setup.exe: per-user installer and uninstaller (no admin rights needed).

Installs the bundled app folder to %LOCALAPPDATA%\\Programs\\Market Journal, adds Start
menu (and optionally Desktop) shortcuts, and registers it under Windows "Installed apps".
Journal data and the saved API key live in %LOCALAPPDATA%\\MarketJournal and are kept
unless the user chooses to delete them while uninstalling. An install of the app under its
old name (Kalshi Journal) is removed, and its data moved over.

  MarketJournal-Setup.exe               install with a small window
  MarketJournal-Setup.exe /S            install silently (/NODESKTOP to skip the Desktop shortcut)
  <install dir>\\uninstall.exe          uninstall (also run from Installed apps)
  uninstall.exe /S [/DELETEDATA]        uninstall silently
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

APP = 'Market Journal'
VERSION = '1.1.2'
EXE = 'MarketJournal.exe'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\MarketJournal'
OLD_APP, OLD_EXE = 'Kalshi Journal', 'KalshiJournal.exe'
OLD_UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\KalshiJournal'
NO_WINDOW = 0x08000000


def local_appdata():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))


def default_install_dir():
    return local_appdata() / 'Programs' / APP


def data_dir(name='MarketJournal'):
    return local_appdata() / name


def start_menu_link(app=APP):
    return Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / f'{app}.lnk'


def desktop_link(app=APP):
    return Path(os.environ.get('USERPROFILE', Path.home())) / 'Desktop' / f'{app}.lnk'


def payload():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent / 'build')) / 'payload.zip'


def app_running():
    """True while Market Journal (or the old Kalshi Journal) is open: its server answers."""
    import urllib.request
    for folder in (data_dir(), data_dir('KalshiJournal')):
        try:
            info = json.loads((folder / 'running.json').read_text(encoding='utf-8'))
            req = urllib.request.Request(info['url'].split('?')[0] + 'api/heartbeat?t=' + info['token'], data=b'', method='POST')
            urllib.request.urlopen(req, timeout=2).read()
            return True
        except Exception:
            continue
    return False


def make_shortcut(link, target):
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:KJ_LINK);"
              "$s.TargetPath=$env:KJ_TARGET;$s.WorkingDirectory=$env:KJ_DIR;"
              "$s.IconLocation=$env:KJ_TARGET+',0';$s.Description='Prediction market trade journal';$s.Save()")
    env = dict(os.environ, KJ_LINK=str(link), KJ_TARGET=str(target), KJ_DIR=str(target.parent))
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], env=env,
                   check=True, creationflags=NO_WINDOW, capture_output=True)


def folder_size_kb(path):
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) // 1024


def register(install_dir):
    import winreg
    uninstaller = install_dir / 'uninstall.exe'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (('DisplayName', APP), ('DisplayVersion', VERSION), ('Publisher', APP),
                            ('DisplayIcon', str(install_dir / EXE)), ('InstallLocation', str(install_dir)),
                            ('UninstallString', f'"{uninstaller}"'), ('QuietUninstallString', f'"{uninstaller}" /S')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, 'EstimatedSize', 0, winreg.REG_DWORD, folder_size_kb(install_dir))
        winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)


def registered_dir(key_name=UNINSTALL_KEY):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
            return Path(winreg.QueryValueEx(key, 'InstallLocation')[0])
    except OSError:
        return None


def is_our_install(path, exe=EXE):
    """Only a folder holding a previous install may be replaced or deleted."""
    if not path.is_dir():
        return False
    if (path / exe).is_file() and (path / '_internal').is_dir():
        return True
    return {p.name.lower() for p in path.iterdir()} == {'uninstall.exe'}  # left by an interrupted uninstall


def unregister(key_name=UNINSTALL_KEY):
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
    except OSError:
        pass


def remove_old_name():
    """Remove an install from when the app was called Kalshi Journal, and move its data to the new folder."""
    old_dir = registered_dir(OLD_UNINSTALL_KEY) or local_appdata() / 'Programs' / OLD_APP
    for link in (start_menu_link(OLD_APP), desktop_link(OLD_APP)):
        link.unlink(missing_ok=True)
    unregister(OLD_UNINSTALL_KEY)
    if is_our_install(old_dir, OLD_EXE):
        shutil.rmtree(old_dir, ignore_errors=True)
    old_data = data_dir('KalshiJournal')
    if old_data.is_dir() and not data_dir().exists():
        try:
            old_data.rename(data_dir())
        except OSError:
            pass  # the app moves it on first launch instead


def install(install_dir, desktop=True, progress=lambda text: None):
    install_dir = Path(install_dir).resolve()
    if install_dir.exists() and not is_our_install(install_dir) and any(install_dir.iterdir()):
        raise RuntimeError(f'{install_dir} already has other files in it. Choose an empty folder.')
    progress('Unpacking files…')
    staging = Path(tempfile.mkdtemp(prefix='mj-install-'))
    try:
        with zipfile.ZipFile(payload()) as z:
            z.extractall(staging)
        if not (staging / EXE).is_file():
            raise RuntimeError('The installer is damaged: the app is missing.')
        progress('Copying files…')
        if install_dir.exists():
            shutil.rmtree(install_dir)  # an earlier version or an empty folder; data lives elsewhere
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(install_dir))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.copy2(sys.executable, install_dir / 'uninstall.exe')
    progress('Removing Kalshi Journal (the old name)…')
    try:
        remove_old_name()
    except Exception:
        pass
    progress('Creating shortcuts…')
    target = install_dir / EXE
    make_shortcut(start_menu_link(), target)
    if desktop:
        make_shortcut(desktop_link(), target)
    else:
        desktop_link().unlink(missing_ok=True)
    register(install_dir)
    progress('Done')
    return target


def uninstall(install_dir, delete_data=False):
    for link in (start_menu_link(), desktop_link()):
        link.unlink(missing_ok=True)
    unregister()
    if is_our_install(install_dir):
        for _ in range(20):  # files can stay locked for a moment after the app closes
            shutil.rmtree(install_dir, ignore_errors=True)
            if not install_dir.exists():
                break
            time.sleep(0.5)
    if delete_data:
        shutil.rmtree(data_dir(), ignore_errors=True)


# --------------------------------------------------------------------------- UI
def ask(text, title=APP, flags=0x24):  # MB_YESNO | MB_ICONQUESTION
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def tell(text, error=False):
    ctypes.windll.user32.MessageBoxW(None, text, APP, 0x10 if error else 0x40)


def install_window():
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title(f'{APP} Setup')
    root.resizable(False, False)
    try:
        root.iconbitmap(str(Path(getattr(sys, '_MEIPASS', '.')) / 'icon.ico'))
    except tk.TclError:
        pass
    frame = ttk.Frame(root, padding=22)
    frame.grid()
    ttk.Label(frame, text=f'Install {APP} {VERSION}', font=('Segoe UI', 14, 'bold')).grid(sticky='w')
    ttk.Label(frame, wraplength=440, justify='left', text=(
        'A trade journal for your Kalshi and Polymarket accounts. It only reads your trade history '
        'and never places orders. After installing, open it and connect an account in Settings.'
        + ('\n\nThis replaces Kalshi Journal. Your trades, notes and saved key carry over.'
           if registered_dir(OLD_UNINSTALL_KEY) or data_dir('KalshiJournal').is_dir() else ''))
    ).grid(sticky='w', pady=(6, 14))
    ttk.Label(frame, text='Install folder').grid(sticky='w')
    folder = tk.StringVar(value=str(registered_dir() or default_install_dir()))
    row = ttk.Frame(frame)
    row.grid(sticky='we', pady=(2, 10))
    ttk.Entry(row, textvariable=folder, width=52).grid(row=0, column=0)
    def browse():
        chosen = filedialog.askdirectory(initialdir=str(Path(folder.get()).parent))
        if chosen:
            folder.set(str(Path(chosen) / APP))
    ttk.Button(row, text='Browse…', command=browse).grid(row=0, column=1, padx=(6, 0))
    desktop = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text='Add a Desktop shortcut', variable=desktop).grid(sticky='w')
    launch = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text=f'Open {APP} when setup finishes', variable=launch).grid(sticky='w', pady=(2, 12))
    status = tk.StringVar(value='')
    ttk.Label(frame, textvariable=status, foreground='#555').grid(sticky='w')
    buttons = ttk.Frame(frame)
    buttons.grid(sticky='e', pady=(10, 0))
    go = ttk.Button(buttons, text='Install')
    go.grid(row=0, column=0)
    ttk.Button(buttons, text='Cancel', command=root.destroy).grid(row=0, column=1, padx=(8, 0))

    def run():
        if app_running():
            tell(f'Close {APP} first, then click Install again.', error=True)
            return
        go.state(['disabled'])
        result = {}

        def work():
            try:
                result['target'] = install(folder.get(), desktop.get(), lambda t: status.set(t))
            except Exception as exc:
                result['error'] = exc
            root.after(0, finish)

        def finish():
            if 'error' in result:
                go.state(['!disabled'])
                status.set('')
                tell(f'Setup could not finish:\n\n{result["error"]}', error=True)
                return
            if launch.get():
                subprocess.Popen([str(result['target'])], cwd=str(result['target'].parent))
            else:
                tell(f'{APP} is installed. Open it from the Start menu.')
            root.destroy()
        threading.Thread(target=work, daemon=True).start()
    go.configure(command=run)
    root.mainloop()


def run_uninstall(args):
    """Runs from a temp copy so the install folder (and this uninstall.exe) can be deleted."""
    silent = '/S' in args
    install_dir = registered_dir() or Path(sys.executable).parent
    if '/FROMTEMP' not in args:
        if not silent and ask(f'Remove {APP} from this PC?') != 6:
            return
        if app_running():
            tell(f'Close {APP} first, then run the uninstaller again.', error=True)
            return
        delete = '/DELETEDATA' in args or (not silent and ask(
            'Also delete your journal (notes, tags, weekly reviews) and your saved API key?\n\n'
            'Choose No to keep them for a future install.', flags=0x134) == 6)  # yes/no, warning, default No
        temp = Path(tempfile.gettempdir()) / f'mj-uninstall-{os.getpid()}.exe'
        shutil.copy2(sys.executable, temp)
        extra = [a for a in ('/S',) if silent] + (['/DELETEDATA'] if delete else [])
        subprocess.Popen([str(temp), '/UNINSTALL', '/FROMTEMP', f'/DIR={install_dir}'] + extra)
        return  # exit now so this uninstall.exe is no longer locked
    install_dir = Path(next(a[5:] for a in args if a.startswith('/DIR=')))
    uninstall(install_dir, '/DELETEDATA' in args)
    if not silent:
        tell(f'{APP} was removed.' + ('' if '/DELETEDATA' in args else
                                      ' Your journal data was kept in %LOCALAPPDATA%\\MarketJournal.'))
    # The temp copy deletes itself once this process exits.
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & del "{sys.executable}"', creationflags=NO_WINDOW)


def main(args):
    upper = [a.upper() if a.startswith('/') and not a.upper().startswith('/DIR=') else a for a in args]
    is_uninstaller = Path(sys.executable).name.lower() == 'uninstall.exe'
    if is_uninstaller or '/UNINSTALL' in upper:
        return run_uninstall(upper)
    if '/S' in upper:
        if app_running():
            sys.exit(2)
        try:
            install(registered_dir() or default_install_dir(), desktop='/NODESKTOP' not in upper)
        except Exception:
            sys.exit(1)
        return
    install_window()


if __name__ == '__main__':
    main(sys.argv[1:])
