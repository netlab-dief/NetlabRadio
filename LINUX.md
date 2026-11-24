# Linux installation and operation

The Linux launcher runs the same standalone Python modem and local browser GUI
as the macOS version. GNU Radio is not required. Python 3.10 or newer, NumPy and
the UHD C library are required; Numba is an optional decoder accelerator.

The instructions below target Debian/Ubuntu with Python 3.10+. On other
distributions, install the equivalent Python, venv and UHD packages using the
distribution's package manager. Both x86-64 and ARM64 are intended targets where
these dependencies are available; neither has been runtime-validated on Linux
for this project yet. Linux USB streaming and RF interoperability remain untested.

## Install dependencies

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip libuhd-dev uhd-host
```

These UHD packages are documented in the [official UHD installation guide](https://files.ettus.com/manual/page_install_binary.html).
The modem loads `libuhd` directly through ctypes, so the UHD Python bindings and
a PyPI package named `uhd` are not needed.

## Install firmware and FPGA images

Use the downloader belonging to the UHD installation on the Linux machine:

```sh
sudo uhd_images_downloader --types b2xx
```

If it is not on PATH, locate the installed script:

```sh
dpkg -L uhd-host | rg 'uhd_images_downloader'
```

`rg` is optional; `grep uhd_images_downloader` can be used instead. Run the
reported executable, or use `sudo python3 /actual/path/uhd_images_downloader.py`
for a Python script. Common script locations include
`/usr/lib/uhd/utils/uhd_images_downloader.py` and
`/usr/local/lib/uhd/utils/uhd_images_downloader.py`.

To install images locally without sudo, run this from the project directory:

```sh
uhd_images_downloader --types b2xx --install-location "$PWD/uhd-images-linux"
export UHD_IMAGES_DIR="$PWD/uhd-images-linux"
```

Set `UHD_IMAGES_DIR` in each new terminal before using local images. The launcher
honors this variable but does not automatically select the macOS `uhd-images`
directory. Download images again after upgrading UHD so that they match the
installed host library. See the [official downloader source and usage](https://github.com/EttusResearch/uhd/blob/master/host/utils/uhd_images_downloader.py.in).

## USB permissions and device detection

Connect the B200 to a USB 3 port. Check discovery as your normal user:

```sh
uhd_find_devices --args type=b200
uhd_usrp_probe --args type=b200
```

If USB access is denied, check whether your UHD package installed its udev rules.
If needed, copy the rules supplied with that UHD installation:

```sh
sudo cp /actual/path/uhd-usrp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Locate the supplied file with `dpkg -L uhd-host | grep uhd-usrp.rules`.
For source installations it may be under `/usr/local/lib/uhd/utils/`.
Replace `/actual/path/` with the installed location; then unplug and reconnect
the B200 and retry discovery. Run the modem as your normal user.
See [UHD USB transport setup](https://uhd.readthedocs.io/en/latest/page_transport.html).

## Start the modem

Copy or clone the project onto Linux; do not reuse a macOS virtual environment.
From the directory containing this file:

```sh
chmod +x start-linux.sh
./start-linux.sh --self-test
./start-linux.sh --gui-demo
./start-linux.sh --mode trx --freq 5860000000
```

The launcher creates `.venv-linux`, installs NumPy when missing, and attempts
to install Numba once. First-time dependency installation requires Internet
access. Later launches reuse the environment. The self-test, demo and help do
not require UHD or a connected radio; they can still require Internet access
if Python dependencies have not yet been installed.

With no arguments, the launcher starts RX/TX and opens the browser GUI. The
modem has the same controls, UDP ports, PCAP output and limitations described
in [README.md](README.md). GUI labels currently remain in Italian.

```sh
./start-linux.sh --mode rx --pcap capture.pcap
./start-linux.sh --no-gui --mode rx
./start-linux.sh --help
```

Use `SDR_PYTHON=python3.12 ./start-linux.sh --self-test` to select the interpreter
when creating the environment. If changing Python version or copying from a
different machine, recreate `.venv-linux` first.

For simultaneous RX/TX, connect RX to **RX2** and TX to **TX/RX**. Defaults remain
5860 MHz, 10 MS/s and encoding 3. Captures overwrite the selected PCAP on each
start. Closing the browser does not stop the server; press Ctrl-C in the terminal
to terminate the application.

### Remote or headless use

Terminal-only operation requires no desktop or browser. For a remote GUI:

```sh
# On the Linux machine:
./start-linux.sh --no-browser --gui-port 8765

# On your local machine, in another terminal:
ssh -L 8765:127.0.0.1:8765 user@linux-host
```

Open `http://127.0.0.1:8765/` in your local browser while the SSH tunnel is open.
The server remains bound to Linux loopback.

### Custom UHD installation

The modem locates the system library using `ctypes.util.find_library("uhd")`.
For a custom installation, specify its actual path:

```sh
./start-linux.sh --uhd-library /actual/path/libuhd.so
# Or:
UHD_LIBRARY=/actual/path/libuhd.so ./start-linux.sh
```

Ensure the library's own shared dependencies are visible to the Linux loader.
Install library, utilities and images from a consistent UHD installation.

## Tests and validation limits

After the first launcher run:

```sh
.venv-linux/bin/python -m unittest discover -s tests -v
```

The optional C++ reference comparison requires `clang++` or `g++` and the sibling
`../gr-ieee802-11` source tree. The ABI check requires UHD headers and a C
compiler. On Debian/Ubuntu, `sudo apt install build-essential` supplies GCC/G++.
Without these optional dependencies, the corresponding tests are skipped.

This port has been checked on the development Mac using software tests and
launcher checks. It has not been executed on a Linux host with a B200. A passing
software self-test does not validate USB throughput, RF behavior or external
802.11p interoperability. The decoder's throughput limits and missing MAC
protocols remain those described in README.md and VALIDATION.md.
