"""Release media is built from pinned sources; retain only imageio's wrapper."""
# Do not collect imageio_ffmpeg/binaries or the wheel's precompiled FFmpeg.
datas = []
binaries = []
hiddenimports = ["imageio_ffmpeg.binaries"]
