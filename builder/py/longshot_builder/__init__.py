"""Longshot Builder, in Python: the same window as the WPF builder (builder/LongshotBuilder), run by python.org's own python.exe and
Qt's DLLs (both signed by their makers), with no executable of Longshot's own to download. It also does what the setup exe did
(builder/setup/setup.c): it reads a longshot-setup.txt, downloads the parts it names, checks each one's SHA-256 and unpacks them
into %LOCALAPPDATA%\\LongshotBuilder\\versions\\<tag>. It looks for no release itself: the Longshot Launcher installs and updates
the builder (docs/releases.md)."""
APP_ID = "MagicBean.Longshot.Builder"   # (Windows' AppUserModelID: the taskbar groups and shows the window as Longshot, not Python)
# (where players get the launcher, which installs and updates the builder: the website's download page)
LAUNCHER_DOWNLOAD = "https://longshot.magicbean.dev/download"
