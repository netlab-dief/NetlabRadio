# Validation performed

Date: November 24, 2026. ARM64 Mac, Python 3.13, NumPy 2.4.4, UHD 4.11.

The following results were recorded during the original implementation validation:

- 34 unittest tests passed, including the centered spectrum view.
- Software loopback: 32 frames, all eight encodings, 0/37/500/1500-byte payloads,
  two-tap multipath, noise, 22 kHz CFO and 511-sample chunks.
- Consecutive packets with variable-length chunks and positive and negative CFO.
- FCS checking: corrupted frame rejected and the following frame recovered.
- Reset after a discontinuity; noise without packets.
- 32 comparisons of the data encoding chain against `utils.cc` from the supplied
  gr-ieee802-11 C++ sources: encodings 0..7 and 28/127/528/1528-byte PSDUs.
  Standalone compilation of DSP helpers, without GNU Radio.
- ctypes layout checked by compiling a C program against UHD headers: sizes and
  offsets of stream args, stream cmd, tune request and tune result.
- Local libuhd loaded and all 36 C functions used by the application resolved.
- Simulated UHD sends: short-send handling, SOB/EOB, timeout and underflow.
- PCAP opened with tshark: 5860 MHz frequency, 9 Mbit/s data rate, FCS present,
  source/destination MAC addresses and sequence number decoded correctly.
- Python and zsh syntax checked.
- GUI checked in the browser: 64QAM 3/4 selection, STA equalizer, grid,
  480-point plot and Stop button, in explicit simulation mode.
- 32 equalizer/encoding combinations checked in software loopback with CFO and
  multipath; the constellation tap remains active even for frames with an
  incorrect FCS, as in the original carrier connection.
- Atomic control validation, preservation of concurrent edits, 480-point window
  limits, and simulation worker start/stop.
- Local HTTP API: configuration forwarding, rejection of incorrect tokens and
  invalid values.
- Reconfiguration during RX checked with a simulated radio, including
  stop/reset/start and no UHD changes during an ongoing read.
- Known amplitude 0.3+0.4j: RMS/peak 0.5, level −6.0206 dBFS; block duration and
  waveform timestamp checked.
- Aggregation of consecutive blocks: a single-sample pulse preserved in the
  display envelope and interval peak, even when the next block contains only zeros.
- Full-scale detection on I/Q components, silence without NaN/inf, window reset
  on gaps, and history bounded to ten seconds.
- Simulated reception without frames: amplitude updated with an empty
  constellation and RX packet count at zero, checking the pre-decoding tap.
- Amplitude view checked in the browser in simulation: envelope, dBFS selector,
  RMS/peak levels, history and independent pause.
- Fixed window: point coordinates unchanged as time passes, new sweep at ten
  seconds and restart on reset; fixed/scrolling selector and fixed Y scale
  checked in the GUI.
- FFT spectrum: known positive-bin tone with amplitude 0.5 measured at
  −6.0206 dBFS/bin, negative offset on the lower RF side, bin spacing and Hann
  equivalent noise bandwidth checked numerically.
- Max hold preserved at unchanged frequency and reset on center-frequency
  changes; silence produces finite JSON data, without NaN or infinity.
- GUI checked in simulation: 5855..5865 MHz spectrum centered at 5860,
  Max hold, and subsequent recentering at 5890 MHz (5885..5895 MHz).
- Indicative monitor cost on the development Mac: approximately 0.31 ms per
  65536-sample block; a single measurement, not a guarantee of RF throughput.

An indicative local measurement without Numba, using a single frame with a
500-byte payload: approximately 2.2 ms encoding, 21.3 ms decoding and 0.512 ms
airtime. This is not a statistical benchmark; it shows that the NumPy decoder
cannot sustain continuous traffic at full load. The IQ queue and counters
expose losses.

Not verified: RF compatibility with independent devices, sensitivity, actual USB
RX/TX streaming, PHY performance, compiled Numba, and the launcher's initial
online installation. `uhd_find_devices --args type=b200` found no B200 in the
available environment. The result should be considered an experimental
prototype verified in software, not a field-validated modem.

## Documentation translation checks

After translating the documentation and terminal help into English:

- `--help` displayed the translated option descriptions successfully.
- `zsh -n avvia.command` and `git diff --check` passed.
- The unittest suite ran 34 tests: 32 passed, including the software loopback,
  C++ encoding reference and UHD ABI checks. Two HTTP tests could not start
  their local server because the execution sandbox denied binding to
  `127.0.0.1` (`PermissionError: [Errno 1] Operation not permitted`).
  Those two HTTP results were not revalidated in this run.

## Linux port preparation checks

- Added `start-linux.sh` with a separate `.venv-linux` environment, plus Linux
  installation and USB setup instructions in LINUX.md.
- Bash syntax and `git diff --check` passed.
- A stub interpreter exercised six launcher argument combinations: default GUI,
  help, self-test, demo, terminal-only RX, and GUI without automatic browser
  opening on a specified port. Paths containing spaces, environment isolation
  and the missing-Python diagnostic were also checked.
- Mocked Linux library loading checked explicit path, environment override and
  system discovery precedence, plus the Linux missing-library diagnostic.
- The reference test supports clang++ or g++, a `.so` output on Linux, and UHD
  headers in standard Linux include directories. Its encoding and ABI checks
  passed on the development Mac after these changes.
- The existing suite again ran 34 tests: 32 passed; the two HTTP tests were
  blocked by the same sandbox restriction on binding a local server.
- No Linux runtime or Linux B200 USB/RF validation was performed. These are
  preparation checks on macOS, not evidence of Linux hardware interoperability.
