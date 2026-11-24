# IEEE 802.11p on the B200, without GNU Radio

Experimental standalone implementation in Python, NumPy and libuhd, with a local
browser GUI. It does not import GNU Radio, gr-foo, gr-ieee802-11, Gqrx or
gr-fosphor. Numba is optional and accelerates Viterbi decoding; the launcher
attempts to install it once.

## Getting started

UHD is already installed on the Mac used to prepare this project. On another Mac:

```sh
brew install uhd python@3.13
```

From the directory containing this file:

```sh
./avvia.command --self-test
./avvia.command --mode trx --freq 5860000000
```

The launcher creates a local Python environment and installs NumPy on first use
(Internet access is required only for installation). Subsequent launches reuse
the environment. Double-clicking `avvia.command` opens the GUI in the default
browser and starts RX and TX with the default settings. If the B200 is unavailable,
the GUI stays open and displays the error; it does not substitute simulated data
for radio data. If standard UHD images cannot be found, it downloads the B200
images into the application's `uhd-images` directory using the installed UHD
downloader (and `requests`). The software self-test and `--help` neither download
images nor open the radio. The first run with Numba may take a few seconds to
compile the decoder. Do not run Gqrx or other applications that open the same
B200 at the same time.

## GUI: controls from the original flowgraph

The `Ieee80211P` window is recreated in the local browser. It is not the original
Qt window and does not reproduce its theme, context menus or borders pixel for
pixel. The data, control choices, control order and main plot correspond to
those in `SDR/ieee80211p.py`:

| Element | Implementation |
|---|---|
| `tx_gain` | Slider and numeric value, 0..1, step 0.01, default 1 |
| `samp_rate` | 5 / 10 / 20 MHz, default 10 |
| `lo_offset` | 0 / 6000000 / 11000000 Hz |
| RX `gain` | Slider and numeric value, 0..1, step 0.01, default 0.75 |
| `freq` | The same 67 values and channel/frequency/11a-g-p labels |
| `encoding` | The same eight BPSK, QPSK, 16QAM and 64QAM radio buttons |
| `chan_est` | LS / LMS / Linear Comb / STA, connected to the decoder |
| Constellation | Latest 480 samples from the 48 data subcarriers, blue, I/Q axes −2..+2, 100 ms refresh |
| `pdu_length` | Slider 0..1500, default 500; no generator connected, as in the flowgraph |
| `interval` | Slider 100..10000, default 1000; no generator connected, as in the flowgraph |

The plot uses equalized symbols before the FCS check, at the same functional
point as the original `carrier` connection. It can therefore show constellations
from frames that subsequently fail CRC validation. It does not show raw IQ or
generate artificial points when the radio is not receiving packets.
The grid and autoscaling are initially disabled, as in the flowgraph; they can
be enabled using the controls below the plot. Pause plot (`Pausa grafico`)
freezes only the display, leaving the radio running. GUI labels remain in Italian;
their exact text is included below where needed to identify a control.

Parameter changes are applied to the modem: changes to frequency, LO, sample
rate or equalizer restart synchronization, and IQ blocks captured before the
change are discarded. UHD access is serialized at read/burst boundaries.
Configuration is not changed while a thread is using the same radio path.
A UHD error stops the modem and is displayed in the GUI.

Stop (`Ferma`) stops RX/TX; Start (`Avvia`) reopens the radio. Each new capture
reopens the PCAP file for writing, overwriting it. Closing the browser tab alone
does not terminate the server: use Stop, or press Ctrl-C in the terminal to
close the application as well.

To try the GUI without connecting a radio:

```sh
./avvia.command --gui-demo
```

Simulation is explicitly labeled and uses the actual modem in software loopback
with noise/CFO. It does not open UHD, transmit RF or write PCAP files.
For terminal-only operation, use `./avvia.command --no-gui`.
When running Python directly, add `--gui` to enable the GUI:

```sh
python ieee80211p_native.py --gui
```

The page and APIs are served exclusively on `127.0.0.1`, without external web
resources. An available port is selected automatically; use `--gui-port 8765`
to select one explicitly, and `--no-browser` to open the URL manually.

### Received signal amplitude

The default view is **Spectrum · center frequency** (`Spettro · frequenza al
centro banda`): the X axis shows RF frequency in MHz, fixed at
`freq ± sample_rate/2`. For example, a center frequency of 5860 MHz and a sample
rate of 10 MS/s give an axis from 5855 to 5865 MHz. A dashed line marks the
center frequency. Changing `freq` recenters the spectrum; changing the sample
rate updates the span. The axis does not scroll with time.

The blue trace is the FFT of the raw IQ block retained by the monitor.
It uses 2048-point complex FFTs, a Hann window and power averaging over complete,
non-overlapping segments of the block. For shorter blocks, the FFT size is
reduced to the available power of two. At 10 MS/s the bin spacing is approximately
4883 Hz; the equivalent noise bandwidth of the Hann window is approximately
1.5 bins. This is not a spectrum analyzer with calibrated RBW.

Normalization preserves coherent amplitude: a complex sinusoid with amplitude 1
exactly on a bin produces a peak of 0 dBFS/bin. The per-bin level is neither
spectral density per Hz nor RF power in dBm. RMS and peak in the readouts still
refer to time-domain samples. The initial scale is dBFS, with a fixed Y axis
from −120 to +6.

**Max hold** adds an orange trace showing the maximum for each bin, retained
since the start of capture even when the checkbox is disabled. It resets on
reset or a change of frequency, sample rate or FFT size. Any LO offset is not
added to the labels: UHD has already compensated for that offset in the IQ
samples returned to the application.

For the previous oscilloscope view, select **Amplitude over time** (`Ampiezza
nel tempo`). Time history is hidden by default and can be enabled with
**Show time history** (`Mostra storico temporale`). The fixed-window/scrolling
controls apply only to that history, not to the RF spectrum.

The **Received signal · raw IQ** panel (`Segnale ricevuto · IQ grezzo`) is
connected directly to the samples returned by UHD, before the decoding queue
and packet detection. It also works with noise, non-802.11p transmissions and
undecodable packets. It does not require valid frames in the constellation plot
or RX counters.

- Time-domain envelope plot of `|I+jQ|`, with a linear or dBFS scale.
- RMS and peak, both shown as numeric amplitude and in dBFS.
- RMS and peak history for the latest ten seconds.
- Indicator for I/Q samples close to digital full scale.
- Independent amplitude-display pause, without stopping RX/TX.

The **Time history** selector (`Storico temporale`), visible when history is
enabled, offers two modes:

- **Fixed window · 0–10 s** (`Finestra fissa · 0–10 s`), the default: each point
  stays in the same position, the axis remains at 0–10 seconds, and a new sweep
  starts at the end. The window also restarts after a reset or a new capture.
- **Scrolling · latest 10 s** (`Scorrimento · ultimi 10 s`): earlier points move
  left to keep the latest ten seconds visible.

The **Fixed Y scale** checkbox (`Scala Y fissa`), initially enabled, keeps the
upper plot's amplitude range at 0–1.5 on the linear scale, or −120 to +6 dBFS.
When disabled, the upper limit follows the current peak. RMS/peak history always
uses the −120..+6 dBFS scale. To freeze the entire amplitude display, use
**Pause amplitude** (`Pausa ampiezza`); radio data continues to be processed.
These display options do not change B200 configuration or capture.

RMS and peak aggregate all blocks read from the radio between updates, at a
maximum update rate of approximately 10 Hz. The time-domain plot displays the
block with the highest peak in that interval; its time axis in microseconds
refers to that block. Reduction to 256 intervals preserves minima and maxima,
so a narrow pulse is not lost through simple display subsampling.
This is not a continuous history of every sample, and it does not reconstruct
samples lost before delivery by UHD. After an overflow or a reconfiguration
that resets RX, measurement restarts with a new window.

Definitions: `RMS = sqrt(mean(I²+Q²))`, `peak = max(|I+jQ|)` and
`dBFS = 20 log10(amplitude)`, referenced to complex amplitude 1.
Zero measurements use a numerical floor of −160 dBFS; the dBFS plot has a lower
limit of −120 dBFS. The full-scale indicator checks
`max(|I|,|Q|) >= 0.999`: it describes the digital samples delivered by UHD and is
not a definitive diagnosis of ADC or RF front-end saturation. Levels are not
calibrated in dBm and also depend on the configured RX gain. If the stream
stops, the GUI indicates that the last measurement is no longer live while
keeping the latest capture visible.

If firmware/FPGA images must be installed manually, use the downloader supplied
with UHD. With Homebrew it is normally located at:

```sh
python3 /opt/homebrew/opt/uhd/lib/uhd/utils/uhd_images_downloader.py
```

If the default image directory is not writable:

```sh
python3 /opt/homebrew/opt/uhd/lib/uhd/utils/uhd_images_downloader.py --types b2xx --install-location "$PWD/uhd-images"
UHD_IMAGES_DIR="$PWD/uhd-images" ./avvia.command
```

Do not install a PyPI package named `uhd`: the application uses the C library
installed by Homebrew directly. Its path can be specified with
`--uhd-library /path/to/libuhd.dylib` or `UHD_LIBRARY`.

## Connections and formats

| Path | Behavior |
|---|---|
| UDP `127.0.0.1:52001` → B200 | Each datagram is an MSDU payload, up to 1500 bytes; a 24-byte MAC DATA header and FCS are added |
| B200 → UDP `127.0.0.1:51001` | Complete MPDU including the MAC header, without FCS, matching the original flowgraph's PHY→UDP connection |
| B200 → `ieee80211p_rx.pcap` | Frames with FCS and a Radiotap header, readable in Wireshark |

The PCAP file is overwritten on each start. Use `--pcap different-name.pcap`
to preserve earlier captures. RX log lines report CFO and an SNR estimate;
these are not calibrated RSSI measurements.

To forward only the DATA payload to the output port, use `--rx-payload-only`.
In this mode, non-DATA and encrypted frames are saved to PCAP but are not
forwarded over UDP. Decryption, defragmentation and A-MSDU handling are not
implemented. None of these transformations takes place in the default mode.

The defaults are 5860 MHz, 10 MS/s and encoding 3 (QPSK 3/4, 9 Mbit/s on a
10 MHz channel). The source MAC is `04:e5:48:20:3b:c8`; destination and BSSID
are broadcast, as in the original file. The original `config.ini` did not
generate traffic in the connected chain: the actual input is UDP.

For simultaneous RX and TX, connect the RX antenna to **RX2** and the TX antenna
to **TX/RX**. The original flowgraph also selected TX/RX for reception; this
application defaults to RX2 to keep both paths available. For receive-only
operation, `--mode rx --rx-antenna TX/RX` can be used.

```sh
./avvia.command --freq 5890000000 --encoding 0 --rx-gain 0.75 --tx-gain 0.5
./avvia.command --mode rx --pcap capture.pcap
./avvia.command --mode tx --tx-port 52001
./avvia.command --help
```

Gains are normalized to 0..1, as in the original USRP source/sink blocks.
The default digital amplitude is 0.15, separate from RF gain. Each TX burst
has zero padding, and the UHD BURST_ACK is checked. Asynchronous errors and
timeouts stop the application with an error, without counting a successful TX.

## PHY features

STF/LTF preamble, packet detection, coarse and fine CFO estimation, LTF-based
LS equalization, pilot-based phase and slope correction, L-SIG with parity,
scrambling/descrambling, K=7 convolutional coding and soft Viterbi decoding,
puncturing, interleaving, BPSK/QPSK/16-QAM/64-QAM, OFDM with FFT64 and CP16,
and CRC32 FCS. All eight legacy encodings from the flowgraph are supported.

| `--encoding` | Modulation | Code rate | Mbit/s at 10 MHz |
|---|---|---|---|
| 0 | BPSK | 1/2 | 3 |
| 1 | BPSK | 3/4 | 4.5 |
| 2 | QPSK | 1/2 | 6 |
| 3 | QPSK | 3/4 | 9 |
| 4 | 16-QAM | 1/2 | 12 |
| 5 | 16-QAM | 3/4 | 18 |
| 6 | 64-QAM | 2/3 | 24 |
| 7 | 64-QAM | 3/4 | 27 |

The sample rate can be 5, 10 or 20 MS/s; FFT/CP sizes remain unchanged and symbol
durations scale accordingly. 802.11p corresponds to 10 MS/s. TX and RX share one
center frequency. Implemented equalizers are LS, decision-directed LMS
(alpha 0.5), pilot-based Linear Comb (alpha 0.2) and STA (alpha 0.5, beta 2),
following the formulas in the supplied sources. Synchronization and pilot-based
phase/slope correction remain those of the Python modem: no bit-for-bit numerical
equivalence of the complete GNU Radio receiver is claimed.
Gqrx and the fosphor GPU display are not included; neither appears in the supplied
flowgraph's GUI.

## Validation status and limitations

This is a new modem implementation, not an already qualified replacement for
gr-ieee802-11. The internal test compares this implementation's TX and RX; that
alone does not demonstrate interoperability with an external radio.
Regression tests additionally cover FCS errors, consecutive packets, interrupted
captures, PCAP output, and comparison of encoding functions with the supplied
C++ sources.

No B200 was detected in the development environment, so USB streaming, RF
transmission, sensitivity and interoperability with RSUs/OBUs have not been
validated. An interoperability test must check both directions against an
independent device, initially at encoding 0 and with low traffic.

CSMA/CA, CCA, ACK/SIFS, MAC retransmissions and DCC are not implemented; the
supplied flowgraph did not implement these protocols either. This application
is not equivalent to a kernel OCB Wi-Fi interface. HT/VHT/HE PHYs and LDPC are
not implemented.

The Python decoder takes much longer than packet airtime. The application reads
the radio in a separate thread, uses a bounded IQ queue, and reports overflows
and dropped blocks. After every discontinuity, it restarts synchronization
without concatenating non-contiguous samples. Numba helps the decoder but does
not guarantee real-time operation at full load. JIT performance has not been
measured when Numba is unavailable in the test environment.
PCAP timestamps come from the UHD clock initialized to host time: they are
neither GNSS/PPS synchronization nor a validated basis for latency measurements.

## Tests

```sh
./avvia.command --self-test
.venv/bin/python -m unittest discover -s tests -v
```

Python >=3.10. `test_reference.py` optionally uses clang++ and the
`../gr-ieee802-11` sources to verify encoding without linking GNU Radio.
The ABI test requires the Homebrew UHD headers.

Protocol tables and conventions derived from bastibl/gr-ieee802-11, copyright
Bastian Bloessl, GPL-3.0-or-later. The standalone implementation is distributed
under GPL-3.0-or-later; see LICENSE.
