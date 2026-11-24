"""Compare the native transmitter to the user's independently compiled C++ codec.

The original DSP helpers are compiled with minimal standalone declarations;
GNU Radio is neither installed nor linked. Skipped if sources/compiler are absent.
"""
from pathlib import Path
import ctypes as C
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ieee80211p_native as m
np=m.np


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(__file__).resolve().parents[2]/"gr-ieee802-11/lib"
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler or not (source/"utils.cc").exists():
            raise unittest.SkipTest("C++ compiler or gr-ieee802-11 sources unavailable")
        cls.tmp=tempfile.TemporaryDirectory()
        tmp=Path(cls.tmp.name)
        header=(source/"utils.h").read_text()
        header=re.sub(r"#include <(?:gnuradio|ieee802_11)/[^>]+>","",header)
        enums="namespace gr { namespace ieee802_11 { enum Encoding { BPSK_1_2, BPSK_3_4, QPSK_1_2, QPSK_3_4, QAM16_1_2, QAM16_3_4, QAM64_2_3, QAM64_3_4 }; }}\n"
        (tmp/"utils.h").write_text(enums+header)
        cpp=(source/"utils.cc").read_text()
        cpp='\n'.join(line for line in cpp.splitlines() if not (line.startswith("#include") and "gnuradio" in line))
        cpp += '''
extern "C" int reference(const char* psdu, int size, int enc, int seed, unsigned char* out) {
    ofdm_param o((Encoding)enc);
    frame_param f(o,size);
    std::vector<char> bits(f.n_data_bits), scr(f.n_data_bits), conv(2*f.n_data_bits);
    std::vector<char> punct(f.n_encoded_bits), inter(f.n_encoded_bits), sym(48*f.n_sym);
    generate_bits(psdu,bits.data(),f);
    scramble(bits.data(),scr.data(),f,(char)seed);
    reset_tail_bits(scr.data(),f);
    convolutional_encoding(scr.data(),conv.data(),f);
    puncturing(conv.data(),punct.data(),f,o);
    interleave(punct.data(),inter.data(),f,o);
    split_symbols(inter.data(),sym.data(),f,o);
    std::memcpy(out,sym.data(),sym.size());
    return (int)sym.size();
}
'''
        (tmp/"ref.cc").write_text('#include <cmath>\n#include <vector>\n'+cpp)
        lib=tmp/("ref.dylib" if sys.platform == "darwin" else "ref.so")
        subprocess.run([compiler,"-std=c++14","-shared","-fPIC","-O2",str(tmp/"ref.cc"),"-o",str(lib)],check=True,capture_output=True)
        cls.lib=C.CDLL(str(lib))
        cls.lib.reference.argtypes=[C.c_char_p,C.c_int,C.c_int,C.c_int,C.c_void_p]
        cls.lib.reference.restype=C.c_int

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_coding_rates_against_cpp(self):
        rng=np.random.default_rng(1337)
        for enc in range(8):
            for length in (28,127,528,1528):
                with self.subTest(enc=enc,length=length):
                    psdu=rng.bytes(length)
                    bpsc,dbps,_,_=m.RATES[enc]
                    nsym=int(np.ceil((16+8*length+6)/dbps))
                    nbits=nsym*dbps
                    bits=np.zeros(nbits,np.uint8)
                    bits[16:16+8*length]=np.unpackbits(np.frombuffer(psdu,np.uint8),bitorder="little")
                    bits=m.scramble(bits,93)
                    bits[16+8*length:22+8*length]=0
                    symbols=m.coded_symbols(bits,enc).ravel()
                    points=m.CONST[bpsc][0]
                    actual=abs(symbols[:,None]-points).argmin(axis=1).astype(np.uint8)
                    expected=np.empty(48*nsym,np.uint8)
                    n=self.lib.reference(psdu,length,enc,93,expected.ctypes.data)
                    self.assertEqual(n,len(expected))
                    np.testing.assert_array_equal(actual,expected)


class ABITests(unittest.TestCase):
    def test_ctypes_structure_layout_matches_uhd_headers(self):
        inc = next((p for p in map(Path, ('/opt/homebrew/opt/uhd/include',
                    '/usr/local/opt/uhd/include', '/usr/local/include', '/usr/include'))
                    if (p/'uhd/usrp/usrp.h').exists()), None)
        compiler = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        if inc is None or not compiler:
            self.skipTest('UHD headers or C compiler unavailable')
        cpp='''
#include <uhd/usrp/usrp.h>
#include <stdio.h>
#include <stddef.h>
int main() {
printf("%zu %zu %zu %zu %zu %zu %zu %zu\\n",
sizeof(uhd_stream_args_t), sizeof(uhd_stream_cmd_t),
sizeof(uhd_tune_request_t),sizeof(uhd_tune_result_t),
offsetof(uhd_stream_args_t,n_channels),offsetof(uhd_stream_cmd_t,time_spec_full_secs),
offsetof(uhd_tune_request_t,rf_freq),offsetof(uhd_tune_request_t,args));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'abi.c'; exe=Path(tmp)/'abi'
            src.write_text(cpp)
            subprocess.run([compiler,'-std=c11','-I'+str(inc),str(src),'-o',str(exe)],check=True,capture_output=True)
            expected=[int(x) for x in subprocess.check_output([str(exe)]).split()]
        actual=[C.sizeof(m.StreamArgs),C.sizeof(m.StreamCmd),C.sizeof(m.TuneRequest),C.sizeof(m.TuneResult),
                m.StreamArgs.n.offset,m.StreamCmd.secs.offset,m.TuneRequest.rf.offset,m.TuneRequest.args.offset]
        self.assertEqual(actual,expected)


if __name__=='__main__':
    unittest.main()
