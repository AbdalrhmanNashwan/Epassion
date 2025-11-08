import string
try:
    import win32file, win32api
except Exception:
    win32file = None; win32api = None

def list_removable_drives():
    drives = []
    if win32file is None:
        # fallback: show all letters; user picks correct one
        for l in string.ascii_uppercase: drives.append(f"{l}:\\")
        return drives
    bitmask = win32file.GetLogicalDrives()
    for l in string.ascii_uppercase:
        if bitmask & (1 << (ord(l)-ord('A'))):
            path = f"{l}:\\"
            try:
                if win32file.GetDriveType(path) == win32file.DRIVE_REMOVABLE:
                    drives.append(path)
            except: pass
    return drives

def get_drive_id(root_path: str):
    if win32api is None: return None
    try:
        vol_name, vol_serial, _, _, fs_name = win32api.GetVolumeInformation(root_path)
        return f"VOL:{vol_name or 'NO_LABEL'}:{vol_serial:08X}:{fs_name}"
    except: return None
