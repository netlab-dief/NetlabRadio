#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local GUI server: standard library only; no GNU Radio/Qt/web dependencies."""
from __future__ import annotations

import argparse
import collections
import copy
import http.server
import json
import logging
import math
from pathlib import Path
import queue
import secrets
import signal
import threading
import time
import webbrowser
from urllib.parse import urlsplit, parse_qs

ENCODINGS = ['BPSK 1/2','BPSK 3/4','QPSK 1/2','QPSK 3/4',
             '16QAM 1/2','16QAM 3/4','64QAM 2/3','64QAM 3/4']
EQUALIZERS = ['LS','LMS','Linear Comb','STA']
CHANNELS = [(2412000000.0, '  1 | 2412.0 | 11g'), (2417000000.0, '  2 | 2417.0 | 11g'), (2422000000.0, '  3 | 2422.0 | 11g'), (2427000000.0, '  4 | 2427.0 | 11g'), (2432000000.0, '  5 | 2432.0 | 11g'), (2437000000.0, '  6 | 2437.0 | 11g'), (2442000000.0, '  7 | 2442.0 | 11g'), (2447000000.0, '  8 | 2447.0 | 11g'), (2452000000.0, '  9 | 2452.0 | 11g'), (2457000000.0, ' 10 | 2457.0 | 11g'), (2462000000.0, ' 11 | 2462.0 | 11g'), (2467000000.0, ' 12 | 2467.0 | 11g'), (2472000000.0, ' 13 | 2472.0 | 11g'), (2484000000.0, ' 14 | 2484.0 | 11g'), (5170000000.0, ' 34 | 5170.0 | 11a'), (5180000000.0, ' 36 | 5180.0 | 11a'), (5190000000.0, ' 38 | 5190.0 | 11a'), (5200000000.0, ' 40 | 5200.0 | 11a'), (5210000000.0, ' 42 | 5210.0 | 11a'), (5220000000.0, ' 44 | 5220.0 | 11a'), (5230000000.0, ' 46 | 5230.0 | 11a'), (5240000000.0, ' 48 | 5240.0 | 11a'), (5250000000.0, ' 50 | 5250.0 | 11a'), (5260000000.0, ' 52 | 5260.0 | 11a'), (5270000000.0, ' 54 | 5270.0 | 11a'), (5280000000.0, ' 56 | 5280.0 | 11a'), (5290000000.0, ' 58 | 5290.0 | 11a'), (5300000000.0, ' 60 | 5300.0 | 11a'), (5310000000.0, ' 62 | 5310.0 | 11a'), (5320000000.0, ' 64 | 5320.0 | 11a'), (5500000000.0, '100 | 5500.0 | 11a'), (5510000000.0, '102 | 5510.0 | 11a'), (5520000000.0, '104 | 5520.0 | 11a'), (5530000000.0, '106 | 5530.0 | 11a'), (5540000000.0, '108 | 5540.0 | 11a'), (5550000000.0, '110 | 5550.0 | 11a'), (5560000000.0, '112 | 5560.0 | 11a'), (5570000000.0, '114 | 5570.0 | 11a'), (5580000000.0, '116 | 5580.0 | 11a'), (5590000000.0, '118 | 5590.0 | 11a'), (5600000000.0, '120 | 5600.0 | 11a'), (5610000000.0, '122 | 5610.0 | 11a'), (5620000000.0, '124 | 5620.0 | 11a'), (5630000000.0, '126 | 5630.0 | 11a'), (5640000000.0, '128 | 5640.0 | 11a'), (5660000000.0, '132 | 5660.0 | 11a'), (5670000000.0, '134 | 5670.0 | 11a'), (5680000000.0, '136 | 5680.0 | 11a'), (5690000000.0, '138 | 5690.0 | 11a'), (5700000000.0, '140 | 5700.0 | 11a'), (5710000000.0, '142 | 5710.0 | 11a'), (5720000000.0, '144 | 5720.0 | 11a'), (5745000000.0, '149 | 5745.0 | 11a (SRD)'), (5755000000.0, '151 | 5755.0 | 11a (SRD)'), (5765000000.0, '153 | 5765.0 | 11a (SRD)'), (5775000000.0, '155 | 5775.0 | 11a (SRD)'), (5785000000.0, '157 | 5785.0 | 11a (SRD)'), (5795000000.0, '159 | 5795.0 | 11a (SRD)'), (5805000000.0, '161 | 5805.0 | 11a (SRD)'), (5825000000.0, '165 | 5825.0 | 11a (SRD)'), (5860000000.0, '172 | 5860.0 | 11p'), (5870000000.0, '174 | 5870.0 | 11p'), (5880000000.0, '176 | 5880.0 | 11p'), (5890000000.0, '178 | 5890.0 | 11p'), (5900000000.0, '180 | 5900.0 | 11p'), (5910000000.0, '182 | 5910.0 | 11p'), (5920000000.0, '184 | 5920.0 | 11p')]
FIELDS = {'tx_gain','sample_rate','lo_offset','rx_gain','freq','encoding','chan_est','pdu_length','interval'}


class SignalMonitor:
    """Raw complex sample levels; reference |IQ|=1, never calibrated dBm.

    Integrates every delivered block into a 100 ms display interval. The scope
    retains the block with the largest peak, reduced to min/max bins so narrow
    pulses survive display decimation. Gaps reset the integration window.
    """
    def __init__(self,np):
        self.np=np
        self.version=0
        self.reset()

    def reset(self):
        self.history=collections.deque(maxlen=150)
        self.count=0
        self.power=0.
        self.peak=0.
        self.component_peak=0.
        self.wave=[]
        self.last_emit=None
        self.origin=None
        self.latest=None
        self.spectrum_peak=None
        self.spectrum_key=None
        self.waterfall_rows=collections.deque(maxlen=300)
        self.waterfall_seq=getattr(self,'waterfall_seq',0)
        self.waterfall_epoch=getattr(self,'waterfall_epoch',0)+1
        self.version+=1

    def spectrum(self,iq,sample_rate,center_freq):
        """Averaged two-sided complex FFT, Hann, coherent amplitude normalization.

        An on-bin unit complex tone has a 0 dBFS/bin peak. This is not dBm or
        a power spectral density per Hz. The LO offset is already removed by UHD.
        """
        np=self.np
        nfft=min(2048,1<<(len(iq).bit_length()-1))
        nframes=len(iq)//nfft
        window=np.hanning(nfft) if nfft>=4 else np.ones(nfft)
        frames=np.asarray(iq[:nframes*nfft]).reshape(nframes,nfft)
        transformed=np.fft.fftshift(np.fft.fft(frames*window,axis=1),axes=1)
        power=np.mean(np.abs(transformed)**2,axis=0)/(window.sum()**2)
        db=10*np.log10(np.maximum(power,1e-16))
        key=(center_freq,sample_rate,nfft)
        if self.spectrum_key!=key:
            self.spectrum_peak=db.copy()
            self.spectrum_key=key
            self.waterfall_rows.clear()
            self.waterfall_epoch+=1
        else:
            self.spectrum_peak=np.maximum(self.spectrum_peak,db)
        return {'center_hz':center_freq,'sample_rate_hz':sample_rate,'nfft':nfft,
                'bin_hz':sample_rate/nfft,'enbw_hz':sample_rate*float(np.sum(window**2)/window.sum()**2),
                'offset_hz':np.fft.fftshift(np.fft.fftfreq(nfft,1/sample_rate)).tolist(),
                'dbfs_bin':db.tolist(),'peak_hold_dbfs_bin':self.spectrum_peak.tolist()}

    def record_waterfall(self,spectrum,now):
        np=self.np
        db=np.asarray(spectrum['dbfs_bin'])
        group=max(1,len(db)//512)
        # Peak pooling keeps a narrow carrier visible when reducing FFT columns.
        reduced=np.maximum.reduceat(db,np.arange(0,len(db),group))
        codes=np.rint((np.clip(reduced,-160,20)+160)*2).astype(np.uint16)
        self.waterfall_seq+=1
        self.waterfall_rows.append({'seq':self.waterfall_seq,'time_s':now,'levels':codes.tolist()})
        while self.waterfall_rows and self.waterfall_rows[0]['time_s']<now-30:
            self.waterfall_rows.popleft()

    def waterfall_snapshot(self,after=0):
        key=self.spectrum_key
        return {'epoch':self.waterfall_epoch,'latest_seq':self.waterfall_seq,
                'center_hz':None if key is None else key[0],
                'sample_rate_hz':None if key is None else key[1],
                'offset_db':-160.,'step_db':.5,
                'rows':[dict(row) for row in self.waterfall_rows if row['seq']>after]}

    def feed(self,iq,sample_rate,timestamp=None,gap=False,now=None,center_freq=0):
        if gap:self.reset()
        np=self.np
        if not len(iq):return
        now=time.monotonic() if now is None else now
        a=np.abs(iq)
        power=float(np.sum(np.square(a,dtype=np.float64),dtype=np.float64))
        peak=float(a.max())
        component_peak=max(float(np.max(np.abs(iq.real))),float(np.max(np.abs(iq.imag))))
        if not all(math.isfinite(x) for x in (power,peak,component_peak)):
            return
        self.power+=power
        self.count+=len(iq)
        self.component_peak=max(self.component_peak,component_peak)
        if peak>=self.peak or not self.wave:
            self.peak=peak
            edges=np.linspace(0,len(iq),min(256,len(iq))+1,dtype=int)
            low=np.minimum.reduceat(a,edges[:-1])
            high=np.maximum.reduceat(a,edges[:-1])
            us=(edges[:-1]+edges[1:]-1)*(.5e6/sample_rate)
            self.wave=np.c_[us,low,high].tolist()
            self.wave_timestamp=timestamp
            self.wave_duration_us=len(iq)/sample_rate*1e6
            self.fft_iq=np.asarray(iq,dtype=np.complex64).copy()
        if self.last_emit is not None and now-self.last_emit<.1:return
        if self.origin is None:self.origin=now
        rms=math.sqrt(self.power/self.count)
        db=lambda value:max(-160.,20*math.log10(max(value,1e-8)))
        self.history.append((now,db(rms),db(self.peak)))
        while self.history and self.history[0][0]<now-10:self.history.popleft()
        self.latest={'rms':rms,'peak':self.peak,'rms_dbfs':db(rms),'peak_dbfs':db(self.peak),
                     'near_full_scale':self.component_peak>=.999,
                     'component_peak':self.component_peak,'samples':self.count,
                     'waveform':self.wave,'duration_us':self.wave_duration_us,
                     'spectrum':self.spectrum(self.fft_iq,sample_rate,center_freq),
                     'timestamp':self.wave_timestamp,'updated_monotonic':now}
        self.record_waterfall(self.latest['spectrum'],now)
        self.count=0
        self.power=self.peak=self.component_peak=0.
        self.wave=[]
        self.last_emit=now
        self.version+=1

    def snapshot(self,now=None):
        now=time.monotonic() if now is None else now
        latest=dict(self.latest) if self.latest is not None else None
        if latest is not None:
            latest['age_s']=max(0.,now-latest.pop('updated_monotonic'))
        sweep=0 if self.origin is None else math.floor((self.last_emit-self.origin)/10)
        start=0 if self.origin is None else self.origin+10*sweep
        return {'version':self.version,'latest':latest,
                'fixed_history':[[t-start,rms,peak] for t,rms,peak in self.history if start<=t<start+10],
                'sweep':sweep,
                'history':[[t-now,rms,peak] for t,rms,peak in self.history]}


def validated(settings):
    if not isinstance(settings,dict) or set(settings)-FIELDS:
        raise ValueError('Controlli sconosciuti')
    result = {}
    choices = {'sample_rate':(5e6,10e6,20e6),'lo_offset':(0,6e6,11e6),
               'freq':tuple(v for v,_ in CHANNELS),'encoding':tuple(range(8)),
               'chan_est':tuple(range(4))}
    ranges = {'tx_gain':(0,1),'rx_gain':(0,1),'pdu_length':(0,1500),'interval':(100,10000)}
    for name,value in settings.items():
        if isinstance(value,bool) or not isinstance(value,(int,float)):
            raise ValueError(f'Valore non numerico per {name}')
        if name in choices and value not in choices[name]:
            raise ValueError(f'Valore non ammesso per {name}')
        if name in ranges and not ranges[name][0]<=value<=ranges[name][1]:
            raise ValueError(f'{name} fuori intervallo')
        if name in ('encoding','chan_est','pdu_length','interval'):
            if int(value)!=value:
                raise ValueError(f'{name} deve essere intero')
            value=int(value)
        result[name]=value
    return result


class Control(logging.Handler):
    def __init__(self,args,modem):
        super().__init__()
        self.args=copy.copy(args)
        self.modem=modem
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.commands=queue.Queue()
        self.worker=None
        self.config={k:getattr(args,k) for k in FIELDS}
        self.actual=dict(self.config)
        self.points=modem.np.empty(0,complex)
        self.logs=collections.deque(maxlen=35)
        self.running=False
        self.is_ready=False
        self.status='Fermo'
        self.error=''
        self.counters={k:0 for k in ('rx','tx','overflow','dropped','crc_or_sync')}
        self.queue_depth=0
        self.last_frame={}
        self.version=0
        self.demo=bool(args.gui_demo)
        self.signal_monitor=SignalMonitor(modem.np)

    def emit(self,record):
        with self.lock:
            self.logs.append(self.format(record))

    def symbols(self,points):
        with self.lock:
            self.points=self.modem.np.r_[self.points,points][-480:]
            self.version+=1

    def samples(self,iq,sample_rate,timestamp=None,gap=False,center_freq=None):
        with self.lock:
            self.signal_monitor.feed(iq,sample_rate,timestamp,gap,
                                     center_freq=self.actual['freq'] if center_freq is None else center_freq)

    def waterfall(self,after=0):
        with self.lock:
            return self.signal_monitor.waterfall_snapshot(after)

    def frame(self,frame):
        with self.lock:
            self.last_frame={'bytes':len(frame.psdu)-4,'encoding':frame.encoding,
                             'cfo_hz':round(frame.cfo_hz),'snr_db':round(frame.snr_db,1)}

    def stats(self,counters,depth):
        with self.lock:
            self.counters=dict(counters)
            self.queue_depth=depth

    def ready(self):
        with self.lock:
            self.is_ready=True
            self.status='SIMULAZIONE — nessuna radio' if self.demo else 'Radio attiva'

    def applied(self,settings,reset=False):
        with self.lock:
            self.actual.update(settings)
            if reset:
                self.points=self.modem.np.empty(0,complex)
                self.version+=1
                self.signal_monitor.reset()

    def configure(self,settings):
        values=validated(settings)
        with self.lock:
            self.config.update(values)
            if self.running:
                # Full snapshots preserve simultaneous edits from distinct widgets.
                while not self.commands.empty():
                    try:self.commands.get_nowait()
                    except queue.Empty:break
                self.commands.put(dict(self.config))
            else:
                self.actual=dict(self.config)

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.stop_event.clear()
            self.error=''
            self.status='Avvio simulazione…' if self.demo else 'Apertura B200…'
            self.running=True
            self.is_ready=False
            self.points=self.modem.np.empty(0,complex)
            self.version+=1
            self.signal_monitor.reset()
            args=copy.copy(self.args)
            for k,v in self.config.items():setattr(args,k,v)
            self.actual=dict(self.config)
            while not self.commands.empty():
                try:self.commands.get_nowait()
                except queue.Empty:break
            self.worker=threading.Thread(target=self._run,args=(args,),daemon=True,name='modem-controller')
            self.worker.start()

    def _run(self,args):
        try:
            if self.demo:
                self._demo(args)
            else:
                self.modem.run(args,self)
        except Exception as exc:
            with self.lock:
                self.error=str(exc.__cause__ or exc)
                self.status='Errore — radio ferma'
            logging.getLogger('80211p').exception('Modem arrestato')
        finally:
            with self.lock:
                self.running=False
                self.is_ready=False
                if not self.error:self.status='Fermo'

    def _demo(self,args):
        """Explicit RF-free loopback; never used as a fallback for hardware errors."""
        m=self.modem
        rng=m.np.random.default_rng(80211)
        counters=dict(self.counters)
        seq=0
        self.ready()
        while not self.stop_event.is_set():
            try:
                settings=self.commands.get_nowait()
            except queue.Empty:settings=None
            if settings:
                for k,v in settings.items():setattr(args,k,v)
                self.applied(settings,True)
            psdu=m.make_mpdu(rng.bytes(500),seq,args.src,args.dst,args.bssid)
            iq=m.encode(psdu,args.encoding,seq%127+1,args.amplitude)
            iq=m.np.r_[m.np.zeros(200),iq,m.np.zeros(800)]
            iq=iq*m.np.exp(1j*(0.4+2*m.np.pi*22000*m.np.arange(len(iq))/args.sample_rate))
            iq+=(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))*0.0015
            self.samples(iq,args.sample_rate,time.time(),center_freq=args.freq)
            rx=m.Receiver(args.sample_rate,args.threshold,args.chan_est,self.symbols)
            found=rx.feed(iq,time.time())
            for frame in found:
                self.frame(frame)
                counters['rx']+=1
            counters['tx']+=1
            counters['crc_or_sync']+=rx.bad
            self.stats(counters,0)
            seq+=1
            self.stop_event.wait(0.3)

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.running:self.status='Arresto…'

    def close(self):
        self.stop()
        if self.worker:self.worker.join(timeout=12)

    def snapshot(self):
        with self.lock:
            return {'config':dict(self.config),'applied':dict(self.actual),
                    'points':self.modem.np.c_[self.points.real,self.points.imag].tolist(),
                    'version':self.version,'running':self.running,'ready':self.is_ready,
                    'signal':self.signal_monitor.snapshot(),
                    'status':self.status,'error':self.error,'demo':self.demo,
                    'counters':dict(self.counters),'queue_depth':self.queue_depth,
                    'frame':dict(self.last_frame),'logs':list(self.logs),
                    'udp_tx':f'{self.args.tx_host}:{self.args.tx_port}',
                    'udp_rx':f'{self.args.rx_host}:{self.args.rx_port}',
                    'pcap':self.args.pcap}


def make_server(control,port=0):
    token=secrets.token_urlsafe(24)
    template=(Path(__file__).with_name('gui.html')).read_text()
    metadata={'token':token,'channels':CHANNELS,'encodings':ENCODINGS,'equalizers':EQUALIZERS}
    html=template.replace('__GUI_METADATA__',json.dumps(metadata)).encode()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*_):pass

        def reply(self,status,data,content_type='application/json'):
            payload=data if isinstance(data,bytes) else json.dumps(data,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def authorized_host(self):
            return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.authorized_host():return self.reply(403,{'error':'Host non ammesso'})
            if self.path=='/':return self.reply(200,html,'text/html; charset=utf-8')
            if self.path=='/api/state':return self.reply(200,control.snapshot())
            if urlsplit(self.path).path=='/api/waterfall':
                try:
                    after=int(parse_qs(urlsplit(self.path).query).get('after',['0'])[0])
                    if not 0<=after<2**63:raise ValueError('Cursore non valido')
                    return self.reply(200,control.waterfall(after))
                except ValueError as exc:return self.reply(400,{'error':str(exc)})
            self.reply(404,{'error':'Non trovato'})

        def do_POST(self):
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if (not self.authorized_host() or self.headers.get('X-Control-Token')!=token or
                self.headers.get('Origin',expected)!=expected):
                return self.reply(403,{'error':'Richiesta non autorizzata'})
            if self.path!='/api/action':return self.reply(404,{'error':'Non trovato'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:raise ValueError('Richiesta troppo grande o vuota')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Oggetto JSON atteso')
                action=body.get('action')
                if action=='configure':control.configure(body.get('settings'))
                elif action=='start':control.start()
                elif action=='stop':control.stop()
                else:raise ValueError('Azione sconosciuta')
                self.reply(200,{'ok':True})
            except (ValueError,TypeError) as exc:
                self.reply(400,{'error':str(exc)})
    server=http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server


def launch(args,modem):
    control=Control(args,modem)
    control.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s','%H:%M:%S'))
    modem.LOG.addHandler(control)
    server=make_server(control,args.gui_port)
    url=f'http://127.0.0.1:{server.server_port}/'
    print(f'GUI NetlabRadio: {url}',flush=True)
    print('Ctrl-C nel terminale chiude GUI server e radio.',flush=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True,name='gui-http')
    thread.start()
    done=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:done.set())
    try:
        if not args.no_browser:webbrowser.open(url)
        control.start()
        while not done.wait(0.5):pass
    finally:
        control.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        modem.LOG.removeHandler(control)
