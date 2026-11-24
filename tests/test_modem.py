"""Regression tests: protocol, streaming, PCAP and UHD send boundaries."""
import ctypes as C
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ieee80211p_native as m
np = m.np


class ModemTests(unittest.TestCase):
    def test_all_rates_with_channel(self):
        m.self_test()

    def test_back_to_back_frames_and_cfo_both_signs(self):
        rng = np.random.default_rng(110)
        expected = [m.make_mpdu(rng.bytes(101),i) for i in range(3)]
        iq = np.r_[np.zeros(71),m.encode(expected[0],0),np.zeros(43),
                   m.encode(expected[1],7),np.zeros(512),m.encode(expected[2],3),np.zeros(800)]
        for cfo in (-95000,-31000,0,95000):
            channel = iq*np.exp(1j*(1.3+2*np.pi*cfo*np.arange(len(iq))/10e6))
            rx = m.Receiver()
            found = []
            pos = 0
            while pos<len(channel):
                n = int(rng.integers(1,2000))
                found.extend(rx.feed(channel[pos:pos+n],1700000000+pos/10e6))
                pos += n
            self.assertEqual([f.psdu for f in found],expected,f"CFO {cfo}")
            # Float epoch timestamps have ~238 ns resolution near this epoch.
            self.assertLess(abs(found[0].timestamp-(1700000000+71/10e6)),5/10e6)

    def test_invalid_crc_never_forwarded(self):
        psdu = bytearray(m.make_mpdu(b"bad-fcs"))
        psdu[-1] ^= 1
        good = m.make_mpdu(b"good")
        rx = m.Receiver()
        f = rx.feed(np.r_[m.encode(bytes(psdu)),np.zeros(1000),m.encode(good),np.zeros(800)],0)
        self.assertEqual([x.psdu for x in f],[good])
        self.assertGreater(rx.bad,0)

    def test_noise_and_discontinuity(self):
        rng = np.random.default_rng(100)
        rx = m.Receiver()
        for _ in range(8):
            noise=(rng.normal(size=5000)+1j*rng.normal(size=5000))*0.03
            self.assertEqual(rx.feed(noise),[])
            self.assertLessEqual(len(rx.buffer),192)
        psdu=m.make_mpdu(b"after reset")
        iq=m.encode(psdu)
        rx.feed(iq[:300])
        rx.reset()
        f=rx.feed(np.r_[iq[300:],np.zeros(1000),iq,np.zeros(800)])
        self.assertEqual([x.psdu for x in f],[psdu])

    def test_pcap_layout(self):
        frame=m.Frame(m.make_mpdu(b"pcap"),3,1700000000.123456,10,20)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"rx.pcap"
            writer=m.Pcap(p,5860e6,10e6)
            writer.write(frame)
            writer.close()
            raw=p.read_bytes()
        magic,major,minor,zone,sigfigs,snaplen,dlt=struct.unpack("<IHHIIII",raw[:24])
        self.assertEqual((magic,major,minor,dlt),(0xa1b2c3d4,2,4,127))
        sec,usec,incl,orig=struct.unpack("<IIII",raw[24:40])
        self.assertEqual((sec,usec),(1700000000,123456))
        self.assertEqual(incl,orig)
        self.assertEqual(incl,14+len(frame.psdu))
        version,pad,rtlen,present,flags,rate,mhz,ch=struct.unpack("<BBHIBBHH",raw[40:54])
        self.assertEqual((rtlen,present,flags,rate,mhz),(14,14,16,18,5860))
        self.assertTrue(ch&0x4000)
        self.assertEqual(raw[54:],frame.psdu)

    def test_mac_payload_and_sequence_wrap(self):
        frame=m.make_mpdu(b"payload",4097)
        self.assertTrue(m.check_fcs(frame))
        self.assertEqual(frame[22:24],b"\x10\x00")
        self.assertEqual(m.extract_payload(frame[:-4]),b"payload")
        with self.assertRaises(ValueError):
            m.make_mpdu(b"x"*1501)

    def test_pilot_polarity_reference(self):
        self.assertEqual(m.POLARITY[:32].tolist(),[
            1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,1,-1,1,1,-1,1,1,1,1,1,1,-1,1])

    def test_arbitrary_scrambler_seed(self):
        psdu=m.make_mpdu(b"scrambler")
        for seed in (1,2,17,63,93,127):
            f=m.Receiver().feed(np.r_[m.encode(psdu,7,seed),np.zeros(800)])
            self.assertEqual([x.psdu for x in f],[psdu])


class FakeAPI:
    def __init__(self,empty=False,error=0,short=False):
        self.sends=[]
        self.empty=empty
        self.error=error
        self.short=short
    def call(self,name,*args):
        if name=="tx_streamer_send":
            handle,ptrs,length,md,timeout,out=args
            n=0 if self.empty else min(length,23) if self.short else length
            C.cast(out,C.POINTER(C.c_size_t))[0]=n
            self.sends.append((length,C.cast(md,C.POINTER(C.c_void_p))[0]))
        elif name=="tx_streamer_recv_async_msg":
            C.cast(args[-1],C.POINTER(C.c_bool))[0]=True
        elif name=="async_metadata_event_code":
            C.cast(args[-1],C.POINTER(C.c_int))[0]=self.error or 1


class UHDTests(unittest.TestCase):
    def make_radio(self,**kw):
        r=object.__new__(m.Radio)
        r.api=FakeAPI(**kw)
        r.tx=C.c_void_p(10)
        r.asyncmd=C.c_void_p(11)
        r.txmd={(True,True):C.c_void_p(4)}
        return r

    def test_complete_burst_sob_and_eob_in_one_send(self):
        r=self.make_radio()
        r.transmit(np.ones(10,np.complex64))
        self.assertEqual(r.api.sends,[(1110,4)])

    def test_short_send_is_not_retried_as_an_intact_burst(self):
        r=self.make_radio(short=True)
        with self.assertRaisesRegex(RuntimeError,"short send"):
            r.transmit(np.ones(10,np.complex64))
        self.assertEqual(r.api.sends,[(1110,4)])

    def test_tx_zero_progress_stops(self):
        r=self.make_radio(empty=True)
        with self.assertRaisesRegex(RuntimeError,"timeout"):
            r.transmit(np.zeros(20,np.complex64))

    def test_tx_underflow_is_not_success(self):
        r=self.make_radio(error=2)
        with self.assertRaisesRegex(RuntimeError,"underflow"):
            r.transmit(np.zeros(20,np.complex64))


if __name__=="__main__":
    unittest.main()
