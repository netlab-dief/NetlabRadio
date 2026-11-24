#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if command -v python3.13 >/dev/null 2>&1; then
  sdr_python="$(command -v python3.13)"
else
  sdr_python="$(command -v python3)"
fi
if [[ ! -x .venv/bin/python ]]; then
  "$sdr_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import numpy' >/dev/null 2>&1; then
  .venv/bin/python -m pip install numpy
fi
if [[ ! -f .venv/.numba-attempted ]]; then
  if ! .venv/bin/python -c 'import numba' >/dev/null 2>&1; then
    .venv/bin/python -m pip install numba || print 'Numba unavailable: using the NumPy decoder.'
  fi
  touch .venv/.numba-attempted
fi
sdr_offline=0
for sdr_arg in "$@"; do
  if [[ "$sdr_arg" == --self-test || "$sdr_arg" == --help || "$sdr_arg" == -h || "$sdr_arg" == --gui-demo ]]; then
    sdr_offline=1
  fi
done
if [[ "$sdr_offline" == 0 && ! -e /opt/homebrew/opt/uhd/lib/libuhd.dylib && ! -e /usr/local/opt/uhd/lib/libuhd.dylib && -z "${UHD_LIBRARY:-}" ]]; then
  print 'libuhd is required. Install it once: brew install uhd'
  exit 1
fi
if [[ -f "$PWD/uhd-images/usrp_b200_fw.hex" && -f "$PWD/uhd-images/usrp_b200_fpga.bin" && -z "${UHD_IMAGES_DIR:-}" ]]; then
  export UHD_IMAGES_DIR="$PWD/uhd-images"
fi
if [[ "$sdr_offline" == 0 && ! -d /opt/homebrew/share/uhd/images && ! -d /usr/local/share/uhd/images && -z "${UHD_IMAGES_DIR:-}" ]]; then
  sdr_downloader=""
  for sdr_candidate in /opt/homebrew/opt/uhd/lib/uhd/utils/uhd_images_downloader.py /usr/local/opt/uhd/lib/uhd/utils/uhd_images_downloader.py; do
    if [[ -f "$sdr_candidate" ]]; then sdr_downloader="$sdr_candidate"; break; fi
  done
  if [[ -n "$sdr_downloader" ]]; then
    print 'Downloading B200 images once into the application directory.'
    if ! .venv/bin/python -c 'import requests' >/dev/null 2>&1; then
      .venv/bin/python -m pip install requests
    fi
    .venv/bin/python "$sdr_downloader" --types 'b2xx_(b200_fpga|common_fw)_default' --install-location "$PWD/uhd-images"
    export UHD_IMAGES_DIR="$PWD/uhd-images"
  else
    print 'If UHD reports missing images, run the UHD downloader described in the README.'
  fi
fi
sdr_gui=1
for sdr_arg in "$@"; do
  if [[ "$sdr_arg" == --no-gui || "$sdr_arg" == --self-test || "$sdr_arg" == --help || "$sdr_arg" == -h ]]; then
    sdr_gui=0
  fi
done
if [[ "$sdr_gui" == 1 ]]; then
  exec .venv/bin/python ieee80211p_native.py --gui "$@"
else
  exec .venv/bin/python ieee80211p_native.py "$@"
fi
