import argparse
import ast
import json
from pathlib import Path
import re
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ieee80211p_native as m
import gui


def args():
    return argparse.Namespace(tx_gain=1.,rx_gain=.75,sample_rate=10e6,lo_offset=0,
                              freq=5860e6,encoding=3,chan_est=0,pdu_length=500,interval=1000,
                              gui_demo=True,tx_host='127.0.0.1',tx_port=52001,
                              rx_host='127.0.0.1',rx_port=51001,pcap='unused.pcap',
                              src=bytes.fromhex('04e548203bc8'),dst=b'\xff'*6,bssid=b'\xff'*6,
                              amplitude=.15,threshold=.72)


class SignalTests(unittest.TestCase):
    def test_waterfall_retains_tone_frequency_with_quantized_levels(self):
        mon=gui.SignalMonitor(m.np)
        tone=.5*m.np.exp(2j*m.np.pi*137*m.np.arange(4096)/2048)
        mon.feed(tone,10e6,center_freq=5860e6,now=0)
        w=mon.waterfall_snapshot()
        self.assertEqual(w['center_hz'],5860e6)
        self.assertEqual(len(w['rows']),1)
        levels=w['rows'][0]['levels']
        self.assertEqual(len(levels),512)
        self.assertEqual(int(m.np.argmax(levels)),(1024+137)//4)
        self.assertAlmostEqual(w['offset_db']+w['step_db']*max(levels),-6.,places=5)

    def test_waterfall_incremental_cursor_and_retune_epoch(self):
        mon=gui.SignalMonitor(m.np)
        tone=m.np.ones(2048,complex)
        mon.feed(tone,10e6,center_freq=5860e6,now=0)
        first=mon.waterfall_snapshot()
        cursor=first['latest_seq']
        self.assertEqual(mon.waterfall_snapshot(cursor)['rows'],[])
        mon.feed(tone*.1,10e6,center_freq=5860e6,now=.11)
        w=mon.waterfall_snapshot(cursor)
        self.assertEqual(len(w['rows']),1)
        self.assertGreater(w['rows'][0]['seq'],cursor)
        mon.feed(tone,10e6,center_freq=5890e6,now=.22)
        w=mon.waterfall_snapshot(cursor)
        self.assertNotEqual(w['epoch'],first['epoch'])
        self.assertEqual(w['center_hz'],5890e6)
        self.assertEqual(len(w['rows']),1)

    def test_waterfall_history_is_bounded_and_reset_clears_it(self):
        mon=gui.SignalMonitor(m.np)
        tone=m.np.ones(64,complex)
        for i in range(360):mon.feed(tone,10e6,now=i*.11)
        w=mon.waterfall_snapshot()
        self.assertLessEqual(len(w['rows']),300)
        self.assertTrue(all(w['rows'][-1]['time_s']-r['time_s']<=30 for r in w['rows']))
        seq=w['latest_seq']
        mon.reset()
        self.assertEqual(mon.waterfall_snapshot()['rows'],[])
        mon.feed(tone,10e6,now=50)
        self.assertGreater(mon.waterfall_snapshot()['latest_seq'],seq)

    def test_spectrum_tone_frequency_and_level(self):
        mon=gui.SignalMonitor(m.np)
        n=m.np.arange(4096)
        tone=.5*m.np.exp(2j*m.np.pi*137*n/2048)
        mon.feed(tone,10e6,center_freq=5890e6,now=0)
        s=mon.snapshot(now=0)['latest']['spectrum']
        i=int(m.np.argmax(s['dbfs_bin']))
        self.assertEqual(s['nfft'],2048)
        self.assertAlmostEqual(s['offset_hz'][i],137*10e6/2048)
        self.assertAlmostEqual(s['dbfs_bin'][i],-6.0206,places=4)
        self.assertEqual(s['center_hz'],5890e6)
        self.assertAlmostEqual(s['bin_hz'],10e6/2048)
        self.assertAlmostEqual(s['enbw_hz']/s['bin_hz'],1.5,places=2)

    def test_spectrum_keeps_negative_offset_on_lower_rf_side(self):
        mon=gui.SignalMonitor(m.np)
        tone=m.np.exp(-2j*m.np.pi*64*m.np.arange(4096)/2048)
        mon.feed(tone,5e6,center_freq=5860e6,now=0)
        s=mon.snapshot(now=0)['latest']['spectrum']
        offset=s['offset_hz'][int(m.np.argmax(s['dbfs_bin']))]
        self.assertEqual(offset,-64*5e6/2048)
        self.assertEqual(s['center_hz']+offset,5859843750.)
        self.assertEqual(s['offset_hz'][1024],0.)

    def test_spectrum_max_hold_resets_when_center_changes(self):
        mon=gui.SignalMonitor(m.np)
        tone=m.np.ones(2048,complex)
        mon.feed(tone,10e6,center_freq=5860e6,now=0)
        mon.feed(tone*.1,10e6,center_freq=5860e6,now=.11)
        s=mon.snapshot(now=.11)['latest']['spectrum']
        self.assertAlmostEqual(max(s['dbfs_bin']),-20.,places=5)
        self.assertAlmostEqual(max(s['peak_hold_dbfs_bin']),0.,places=5)
        mon.feed(tone*.1,10e6,center_freq=5890e6,now=.22)
        s=mon.snapshot(now=.22)['latest']['spectrum']
        self.assertAlmostEqual(max(s['peak_hold_dbfs_bin']),-20.,places=5)

    def test_spectrum_silence_serializes_without_infinity(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.zeros(4096,complex),10e6,center_freq=5860e6,now=0)
        s=mon.snapshot(now=0)
        self.assertTrue(all(x==-160 for x in s['latest']['spectrum']['dbfs_bin']))
        json.dumps(s,allow_nan=False)

    def test_known_complex_amplitude_and_dbfs(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.full(1000,.3+.4j),5e6,timestamp=123.,now=0)
        s=mon.snapshot(now=.1)['latest']
        self.assertAlmostEqual(s['rms'],.5)
        self.assertAlmostEqual(s['peak'],.5)
        self.assertAlmostEqual(s['rms_dbfs'],-6.020599913,places=6)
        self.assertAlmostEqual(s['peak_dbfs'],-6.020599913,places=6)
        self.assertEqual(s['duration_us'],200.)
        self.assertEqual(s['timestamp'],123.)
        self.assertAlmostEqual(s['age_s'],.1)

    def test_all_blocks_aggregated_and_brief_peak_preserved(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.zeros(10,complex),10e6,now=0)
        impulse=m.np.zeros(65536,complex)
        impulse[32815]=.9
        mon.feed(impulse,10e6,now=.01)
        mon.feed(m.np.zeros(65536,complex),10e6,now=.11)
        latest=mon.snapshot(now=.11)['latest']
        self.assertEqual(latest['samples'],131072)
        self.assertAlmostEqual(latest['rms'],.9/m.np.sqrt(131072))
        self.assertEqual(latest['peak'],.9)
        self.assertEqual(len(latest['waveform']),256)
        self.assertEqual(max(row[2] for row in latest['waveform']),.9)

    def test_full_scale_is_component_based_and_zero_is_finite(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.full(3,.8+.8j),10e6,now=0)
        self.assertFalse(mon.snapshot(now=0)['latest']['near_full_scale'])
        mon.feed(m.np.ones(3,complex),10e6,now=.11)
        self.assertTrue(mon.snapshot(now=.11)['latest']['near_full_scale'])
        mon.feed(m.np.zeros(3,complex),10e6,now=.22)
        latest=mon.snapshot(now=.22)['latest']
        self.assertEqual(latest['rms_dbfs'],-160.)
        self.assertEqual(latest['peak_dbfs'],-160.)
        json.dumps(mon.snapshot(now=.22),allow_nan=False)

    def test_gap_reset_and_invalid_samples(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.ones(30,complex),10e6,now=0)
        mon.feed(m.np.zeros(20,complex),10e6,gap=True,now=.01)
        s=mon.snapshot(now=.01)
        self.assertEqual(len(s['history']),1)
        self.assertEqual(s['latest']['peak'],0.)
        mon.feed(m.np.array([complex(float('nan'),0)]),10e6,now=.2)
        self.assertEqual(mon.snapshot(now=.2)['latest']['samples'],20)
        mon.reset()
        self.assertIsNone(mon.snapshot()['latest'])

    def test_history_bounded_to_ten_seconds(self):
        mon=gui.SignalMonitor(m.np)
        for i in range(200):mon.feed(m.np.ones(10,complex)*.1,10e6,now=i*.11)
        history=mon.snapshot(now=199*.11)['history']
        self.assertTrue(all(-10<=row[0]<=0 for row in history))
        self.assertLessEqual(len(history),150)

    def test_fixed_history_does_not_translate_as_time_passes(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.ones(10,complex)*.1,10e6,now=100)
        mon.feed(m.np.ones(10,complex)*.2,10e6,now=102)
        first=mon.snapshot(now=102)
        later=mon.snapshot(now=105)
        self.assertEqual(first['fixed_history'],later['fixed_history'])
        self.assertEqual([row[0] for row in later['fixed_history']],[0,2])
        self.assertNotEqual(first['history'],later['history'])

    def test_fixed_history_wraps_and_resets_with_acquisition(self):
        mon=gui.SignalMonitor(m.np)
        mon.feed(m.np.ones(10,complex)*.1,10e6,now=100)
        mon.feed(m.np.ones(10,complex)*.2,10e6,now=109)
        mon.feed(m.np.ones(10,complex)*.3,10e6,now=110.5)
        s=mon.snapshot(now=111)
        self.assertEqual(s['sweep'],1)
        self.assertEqual([row[0] for row in s['fixed_history']],[.5])
        mon.feed(m.np.ones(10,complex)*.4,10e6,gap=True,now=113)
        s=mon.snapshot(now=113)
        self.assertEqual(s['sweep'],0)
        self.assertEqual([row[0] for row in s['fixed_history']],[0])


class EqualizerTests(unittest.TestCase):
    def test_all_equalizers_all_encodings(self):
        rng=m.np.random.default_rng(400)
        for eq in range(4):
            for enc in range(8):
                with self.subTest(equalizer=eq,encoding=enc):
                    psdu=m.make_mpdu(rng.bytes(120))
                    iq=m.encode(psdu,enc)
                    iq=m.np.convolve(iq,[1,0,0,.06+.03j])
                    iq=m.np.r_[m.np.zeros(211),iq,m.np.zeros(800)]
                    iq=iq*m.np.exp(1j*(.8+2*m.np.pi*15000*m.np.arange(len(iq))/10e6))
                    points=[]
                    rx=m.Receiver(chan_est=eq,on_symbols=lambda x:points.extend(x))
                    frames=rx.feed(iq,0)
                    self.assertEqual([f.psdu for f in frames],[psdu])
                    self.assertTrue(points)
                    self.assertEqual(len(points)%48,0)

    def test_constellation_before_bad_fcs(self):
        psdu=m.make_mpdu(b'bad-fcs')
        psdu=psdu[:-1]+bytes([psdu[-1]^1])
        points=[]
        rx=m.Receiver(on_symbols=lambda x:points.extend(x))
        frames=rx.feed(m.np.r_[m.encode(psdu),m.np.zeros(800)])
        self.assertEqual(frames,[])
        self.assertTrue(points,'The original carrier tap precedes the CRC checker')


class ControlTests(unittest.TestCase):
    def test_configuration_is_atomic_and_strict(self):
        c=gui.Control(args(),m)
        before=c.snapshot()['config']
        with self.assertRaises(ValueError):c.configure({'rx_gain':.4,'encoding':100})
        self.assertEqual(c.snapshot()['config'],before)
        for bad in ({'rx_gain':float('nan')},{'interval':99},{'tx_gain':True},{'pdu_length':4.5},{'unknown':1}):
            with self.assertRaises(ValueError):c.configure(bad)

    def test_480_latest_carrier_samples(self):
        c=gui.Control(args(),m)
        c.symbols(m.np.arange(400)+1j)
        c.symbols(m.np.arange(400,600)+1j)
        points=c.snapshot()['points']
        self.assertEqual(len(points),480)
        self.assertEqual(points[0],[120.,1.])
        self.assertEqual(points[-1],[599.,1.])

    def test_all_original_channel_labels_retained(self):
        source=Path(__file__).resolve().parents[2]/'SDR/ieee80211p.py'
        if not source.exists():self.skipTest('Original flowgraph unavailable')
        found={}
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Attribute):
                name=node.targets[0].attr
                if name in ('_freq_options','_freq_labels'):found[name]=ast.literal_eval(node.value)
        self.assertEqual(gui.CHANNELS,list(zip(found['_freq_options'],found['_freq_labels'])))

    def test_queued_edits_keep_other_values(self):
        c=gui.Control(args(),m)
        c.running=True
        c.configure({'rx_gain':.3})
        c.configure({'encoding':7})
        self.assertEqual(c.commands.qsize(),1)
        values=c.commands.get_nowait()
        self.assertEqual((values['rx_gain'],values['encoding']),(.3,7))

    def test_demo_start_stop(self):
        c=gui.Control(args(),m)
        c.start()
        try:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and not c.snapshot()['points']:time.sleep(.01)
            s=c.snapshot()
            self.assertTrue(s['demo'])
            self.assertTrue(s['points'])
            self.assertTrue(s['ready'])
        finally:c.close()
        self.assertFalse(c.worker.is_alive())
        self.assertFalse(c.snapshot()['running'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.control=gui.Control(args(),m)
        self.server=gui.make_server(self.control)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        with urllib.request.urlopen(self.url) as r:html=r.read().decode()
        self.token=re.search(r'"token": "([^"]+)"',html)[1]

    def tearDown(self):
        self.control.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self,body,token=None):
        request=urllib.request.Request(self.url+'/api/action',json.dumps(body).encode(),
                                       {'Content-Type':'application/json','X-Control-Token':token or self.token})
        with urllib.request.urlopen(request) as r:return json.load(r)

    def test_settings_reach_controller(self):
        self.assertTrue(self.post({'action':'configure','settings':{'encoding':7,'chan_est':3}})['ok'])
        with urllib.request.urlopen(self.url+'/api/state') as r:s=json.load(r)
        self.assertEqual((s['config']['encoding'],s['config']['chan_est']),(7,3))

    def test_waterfall_endpoint_only_returns_new_rows(self):
        self.control.samples(m.np.ones(2048,complex)*.1,10e6)
        with urllib.request.urlopen(self.url+'/api/waterfall?after=0') as r:w=json.load(r)
        self.assertEqual(len(w['rows']),1)
        with urllib.request.urlopen(self.url+f"/api/waterfall?after={w['latest_seq']}") as r:next_w=json.load(r)
        self.assertEqual(next_w['rows'],[])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url+'/api/waterfall?after=-1')
        self.assertEqual(ctx.exception.code,400)

    def test_invalid_and_cross_origin_actions_rejected(self):
        for body,token,expected in [({'action':'configure','settings':{'encoding':99}},None,400),
                                    ({'action':'start'},'wrong-token',403)]:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.post(body,token)
            self.assertEqual(ctx.exception.code,expected)


class RuntimeTests(unittest.TestCase):
    def test_live_reconfigure_at_receive_boundary(self):
        instances=[]
        class FakeRadio:
            def __init__(self,a):
                self.rx=True
                self.tx=None
                self.in_receive=False
                self.changes=[]
                self.closed=False
                self.commands=[]
                instances.append(self)
            def command(self,start):self.commands.append(start)
            def receive(self,n):
                self.in_receive=True
                try:
                    time.sleep(.005)
                    return m.np.zeros(2048,m.np.complex64),time.time(),0
                finally:self.in_receive=False
            def reconfigure(self,a,changed):
                if self.in_receive:raise AssertionError('Concurrent reconfiguration during receive')
                self.changes.append(set(changed))
            def close(self):self.closed=True
        with tempfile.TemporaryDirectory() as tmp:
            a=args()
            a.gui_demo=False
            a.mode='rx'
            a.pcap=str(Path(tmp)/'rx.pcap')
            a.queue_blocks=4
            a.block_size=2048
            a.rx_payload_only=False
            c=gui.Control(a,m)
            with patch.object(m,'Radio',FakeRadio):
                c.start()
                try:
                    deadline=time.monotonic()+3
                    while not c.snapshot()['ready'] and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(c.snapshot()['ready'])
                    deadline=time.monotonic()+1
                    while c.snapshot()['signal']['latest'] is None and time.monotonic()<deadline:time.sleep(.01)
                    s=c.snapshot()
                    self.assertIsNotNone(s['signal']['latest'])
                    self.assertEqual(s['points'],[])
                    self.assertEqual(s['counters']['rx'],0)
                    c.configure({'sample_rate':5e6,'freq':5890e6,'chan_est':3,'rx_gain':.4})
                    deadline=time.monotonic()+3
                    while c.snapshot()['applied']['sample_rate']!=5e6 and time.monotonic()<deadline:time.sleep(.01)
                    s=c.snapshot()
                    self.assertEqual(s['applied']['sample_rate'],5e6)
                    self.assertEqual(s['applied']['chan_est'],3)
                    self.assertFalse(s['error'])
                    self.assertTrue(instances[0].changes)
                finally:c.close()
        self.assertTrue(instances[0].closed)
        self.assertFalse(c.worker.is_alive())


if __name__=='__main__':unittest.main()
