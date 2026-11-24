#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental standalone legacy OFDM modem: Python + NumPy + libuhd.

No GNU Radio imports. Derived protocol tables and coding conventions from
bastibl/gr-ieee802-11 (Copyright Bastian Bloessl, GPL-3.0-or-later).
UDP input is an MSDU; UDP output is the received MPDU without FCS, matching
the supplied flowgraph. Run --self-test before using --mode trx.
"""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import dataclasses
import logging
import math
import os
from pathlib import Path
import queue
import signal
import socket
import struct
import sys
import threading
import time
import zlib

try:
    import numpy as np
except ImportError:
    raise SystemExit("Serve NumPy: python3 -m pip install numpy")

LOG = logging.getLogger("80211p")
RATES = [  # bits/subcarrier, data bits/symbol, SIGNAL field, puncturing
    (1, 24, 13, (1, 1)), (1, 36, 15, (1, 1, 1, 0, 0, 1)),
    (2, 48, 5, (1, 1)), (2, 72, 7, (1, 1, 1, 0, 0, 1)),
    (4, 96, 9, (1, 1)), (4, 144, 11, (1, 1, 1, 0, 0, 1)),
    (6, 192, 1, (1, 1, 1, 0)), (6, 216, 3, (1, 1, 1, 0, 0, 1)),
]
DATA_K = np.array([k for k in range(-26, 27) if k not in (-21, -7, 0, 7, 21)])
DATA = DATA_K % 64
PILOT_K = np.array([-21, -7, 7, 21])
PILOT = PILOT_K % 64
ACTIVE = np.r_[np.arange(-26, 0), np.arange(1, 27)] % 64
LONG_SHIFTED = np.array([
    0,0,0,0,0,0,1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,-1,
    1,-1,1,1,1,1,0,1,-1,-1,1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,1,-1,
    -1,1,-1,1,-1,1,1,1,1,0,0,0,0,0], dtype=np.complex128)
LONG_F = np.fft.ifftshift(LONG_SHIFTED)
LONG_T = np.fft.ifft(LONG_F) * (64 / np.sqrt(52))
SHORT_F = np.zeros(64, dtype=np.complex128)
SHORT_F[np.array([-24,-20,-16,-12,-8,-4,4,8,12,16,20,24]) % 64] = (
    np.array([1,-1,1,-1,-1,1,-1,-1,1,1,1,1]) * (1+1j) * np.sqrt(13/6))
SHORT_T = np.fft.ifft(SHORT_F) * (64 / np.sqrt(52))
PREAMBLE = np.r_[np.tile(SHORT_T[:16], 10), LONG_T[-32:], LONG_T, LONG_T]


def scramble(bits, seed):
    out = np.empty(len(bits), dtype=np.uint8)
    state = int(seed)
    for i, bit in enumerate(bits):
        feedback = ((state >> 6) ^ (state >> 3)) & 1
        out[i] = int(bit) ^ feedback
        state = ((state << 1) & 126) | feedback
    return out


POLARITY = 1 - 2 * scramble(np.zeros(127, dtype=np.uint8), 127).astype(np.int8)
PARITY = np.array([i.bit_count() & 1 for i in range(128)], dtype=np.uint8)
STATES = np.arange(64)
PRED0 = STATES >> 1
PRED1 = PRED0 + 32
INPUT = STATES & 1
REG0 = (PRED0 << 1) | INPUT
REG1 = (PRED1 << 1) | INPUT
EXPECT0 = np.stack([PARITY[REG0 & 0o155], PARITY[REG0 & 0o117]], axis=1)
EXPECT1 = np.stack([PARITY[REG1 & 0o155], PARITY[REG1 & 0o117]], axis=1)


def convolutional(bits):
    state = 0
    out = np.empty(2 * len(bits), dtype=np.uint8)
    for i, bit in enumerate(bits):
        state = ((state << 1) | int(bit)) & 127
        out[2*i:2*i+2] = PARITY[state & 0o155], PARITY[state & 0o117]
    return out


def viterbi(soft, final_zero=False):
    """Max-log decoder; positive soft values favour bit 1, zero is erasure."""
    pairs = np.asarray(soft, dtype=np.float64).reshape(-1, 2)
    metrics = np.full(64, -1e30)
    metrics[0] = 0
    history = np.empty((len(pairs), 64), dtype=np.bool_)
    e0, e1 = 2.0 * EXPECT0 - 1.0, 2.0 * EXPECT1 - 1.0
    for i, pair in enumerate(pairs):
        a = metrics[PRED0] + e0 @ pair
        b = metrics[PRED1] + e1 @ pair
        choose = b > a
        history[i] = choose
        metrics = np.where(choose, b, a)
        metrics -= metrics.max()
    state = 0 if final_zero else int(np.argmax(metrics))
    out = np.empty(len(pairs), dtype=np.uint8)
    for i in range(len(pairs)-1, -1, -1):
        out[i] = state & 1
        state = (state >> 1) | (32 if history[i, state] else 0)
    return out


def interleaver(bpsc):
    n = 48 * bpsc
    k = np.arange(n)
    i = (n // 16) * (k % 16) + k // 16
    s = max(bpsc // 2, 1)
    return s * (i // s) + (i + n - (16 * i) // n) % s


def constellation(bpsc):
    bits = ((np.arange(1 << bpsc)[:, None] >> np.arange(bpsc)) & 1)
    if bpsc == 1:
        points = (2 * bits[:, 0] - 1).astype(complex)
    else:
        half = bpsc // 2
        levels = {1: np.array([-1, 1]), 2: np.array([-3, 3, -1, 1]),
                  3: np.array([-7, 7, -1, 1, -5, 5, -3, 3])}[half]
        axis_index = np.arange(1 << bpsc)
        points = (levels[axis_index & ((1 << half)-1)] +
                  1j * levels[axis_index >> half]) / np.sqrt({2:2, 4:10, 6:42}[bpsc])
    return points, bits


CONST = {b: constellation(b) for b in (1, 2, 4, 6)}


# Optional acceleration. The NumPy decoder remains available without Numba.
ACCELERATED = False
try:
    from numba import njit

    @njit(cache=True, nogil=True)
    def _fast_viterbi(soft, final_zero):
        n = len(soft)//2
        metrics = np.full(64,-1e30)
        metrics[0] = 0.
        history = np.empty((n,64),np.uint8)
        for i in range(n):
            next_metrics = np.empty(64)
            best = -1e30
            for state in range(64):
                p0 = state>>1
                p1 = p0+32
                reg0 = (p0<<1)|(state&1)
                reg1 = (p1<<1)|(state&1)
                a = metrics[p0] + (2.*PARITY[reg0&0o155]-1.)*soft[2*i] + (2.*PARITY[reg0&0o117]-1.)*soft[2*i+1]
                b = metrics[p1] + (2.*PARITY[reg1&0o155]-1.)*soft[2*i] + (2.*PARITY[reg1&0o117]-1.)*soft[2*i+1]
                history[i,state] = 1 if b>a else 0
                next_metrics[state] = max(a,b)
                best = max(best,next_metrics[state])
            metrics = next_metrics-best
        state = 0 if final_zero else np.argmax(metrics)
        out = np.empty(n,np.uint8)
        for i in range(n-1,-1,-1):
            out[i] = state&1
            state = (state>>1)|(32 if history[i,state] else 0)
        return out

    _numpy_viterbi = viterbi

    def viterbi(soft, final_zero=False):
        return _fast_viterbi(np.asarray(soft,dtype=np.float64).ravel(),final_zero)

    ACCELERATED = True
except ImportError:
    pass


def demap(symbols, bpsc, weights=None):
    points, labels = CONST[bpsc]
    dist = abs(np.asarray(symbols).reshape(-1, 1) - points[None, :]) ** 2
    out = np.stack([dist[:, labels[:, b] == 0].min(axis=1) -
                    dist[:, labels[:, b] == 1].min(axis=1) for b in range(bpsc)], axis=1)
    if weights is not None:
        out *= np.asarray(weights).reshape(-1, 1)
    return out.ravel()


def coded_symbols(bits, encoding):
    bpsc, _, _, pattern = RATES[encoding]
    encoded = convolutional(bits)
    coded = encoded[np.resize(np.array(pattern, bool), len(encoded))]
    rows = coded.reshape(-1, 48 * bpsc)
    inter = np.empty_like(rows)
    inter[:, interleaver(bpsc)] = rows
    groups = inter.reshape(-1, 48, bpsc)
    indexes = (groups * (1 << np.arange(bpsc))).sum(axis=2)
    return CONST[bpsc][0][indexes]


def ofdm(data, first_pilot):
    bins = np.zeros((len(data), 64), dtype=complex)
    bins[:, DATA] = data
    p = POLARITY[(np.arange(len(data)) + first_pilot) % 127]
    bins[:, PILOT] = p[:, None] * np.array([1, 1, 1, -1])
    symbols = np.fft.ifft(bins, axis=1) * (64 / np.sqrt(52))
    return np.c_[symbols[:, -16:], symbols].ravel()


def encode(psdu, encoding=3, seed=93, amplitude=0.15):
    if not 4 <= len(psdu) <= 4095:
        raise ValueError("PSDU deve includere FCS e avere lunghezza 4..4095")
    if not 1 <= seed <= 127:
        raise ValueError("Scrambler seed deve essere 1..127")
    _, dbps, rate, _ = RATES[encoding]
    sig = np.zeros(24, dtype=np.uint8)
    sig[:4] = [(rate >> b) & 1 for b in (3, 2, 1, 0)]
    sig[5:17] = [(len(psdu) >> b) & 1 for b in range(12)]
    sig[17] = sig[:17].sum() % 2
    nsyms = math.ceil((16 + 8 * len(psdu) + 6) / dbps)
    bits = np.zeros(nsyms * dbps, dtype=np.uint8)
    bits[16:16+8*len(psdu)] = np.unpackbits(np.frombuffer(psdu, np.uint8), bitorder="little")
    bits = scramble(bits, seed)
    bits[16+8*len(psdu):22+8*len(psdu)] = 0
    iq = np.r_[PREAMBLE, ofdm(coded_symbols(sig, 0), 0),
               ofdm(coded_symbols(bits, encoding), 1)] * amplitude
    # Keep the DAC in range without clipping; no payload-dependent clipping.
    iq /= max(1.0, float(abs(iq).max()) / 0.95)
    return iq.astype(np.complex64)


def make_mpdu(payload, sequence=0, src=bytes.fromhex("04e548203bc8"),
              dst=b"\xff"*6, bssid=b"\xff"*6):
    if len(payload) > 1500:
        raise ValueError("Payload UDP oltre 1500 byte")
    frame = struct.pack("<HH", 8, 0) + dst + src + bssid + struct.pack("<H", (sequence & 4095) << 4) + payload
    return frame + struct.pack("<I", zlib.crc32(frame))


def check_fcs(psdu):
    return len(psdu) >= 4 and struct.pack("<I", zlib.crc32(psdu[:-4])) == psdu[-4:]


@dataclasses.dataclass
class Frame:
    psdu: bytes
    encoding: int
    timestamp: float
    cfo_hz: float
    snr_db: float


class BadFrame(ValueError):
    pass


class NeedSamples(Exception):
    pass


class Receiver:
    """Chunk-safe STF detector, LTF LS channel estimate and pilot phase tracking."""
    def __init__(self, sample_rate=10e6, threshold=0.72, chan_est=0, on_symbols=None):
        self.rate = sample_rate
        self.threshold = threshold
        self.buffer = np.empty(0, np.complex64)
        self.timestamp = 0.0
        self.pending = False
        self.bad = 0
        self.good = 0
        self.chan_est = chan_est
        self.on_symbols = on_symbols

    def reset(self):
        self.buffer = np.empty(0, np.complex64)
        self.pending = False

    def discard(self, n):
        self.buffer = self.buffer[n:]
        self.timestamp += n / self.rate
        self.pending = False

    @staticmethod
    def moving(x, n):
        acc = np.r_[0, np.cumsum(x)]
        return acc[n:] - acc[:-n]

    def detect(self):
        x = self.buffer
        n = 48
        if len(x) < n + 16 + 24:
            return None
        p = self.moving(x[16:] * x[:-16].conj(), n)
        a = self.moving(abs(x[:-16])**2, n)
        b = self.moving(abs(x[16:])**2, n)
        valid = (abs(p)**2 / np.maximum(a*b, 1e-25) > self.threshold**2) & (a/n > 1e-10)
        runs = np.convolve(valid.astype(np.int16), np.ones(24, np.int16), mode="valid")
        hits = np.flatnonzero(runs == 24)
        return int(hits[0]) if len(hits) else None

    def locate(self):
        # A detection can begin before the actual STF because of partial windows.
        x = self.buffer
        if len(x) < 640:
            raise NeedSamples
        seg = x[:112]
        coarse = np.angle(np.vdot(seg[:-16], seg[16:])) / 16
        search = x[:min(len(x), 640)] * np.exp(-1j * coarse * np.arange(min(len(x),640)))
        corr = np.correlate(search, LONG_T, "valid")
        energy = self.moving(abs(search)**2, 64)
        score = abs(corr)**2 / np.maximum(energy * np.vdot(LONG_T,LONG_T).real, 1e-25)
        pair = np.minimum(score[:-64], score[64:])
        # LTF must occur near STF; avoid locking onto payload or a later packet.
        pair[350:] = 0
        pos = int(np.argmax(pair))
        if pair[pos] < 0.42:
            raise BadFrame("LTF non trovato")
        residual = np.angle(np.vdot(search[pos:pos+64], search[pos+64:pos+128])) / 64
        return pos, coarse + residual

    def decode(self):
        t, cfo = self.locate()
        x = self.buffer
        def fft_at(start):
            if start + 64 > len(x):
                raise NeedSamples
            return np.fft.fft(x[start:start+64] * np.exp(-1j*cfo*np.arange(start,start+64)))
        l1, l2 = fft_at(t), fft_at(t+64)
        h = np.ones(64, dtype=complex)
        h[ACTIVE] = (l1[ACTIVE] + l2[ACTIVE]) / (2 * LONG_F[ACTIVE])
        if np.min(abs(h[PILOT])) < 1e-9:
            raise BadFrame("Piloti in fading")
        weights = abs(h[DATA])**2
        weights /= max(float(weights.mean()), 1e-20)
        noise = np.mean(abs(l1[ACTIVE]-l2[ACTIVE])**2) / 2
        snr = 10*np.log10(max(float(np.mean(abs(h[ACTIVE])**2)),1e-20) / max(float(noise),1e-20))
        channel = ChannelEqualizer(h, self.chan_est, l1, l2)
        def equalized(symbol, bpsc=1):
            spectrum = fft_at(t+128+80*symbol+16)
            symbols = channel.equalize(spectrum, symbol, bpsc)
            if symbol > 0 and self.on_symbols is not None:
                # Same tap as frame_equalizer's carrier port: before MAC/FCS.
                self.on_symbols(symbols)
            return symbols
        sig = viterbi(demap(equalized(0), 1, weights)[interleaver(1)], True)
        if sig[4] or np.any(sig[18:]) or int(sig[:18].sum()) % 2:
            raise BadFrame("SIGNAL parity/tail")
        rate_field = sum(int(sig[i]) << (3-i) for i in range(4))
        enc = next((i for i,r in enumerate(RATES) if r[2]==rate_field), None)
        if enc is None:
            raise BadFrame("SIGNAL RATE")
        length = sum(int(sig[5+i]) << i for i in range(12))
        if not 4 <= length <= 4095:
            raise BadFrame("SIGNAL LENGTH")
        bpsc, dbps, _, pattern = RATES[enc]
        nsym = math.ceil((16+8*length+6)/dbps)
        end = t+128+80*(1+nsym)
        if len(x) < end:
            raise NeedSamples
        soft = np.stack([demap(equalized(i+1,bpsc),bpsc,weights)[interleaver(bpsc)]
                         for i in range(nsym)]).ravel()
        mask = np.resize(np.array(pattern,bool), nsym*dbps*2)
        depunct = np.zeros(len(mask))
        depunct[mask] = soft
        # Tail is before scrambled PAD; force state zero at tail, not after PAD.
        bits = viterbi(depunct[:2*(16+8*length+6)], True)
        # Recover state after the first seven scrambled SERVICE zero bits.
        state = sum(int(bits[i]) << (6-i) for i in range(7))
        clear = np.zeros(16+8*length, np.uint8)
        clear[7:] = scramble(bits[7:16+8*length], state)
        if np.any(clear[7:16]):
            raise BadFrame("SERVICE")
        psdu = np.packbits(clear[16:], bitorder="little").tobytes()
        if not check_fcs(psdu):
            raise BadFrame("FCS")
        return Frame(psdu, enc, self.timestamp+(t-192)/self.rate,
                     cfo*self.rate/(2*np.pi), float(snr)), end

    def feed(self, samples, timestamp=None):
        if not len(self.buffer):
            self.timestamp = time.time() if timestamp is None else timestamp
        self.buffer = np.r_[self.buffer, np.asarray(samples,dtype=np.complex64)]
        frames = []
        while True:
            if not self.pending:
                hit = self.detect()
                if hit is None:
                    if len(self.buffer) > 192:
                        self.discard(len(self.buffer)-192)
                    break
                self.discard(max(0, hit-16))
                self.pending = True
            try:
                frame, end = self.decode()
            except NeedSamples:
                break
            except BadFrame:
                self.bad += 1
                self.discard(32)
                continue
            frames.append(frame)
            self.good += 1
            self.discard(end)
        return frames


class ChannelEqualizer:
    """LS, decision-directed LMS, linear pilot comb and STA (alpha/beta upstream).

    The native modem retains its own pilot phase/slope correction. This is not
    a claim of bit-for-bit equivalence to GNU Radio's synchronizer/equalizer.
    """
    def __init__(self, channel, algorithm=0, l1=None, l2=None):
        if algorithm not in range(4):
            raise ValueError("Equalizzatore deve essere 0..3")
        self.h = channel.copy()
        self.algorithm = algorithm
        if algorithm == 2 and l1 is not None:
            a = self.comb(l1, LONG_F[PILOT])
            b = self.comb(l2, LONG_F[PILOT])
            self.h = 0.8*a + 0.2*b

    @staticmethod
    def comb(spectrum, pilot_ref):
        pilots = spectrum[PILOT] / pilot_ref
        average = pilots.mean()
        shifted = np.interp(np.arange(64), [0,11,25,39,53,64],
                            np.r_[average,pilots,average])
        return np.fft.ifftshift(shifted)

    def equalize(self, spectrum, symbol, bpsc):
        safe = np.where(abs(self.h)>1e-12, self.h, 1e-12)
        ref = POLARITY[symbol%127] * np.array([1,1,1,-1])
        phases = np.unwrap(np.angle(spectrum[PILOT]/safe[PILOT]*ref))
        slope,phase = np.polyfit(PILOT_K,phases,1)
        carriers = np.fft.fftfreq(64)*64
        corrected = spectrum*np.exp(-1j*(phase+slope*carriers))
        if self.algorithm == 2:
            self.h = 0.8*self.h + 0.2*self.comb(corrected,ref)
        safe = np.where(abs(self.h)>1e-12,self.h,1e-12)
        symbols = corrected[DATA]/safe[DATA]
        if self.algorithm in (1,3):
            points = CONST[bpsc][0]
            decisions = points[abs(symbols[:,None]-points).argmin(axis=1)]
            estimate = self.h.copy()
            estimate[DATA] = corrected[DATA]/decisions
            estimate[PILOT] = corrected[PILOT]/ref
            if self.algorithm == 1:
                self.h[DATA] = 0.5*self.h[DATA]+0.5*estimate[DATA]
            else:
                shifted = np.fft.fftshift(estimate)
                updated = shifted.copy()
                for i in range(6,59):
                    indexes = [k for k in range(i-2,i+3) if 6<=k<=58 and k!=32]
                    updated[i] = shifted[indexes].mean()
                update = np.fft.ifftshift(updated)
                self.h[ACTIVE] = 0.5*self.h[ACTIVE]+0.5*update[ACTIVE]
        return symbols


class Pcap:
    """Radiotap PCAP, host epoch timestamps; frames include the FCS."""
    def __init__(self, path, frequency, sample_rate):
        self.file = open(path, "wb")
        self.file.write(struct.pack("<IHHIIII",0xa1b2c3d4,2,4,0,0,65535,127))
        self.frequency = frequency
        self.rate = sample_rate

    def write(self, frame):
        # FLAGS (FCS present), RATE, CHANNEL. Radiotap channel has 2-byte alignment.
        mbps = RATES[frame.encoding][1] * self.rate / 80 / 1e6
        flags = 0x10
        channel_flags = 0x40 | (0x100 if self.frequency >= 3e9 else 0x80)
        if self.rate == 10e6:
            channel_flags |= 0x4000  # Half-rate OFDM channel.
        elif self.rate == 5e6:
            channel_flags |= 0x8000  # Quarter-rate OFDM channel.
        rt = struct.pack("<BBHIBBHH",0,0,14,0x0e,flags,round(mbps*2),
                         round(self.frequency/1e6),channel_flags)
        packet = rt + frame.psdu
        usec = round(frame.timestamp*1e6)
        self.file.write(struct.pack("<IIII",usec//1000000,usec%1000000,len(packet),len(packet)))
        self.file.write(packet)
        self.file.flush()

    def close(self):
        self.file.close()


# UHD C ABI: installed public headers in include/uhd/usrp/usrp.h and types/*.h.
H = C.c_void_p
Z = C.c_size_t
D = C.c_double
B = C.c_bool
I = C.c_int
P = C.POINTER


class StreamArgs(C.Structure):
    _fields_ = [("cpu",C.c_char_p),("otw",C.c_char_p),("args",C.c_char_p),
                ("channels",P(Z)),("n",I)]


class StreamCmd(C.Structure):
    _fields_ = [("mode",I),("n",Z),("now",B),("secs",C.c_int64),("frac",D)]


class TuneRequest(C.Structure):
    _fields_ = [("target",D),("rf_policy",I),("rf",D),("dsp_policy",I),
                ("dsp",D),("args",C.c_char_p)]


class TuneResult(C.Structure):
    _fields_ = [(n,D) for n in ("clipped","target","actual","dsp_target","dsp_actual")]


class UHD:
    def __init__(self, library=None):
        candidates = [library, os.environ.get("UHD_LIBRARY"),
                      "/opt/homebrew/opt/uhd/lib/libuhd.dylib",
                      "/usr/local/opt/uhd/lib/libuhd.dylib", ctypes.util.find_library("uhd")]
        errors = []
        for path in filter(None,candidates):
            try:
                self.lib = C.CDLL(path)
                break
            except OSError as exc:
                errors.append(str(exc))
        else:
            raise RuntimeError("libuhd non trovata. Installa: brew install uhd\n" + "\n".join(errors))
        self.functions = {}
        def bind(name, *args):
            f = getattr(self.lib,"uhd_"+name)
            f.argtypes = list(args)
            f.restype = I
            self.functions[name] = f
        bind("get_last_error",C.c_char_p,Z)
        for kind in ("usrp","rx_streamer","tx_streamer","rx_metadata","tx_metadata","async_metadata"):
            bind(kind+"_free",P(H))
        bind("usrp_make",P(H),C.c_char_p)
        for kind in ("rx_streamer","tx_streamer","rx_metadata","async_metadata"):
            bind(kind+"_make",P(H))
        bind("tx_metadata_make",P(H),B,C.c_int64,D,B,B)
        bind("usrp_get_pp_string",H,C.c_char_p,Z)
        bind("usrp_set_time_now",H,C.c_int64,D,Z)
        for direction in ("rx","tx"):
            for what in ("rate","bandwidth","normalized_"+direction+"_gain"):
                name = "usrp_set_"+direction+"_"+what if not what.startswith("normalized") else "usrp_set_"+what
                bind(name,H,D,Z)
            bind("usrp_get_"+direction+"_rate",H,Z,P(D))
            bind("usrp_set_"+direction+"_freq",H,P(TuneRequest),Z,P(TuneResult))
            bind("usrp_set_"+direction+"_antenna",H,C.c_char_p,Z)
            bind("usrp_get_"+direction+"_stream",H,P(StreamArgs),H)
        bind("rx_streamer_issue_stream_cmd",H,P(StreamCmd))
        bind("rx_streamer_recv",H,P(H),Z,P(H),D,B,P(Z))
        bind("rx_metadata_error_code",H,P(I))
        bind("rx_metadata_time_spec",H,P(C.c_int64),P(D))
        bind("tx_streamer_send",H,P(H),Z,P(H),D,P(Z))
        bind("tx_streamer_recv_async_msg",H,P(H),D,P(B))
        bind("async_metadata_event_code",H,P(I))

    def call(self,name,*args):
        rc = self.functions[name](*args)
        if rc:
            error = C.create_string_buffer(2048)
            self.functions["get_last_error"](error,len(error))
            raise RuntimeError(f"UHD {name}: {error.value.decode(errors='replace')} (code {rc})")


class Radio:
    def __init__(self, args):
        self.api = UHD(args.uhd_library)
        self.handles = []
        self.usrp = H()
        self.api.call("usrp_make",C.byref(self.usrp),args.device.encode())
        self.handles.append(("usrp",self.usrp))
        try:
            text = C.create_string_buffer(4096)
            self.api.call("usrp_get_pp_string",self.usrp,text,len(text))
            LOG.info("%s",text.value.decode())
            now = time.time()
            self.api.call("usrp_set_time_now",self.usrp,int(now),now-int(now),0)
            self.rx = self.tx = None
            for direction in ("rx","tx"):
                if args.mode not in (direction,"trx"):
                    continue
                self.api.call("usrp_set_"+direction+"_rate",self.usrp,args.sample_rate,0)
                actual = D()
                self.api.call("usrp_get_"+direction+"_rate",self.usrp,0,C.byref(actual))
                if abs(actual.value-args.sample_rate)>1:
                    raise RuntimeError(f"Sample rate {direction} effettivo {actual.value}, richiesto {args.sample_rate}")
                self.api.call("usrp_set_"+direction+"_bandwidth",self.usrp,args.sample_rate,0)
                self.api.call("usrp_set_normalized_"+direction+"_gain",self.usrp,getattr(args,direction+"_gain"),0)
                self.api.call("usrp_set_"+direction+"_antenna",self.usrp,getattr(args,direction+"_antenna").encode(),0)
                req = TuneRequest(args.freq,77 if args.lo_offset else 65,args.freq-args.lo_offset,65,0,b"")
                result = TuneResult()
                self.api.call("usrp_set_"+direction+"_freq",self.usrp,C.byref(req),0,C.byref(result))
                streamer = H()
                self.api.call(direction+"_streamer_make",C.byref(streamer))
                self.handles.append((direction+"_streamer",streamer))
                channels = (Z*1)(0)
                sa = StreamArgs(b"fc32",b"sc16",b"",channels,1)
                self.api.call("usrp_get_"+direction+"_stream",self.usrp,C.byref(sa),streamer)
                setattr(self,direction,streamer)
            self.rxmd = self.new_metadata("rx_metadata") if self.rx else None
            self.asyncmd = self.new_metadata("async_metadata") if self.tx else None
            self.txmd = {}
            if self.tx:
                for sob,eob in ((True,False),(False,False),(False,True)):
                    self.txmd[sob,eob] = self.new_metadata("tx_metadata",False,0,0.,sob,eob)
        except BaseException:
            self.close()
            raise

    def new_metadata(self,kind,*args):
        h = H()
        self.api.call(kind+"_make",C.byref(h),*args)
        self.handles.append((kind,h))
        return h

    def command(self,start):
        cmd = StreamCmd(97 if start else 111,0,True,0,0.)
        self.api.call("rx_streamer_issue_stream_cmd",self.rx,C.byref(cmd))

    def receive(self,count):
        iq = np.empty(count,np.complex64)
        pointers = (H*1)(iq.ctypes.data)
        n,code = Z(),I()
        self.api.call("rx_streamer_recv",self.rx,pointers,count,C.byref(self.rxmd),0.2,False,C.byref(n))
        self.api.call("rx_metadata_error_code",self.rxmd,C.byref(code))
        secs,frac = C.c_int64(),D()
        self.api.call("rx_metadata_time_spec",self.rxmd,C.byref(secs),C.byref(frac))
        if code.value not in (0,1,8):
            raise RuntimeError(f"UHD RX metadata error 0x{code.value:x}")
        return iq[:n.value],secs.value+frac.value,code.value

    def transmit(self,iq):
        iq = np.ascontiguousarray(np.r_[np.zeros(100,np.complex64),iq,np.zeros(1000,np.complex64)],dtype=np.complex64)
        offset = 0
        while offset < len(iq):
            pointers = (H*1)(iq.ctypes.data+offset*8)
            n = Z()
            md = self.txmd[offset==0,False]
            self.api.call("tx_streamer_send",self.tx,pointers,len(iq)-offset,C.byref(md),1.,C.byref(n))
            if n.value == 0:
                raise RuntimeError("UHD TX timeout: nessun campione accettato")
            offset += n.value
        n = Z()
        md = self.txmd[False,True]
        pointers = (H*1)(iq.ctypes.data)
        self.api.call("tx_streamer_send",self.tx,pointers,0,C.byref(md),1.,C.byref(n))
        deadline = time.monotonic()+1.
        while time.monotonic() < deadline:
            valid = B()
            self.api.call("tx_streamer_recv_async_msg",self.tx,C.byref(self.asyncmd),0.05,C.byref(valid))
            if valid.value:
                code = I()
                self.api.call("async_metadata_event_code",self.asyncmd,C.byref(code))
                if code.value == 1:
                    return
                raise RuntimeError(f"UHD TX async error 0x{code.value:x}: burst non confermato")
        raise RuntimeError("UHD TX: nessun BURST_ACK entro 1 secondo")

    def reconfigure(self, args, changed):
        """Called only with capture/decode/TX paused at block/burst boundaries."""
        for direction in ("rx","tx"):
            if not getattr(self,direction):
                continue
            if "sample_rate" in changed:
                self.api.call("usrp_set_"+direction+"_rate",self.usrp,args.sample_rate,0)
                actual = D()
                self.api.call("usrp_get_"+direction+"_rate",self.usrp,0,C.byref(actual))
                if abs(actual.value-args.sample_rate)>1:
                    raise RuntimeError(f"Sample rate {direction} effettivo {actual.value}")
                self.api.call("usrp_set_"+direction+"_bandwidth",self.usrp,args.sample_rate,0)
            if direction+"_gain" in changed:
                self.api.call("usrp_set_normalized_"+direction+"_gain",self.usrp,getattr(args,direction+"_gain"),0)
            if {"freq","lo_offset"} & changed:
                req = TuneRequest(args.freq,77 if args.lo_offset else 65,args.freq-args.lo_offset,65,0,b"")
                result = TuneResult()
                self.api.call("usrp_set_"+direction+"_freq",self.usrp,C.byref(req),0,C.byref(result))

    def close(self):
        for kind,h in reversed(self.handles):
            try:
                self.api.call(kind+"_free",C.byref(h))
            except Exception as exc:
                LOG.warning("Cleanup %s: %s",kind,exc)
        self.handles.clear()


def parse_mac(value):
    try:
        result = bytes.fromhex(value.replace(":",""))
        if len(result)==6:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("MAC atteso: 04:e5:48:20:3b:c8")


def run(args, control=None):
    stop = control.stop_event if control is not None else threading.Event()
    failures = queue.Queue()
    iq_queue = queue.Queue(maxsize=args.queue_blocks)
    counters = dict(rx=0,tx=0,overflow=0,dropped=0,crc_or_sync=0)
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_: stop.set())
    radio = Radio(args)
    sockets = []
    pcap = None
    threads = []
    capture_lock, decode_lock, tx_lock = threading.Lock(),threading.Lock(),threading.Lock()
    reconfiguring = threading.Event()
    def guarded(fn):
        def worker():
            try:
                fn()
            except BaseException as exc:
                failures.put(exc)
                stop.set()
        return worker
    def start(fn):
        t = threading.Thread(target=guarded(fn),name=fn.__name__,daemon=True)
        threads.append(t)
        t.start()
    try:
        if ACCELERATED:
            # Compile before starting the radio; compilation must not starve RX.
            viterbi(np.zeros(48),True)
        else:
            LOG.warning("Numba assente: decoder NumPy più lento. Per accelerare: python -m pip install numba")
        if radio.rx:
            pcap = Pcap(args.pcap,args.freq,args.sample_rate)
            udp_out = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            sockets.append(udp_out)
            receiver = Receiver(args.sample_rate,args.threshold,getattr(args,"chan_est",0),
                                control.symbols if control is not None else None)
            def capture():
                discontinuity = False
                while not stop.is_set():
                    if reconfiguring.is_set():
                        stop.wait(0.005)
                        continue
                    with capture_lock:
                        iq,ts,error = radio.receive(args.block_size)
                        if error==8:
                            counters["overflow"] += 1
                            discontinuity = True
                        if not len(iq):
                            continue
                        if control is not None:
                            # Raw IQ tap precedes the decode queue and packet detection.
                            control.samples(iq,args.sample_rate,ts,error==8,center_freq=args.freq)
                        try:
                            iq_queue.put_nowait((iq,ts,discontinuity))
                            discontinuity = False
                        except queue.Full:
                            counters["dropped"] += 1
                            discontinuity = True
            def decode_worker():
                while not stop.is_set():
                    if reconfiguring.is_set():
                        stop.wait(0.005)
                        continue
                    with decode_lock:
                        try:
                            iq,ts,gap = iq_queue.get(timeout=0.02)
                        except queue.Empty:
                            continue
                        if gap:
                            receiver.reset()
                        for frame in receiver.feed(iq,ts):
                            pcap.write(frame)
                            # Original PHY output: MAC header retained, FCS stripped.
                            data = frame.psdu[:-4]
                            if args.rx_payload_only:
                                try:
                                    data = extract_payload(data)
                                except ValueError:
                                    continue
                            udp_out.sendto(data,(args.rx_host,args.rx_port))
                            counters["rx"] += 1
                            if control is not None:
                                control.frame(frame)
                            LOG.info("RX %d B enc=%d CFO=%.0f Hz SNR~%.1f dB",len(data),frame.encoding,frame.cfo_hz,frame.snr_db)
                        counters["crc_or_sync"] = receiver.bad
            radio.command(True)
            start(capture)
            start(decode_worker)
        if radio.tx:
            udp_in = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            sockets.append(udp_in)
            udp_in.bind((args.tx_host,args.tx_port))
            udp_in.settimeout(0.2)
            def transmit_worker():
                seq = 0
                while not stop.is_set():
                    try:
                        payload,_ = udp_in.recvfrom(65535)
                    except socket.timeout:
                        continue
                    if len(payload)>1500:
                        LOG.warning("Scartato payload UDP %d B (>1500)",len(payload))
                        continue
                    with tx_lock:
                        psdu = make_mpdu(payload,seq,args.src,args.dst,args.bssid)
                        radio.transmit(encode(psdu,args.encoding,seq%127+1,args.amplitude))
                        seq = (seq+1)%4096
                        counters["tx"] += 1
                    LOG.info("TX payload %d B",len(payload))
            start(transmit_worker)
        LOG.info("Modalità %s, %.3f MHz, %.1f MS/s; Ctrl-C per fermare",args.mode,args.freq/1e6,args.sample_rate/1e6)
        LOG.info("UDP TX %s:%d -> radio; radio -> UDP %s:%d; PCAP %s",args.tx_host,args.tx_port,args.rx_host,args.rx_port,args.pcap)
        if control is not None:
            control.ready()
        next_log = time.monotonic()+5
        while not stop.wait(0.1):
            if control is not None:
                control.stats(counters,iq_queue.qsize())
                try:
                    settings = control.commands.get_nowait()
                except queue.Empty:
                    settings = None
                if settings:
                    changed = {k for k,v in settings.items() if getattr(args,k,None)!=v}
                    if changed:
                        reconfiguring.set()
                        with capture_lock,decode_lock,tx_lock:
                            reset = bool(changed & {"freq","sample_rate","lo_offset","chan_est"})
                            if radio.rx and reset:
                                radio.command(False)
                            for k,v in settings.items():
                                setattr(args,k,v)
                            radio.reconfigure(args,changed)
                            if radio.rx and reset:
                                while not iq_queue.empty():
                                    try:
                                        iq_queue.get_nowait()
                                    except queue.Empty:
                                        break
                                receiver.reset()
                                receiver.rate = args.sample_rate
                                receiver.chan_est = args.chan_est
                                pcap.frequency,pcap.rate = args.freq,args.sample_rate
                                radio.command(True)
                        reconfiguring.clear()
                        control.applied(settings,reset)
            if time.monotonic()>=next_log:
                LOG.info("Contatori %s; coda IQ %d/%d",counters,iq_queue.qsize(),args.queue_blocks)
                next_log = time.monotonic()+5
    finally:
        stop.set()
        if radio.rx:
            try:
                radio.command(False)
            except Exception as exc:
                LOG.warning("Stop RX: %s",exc)
        for t in threads:
            t.join(timeout=10)
        if any(t.is_alive() for t in threads):
            # Never free C handles while a worker is still using them.
            LOG.error("Worker ancora attivo; uscita senza liberare handle UHD")
        else:
            radio.close()
            if pcap:
                pcap.close()
            for s in sockets:
                s.close()
        LOG.info("Contatori finali %s",counters)
    if not failures.empty():
        raise RuntimeError("Worker fallito") from failures.get()


def extract_payload(mpdu):
    if len(mpdu)<24:
        raise ValueError("MPDU troppo corto")
    fc = int.from_bytes(mpdu[:2],"little")
    if (fc>>2)&3 != 2 or fc & 0x4000:
        raise ValueError("--rx-payload-only richiede frame DATA non cifrati")
    hdr = 24 + (6 if fc&0x300==0x300 else 0)
    if fc & 0x80:
        hdr += 2
        if fc & 0x8000:
            hdr += 4
    if len(mpdu)<hdr:
        raise ValueError("Header DATA incompleto")
    return mpdu[hdr:]


def self_test():
    rng = np.random.default_rng(80211)
    count = 0
    for enc in range(8):
        for size in (0,37,500,1500):
            payload = rng.bytes(size)
            psdu = make_mpdu(payload,count)
            tx = encode(psdu,enc,seed=count%127+1)
            # Unknown arrival time, two-tap multipath, CFO, arbitrary phase, AWGN.
            ch = np.convolve(tx,np.array([1,0,0,0.12+0.07j]))
            iq = np.r_[np.zeros(293),ch,np.zeros(800)].astype(complex)
            iq *= np.exp(1j*(0.83+2*np.pi*22000*np.arange(len(iq))/10e6))
            iq += (rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))*0.0007
            rx = Receiver()
            frames = []
            for pos in range(0,len(iq),511):
                frames.extend(rx.feed(iq[pos:pos+511],1700000000+pos/10e6))
            if len(frames)!=1 or frames[0].psdu!=psdu or frames[0].encoding!=enc:
                raise AssertionError(f"Loopback fallito enc={enc} size={size}: {len(frames)} frame, bad={rx.bad}")
            if abs(frames[0].cfo_hz-22000)>1000:
                raise AssertionError("CFO estimate")
            count += 1
    print(f"PASS: {count} frame TX/RX, 8 encoding, 0..1500 B, CFO/multipath/AWGN, chunk da 511 campioni.")
    print("Test software interno: non dimostra interoperabilità RF con apparati 802.11p.")


def main():
    p = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test",action="store_true")
    p.add_argument("--gui",action="store_true",help="Apri la GUI locale con i controlli del flowgraph originale")
    p.add_argument("--no-gui",action="store_true",help="Esecuzione da terminale")
    p.add_argument("--gui-demo",action="store_true",help="GUI con loopback software esplicitamente simulato, senza radio")
    p.add_argument("--no-browser",action="store_true",help="Non aprire automaticamente il browser")
    p.add_argument("--gui-port",type=int,default=0,help="Porta HTTP locale; 0 assegna una porta libera")
    p.add_argument("--mode",choices=("rx","tx","trx"),default="trx")
    p.add_argument("--freq",type=float,default=5860e6)
    p.add_argument("--sample-rate",type=float,choices=(5e6,10e6,20e6),default=10e6)
    p.add_argument("--device",default="type=b200")
    p.add_argument("--uhd-library")
    p.add_argument("--rx-gain",type=float,default=0.75,help="Gain normalizzato 0..1")
    p.add_argument("--tx-gain",type=float,default=1.0,help="Gain normalizzato 0..1")
    p.add_argument("--rx-antenna",default="RX2",help="RX2 per RX/TX contemporanei sulla B200")
    p.add_argument("--tx-antenna",default="TX/RX")
    p.add_argument("--lo-offset",type=float,default=0)
    p.add_argument("--encoding",type=int,choices=range(8),default=3)
    p.add_argument("--chan-est",type=int,choices=range(4),default=0)
    p.add_argument("--pdu-length",type=int,default=500,help="Controllo originale senza generatore collegato")
    p.add_argument("--interval",type=int,default=1000,help="Controllo originale senza generatore collegato")
    p.add_argument("--amplitude",type=float,default=0.15)
    p.add_argument("--threshold",type=float,default=0.72)
    p.add_argument("--src",type=parse_mac,default=bytes.fromhex("04e548203bc8"))
    p.add_argument("--dst",type=parse_mac,default=b"\xff"*6)
    p.add_argument("--bssid",type=parse_mac,default=b"\xff"*6)
    p.add_argument("--tx-host",default="127.0.0.1")
    p.add_argument("--tx-port",type=int,default=52001)
    p.add_argument("--rx-host",default="127.0.0.1")
    p.add_argument("--rx-port",type=int,default=51001)
    p.add_argument("--rx-payload-only",action="store_true")
    p.add_argument("--pcap",default="ieee80211p_rx.pcap")
    p.add_argument("--block-size",type=int,default=65536)
    p.add_argument("--queue-blocks",type=int,default=32)
    args = p.parse_args()
    for name in ("rx_gain","tx_gain"):
        if not 0<=getattr(args,name)<=1:
            p.error("gain deve essere tra 0 e 1")
    if not 0<args.amplitude<=1 or not 0<args.threshold<1:
        p.error("amplitude in (0,1], threshold in (0,1)")
    if args.block_size<1024 or args.queue_blocks<1:
        p.error("block-size >=1024 e queue-blocks >=1")
    for port in (args.tx_port,args.rx_port):
        if not 1<=port<=65535:
            p.error("porta UDP deve essere 1..65535")
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if args.self_test:
        self_test()
    elif (args.gui or args.gui_demo) and not args.no_gui:
        from gui import launch
        launch(args,sys.modules[__name__])
    else:
        run(args)


if __name__ == "__main__":
    main()
