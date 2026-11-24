#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Keep Linux dependencies separate from a copied macOS virtual environment.
sdr_python="${SDR_PYTHON:-python3}"
sdr_venv=".venv-linux"
if ! command -v "$sdr_python" >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10+ is required. See LINUX.md.' >&2
    exit 1
fi
"$sdr_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required")'
if [[ ! -x "$sdr_venv/bin/python" ]]; then
    if ! "$sdr_python" -m venv "$sdr_venv"; then
        printf '%s\n' 'Cannot create the Python environment. On Debian/Ubuntu install python3-venv.' >&2
        exit 1
    fi
fi
if ! "$sdr_venv/bin/python" -c 'import numpy' >/dev/null 2>&1; then
    "$sdr_venv/bin/python" -m pip install -r requirements.txt
fi
if [[ ! -f "$sdr_venv/.numba-attempted" ]]; then
    if ! "$sdr_venv/bin/python" -c 'import numba' >/dev/null 2>&1; then
        "$sdr_venv/bin/python" -m pip install numba || printf '%s\n' 'Numba unavailable: using the NumPy decoder.' >&2
    fi
    touch "$sdr_venv/.numba-attempted"
fi

# UHD uses the images installed for the Linux UHD version. Honor an explicit
# UHD_IMAGES_DIR, but never auto-select images copied from the Mac installation.
sdr_gui=1
for sdr_arg in "$@"; do
    case "$sdr_arg" in
        --no-gui|--self-test|--help|-h) sdr_gui=0 ;;
    esac
done
if [[ "$sdr_gui" == 1 ]]; then
    exec "$sdr_venv/bin/python" ieee80211p_native.py --gui "$@"
else
    exec "$sdr_venv/bin/python" ieee80211p_native.py "$@"
fi
