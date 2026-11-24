# IEEE 802.11p su B200, senza GNU Radio

Implementazione sperimentale autonoma in Python, NumPy e libuhd, con GUI locale
nel browser. Nessun import di GNU Radio, gr-foo, gr-ieee802-11, Gqrx o gr-fosphor. Numba è opzionale per
accelerare Viterbi; il lanciatore tenta di installarlo una volta.

## Avvio

UHD è già presente sul Mac su cui è stato preparato il progetto. Su un altro Mac:

```sh
brew install uhd python@3.13
```

Dalla cartella di questo file:

```sh
./avvia.command --self-test
./avvia.command --mode trx --freq 5860000000
```

Il lanciatore crea un ambiente Python locale e installa NumPy al primo avvio
(serve Internet soltanto per l'installazione). I successivi avvii riutilizzano
l'ambiente. Un doppio clic su `avvia.command` apre la GUI nel browser predefinito
e avvia RX e TX con i valori predefiniti. Se la B200 non è disponibile, la GUI
rimane aperta e mostra l'errore; non sostituisce i dati radio con una simulazione.
Se non trova le immagini UHD standard, scarica quelle B200 nella cartella
`uhd-images` del programma, usando il downloader UHD installato (e `requests`).
Il test software e `--help` non scaricano immagini e non aprono la radio.
La prima esecuzione con Numba può impiegare qualche secondo per compilare il decoder.
Non avviare contemporaneamente Gqrx o altri programmi che aprono la stessa B200.

## GUI: controlli del flowgraph originale

La finestra `Ieee80211P` è ricostruita nel browser locale; non è la stessa
finestra Qt e non ne riproduce pixel per pixel tema, menu contestuali o bordi.
I dati, le scelte dei controlli, il loro ordine e il grafico principale
corrispondono a quelli presenti in `SDR/ieee80211p.py`:

| Elemento | Riproduzione |
|---|---|
| `tx_gain` | Slider e valore numerico 0..1, passo 0.01, default 1 |
| `samp_rate` | 5 / 10 / 20 MHz, default 10 |
| `lo_offset` | 0 / 6000000 / 11000000 Hz |
| `gain` RX | Slider e valore numerico 0..1, passo 0.01, default 0.75 |
| `freq` | Stessi 67 valori e stesse etichette canale/frequenza/11a-g-p |
| `encoding` | Stessi 8 radio button BPSK, QPSK, 16QAM e 64QAM |
| `chan_est` | LS / LMS / Linear Comb / STA, effettivamente collegati al decoder |
| Costellazione | Ultimi 480 campioni sulle 48 sottoportanti dati, blu, assi I/Q −2..+2, refresh 100 ms |
| `pdu_length` | Slider 0..1500, default 500; nessun generatore collegato, come nel flowgraph |
| `interval` | Slider 100..10000, default 1000; nessun generatore collegato, come nel flowgraph |

Il grafico usa i simboli equalizzati prima del controllo FCS, nello stesso
punto funzionale del collegamento `carrier` originale; quindi può mostrare
costellazioni di frame il cui CRC non viene poi accettato. Non mostra l'IQ grezzo
e non genera punti artificiali quando la radio non riceve pacchetti.
La griglia e l'autoscale sono inizialmente disattivati, come nel flowgraph;
si possono attivare dai controlli sotto il grafico. `Pausa grafico` congela
soltanto la visualizzazione, lasciando attiva la radio.

I cambi di parametri vengono applicati al modem: frequenza, LO, sample rate ed
equalizzatore ripartono da una nuova sincronizzazione; i blocchi IQ precedenti
alla modifica vengono scartati. L'accesso UHD viene serializzato ai confini di
lettura/burst. Non si cambia la configurazione mentre un thread usa lo stesso
percorso radio. Un errore UHD ferma il modem e viene mostrato nella GUI.

`Ferma` arresta RX/TX; `Avvia` riapre la radio. Ogni nuova acquisizione apre
nuovamente il file PCAP in scrittura, sovrascrivendolo. Chiudere la sola scheda
del browser non termina il server: usare `Ferma`, oppure Ctrl-C nel terminale
per chiudere anche il programma.

Per provare la GUI senza collegare la radio:

```sh
./avvia.command --gui-demo
```

La simulazione è contrassegnata esplicitamente e utilizza il vero modem in
loopback software con rumore/CFO. Non apre UHD, non trasmette RF e non scrive
PCAP. Per il solo terminale, usare `./avvia.command --no-gui`.
Eseguendo direttamente il Python, aggiungere `--gui` per la GUI:

```sh
python ieee80211p_native.py --gui
```

La pagina e le API sono servite esclusivamente su `127.0.0.1`, senza risorse
web esterne. Una porta libera viene scelta automaticamente; `--gui-port 8765`
per sceglierne una esplicitamente e `--no-browser` per aprire manualmente l'URL.

### Ampiezza dei segnali ricevuti

Il pannello **Segnale ricevuto · IQ grezzo** è collegato direttamente ai campioni
restituiti da UHD, prima della coda di decodifica e della ricerca dei pacchetti.
Funziona anche con rumore, trasmissioni non 802.11p o pacchetti non decodificabili.
Non richiede che la costellazione o i contatori RX mostrino frame validi.

- Grafico dell'inviluppo `|I+jQ|` nel tempo, con scala lineare o dBFS.
- RMS e picco, entrambi riportati come ampiezza numerica e in dBFS.
- Storico RMS e picco degli ultimi dieci secondi.
- Indicazione di campioni I/Q vicini al fondo scala digitale.
- Pausa della sola visualizzazione dell'ampiezza, senza fermare RX/TX.

Il selettore **Andamento nel tempo** offre due modalità:

- **Finestra fissa · 0–10 s**, predefinita: ogni punto rimane nella stessa
  posizione, l'asse resta da 0 a 10 secondi e al termine riparte una nuova
  scansione. La finestra riparte anche dopo reset o nuova acquisizione.
- **Scorrimento · ultimi 10 s**: i punti precedenti scorrono verso sinistra
  per mantenere visibili gli ultimi dieci secondi.

La casella **Scala Y fissa**, inizialmente attiva, mantiene l'ampiezza
del grafico superiore tra 0 e 1.5 in scala lineare, oppure tra −120 e +6 dBFS.
Disattivandola, il limite superiore si adatta al picco corrente. Lo storico
RMS/picco mantiene sempre la scala −120..+6 dBFS.
Per fermare completamente l'immagine usare **Pausa ampiezza**: i dati radio
continuano a essere elaborati. Le opzioni sono di visualizzazione e non
modificano la configurazione o l'acquisizione della B200.

RMS e picco aggregano tutti i blocchi letti dalla radio tra due aggiornamenti,
con cadenza massima di circa 10 Hz. Il grafico temporale visualizza il blocco
con il picco maggiore dell'intervallo; il tempo in microsecondi si riferisce
a quel blocco. La riduzione a 256 intervalli conserva minimo e massimo,
così un impulso stretto non sparisce per semplice sottocampionamento grafico.
Non è uno storico continuo di tutti i campioni e non ricostruisce campioni
persi prima della consegna da UHD. Dopo un overflow o una riconfigurazione
che resetta RX, la misura riparte da una nuova finestra.

Definizioni: `RMS = sqrt(mean(I²+Q²))`, `picco = max(|I+jQ|)` e
`dBFS = 20 log10(ampiezza)`, con riferimento ampiezza complessa 1.
Le misure nulle sono rappresentate con un limite numerico di −160 dBFS;
il grafico in dBFS ha un limite inferiore di −120 dBFS.
L'indicatore di fondo scala controlla `max(|I|,|Q|) >= 0.999`: è un'indicazione
sui campioni digitali consegnati da UHD, non una diagnosi certa di saturazione
dell'ADC o del front end RF. I livelli non sono calibrati in dBm e dipendono
anche dal gain RX impostato. Se il flusso si interrompe, la GUI segnala che
l'ultima misura non è più live, mantenendo visibile l'ultima acquisizione.

Se è necessaria un'installazione manuale delle immagini firmware/FPGA,
utilizzare il downloader fornito con UHD; per Homebrew si trova normalmente in:

```sh
python3 /opt/homebrew/opt/uhd/lib/uhd/utils/uhd_images_downloader.py
```

Se il percorso predefinito delle immagini non è scrivibile:

```sh
python3 /opt/homebrew/opt/uhd/lib/uhd/utils/uhd_images_downloader.py --types b2xx --install-location "$PWD/uhd-images"
UHD_IMAGES_DIR="$PWD/uhd-images" ./avvia.command
```

Non installare un pacchetto PyPI chiamato `uhd`: il programma usa direttamente
la libreria C installata da Homebrew. Il percorso può essere specificato con
`--uhd-library /percorso/libuhd.dylib` oppure `UHD_LIBRARY`.

## Collegamenti e formati

| Percorso | Comportamento |
|---|---|
| UDP `127.0.0.1:52001` → B200 | Ogni datagramma è un payload MSDU, massimo 1500 byte; aggiunge header MAC DATA di 24 byte e FCS |
| B200 → UDP `127.0.0.1:51001` | MPDU completo con header MAC, senza FCS: come il collegamento PHY→UDP del flowgraph originale |
| B200 → `ieee80211p_rx.pcap` | Frame con FCS e header Radiotap, leggibile in Wireshark |

Il PCAP viene sovrascritto a ogni avvio. Usare `--pcap nome-diverso.pcap`
per conservare acquisizioni precedenti. Le righe RX riportano CFO e una stima SNR;
non sono misure RSSI calibrate.

Per inviare solo il payload DATA sulla porta di uscita usare `--rx-payload-only`.
In tale modalità i frame non DATA e quelli cifrati vengono salvati in PCAP ma non
inoltrati su UDP. Non sono implementati decifratura, deframmentazione e A-MSDU.
Nella modalità predefinita non avviene nessuna di queste trasformazioni.

La frequenza predefinita è 5860 MHz, il sample rate 10 MS/s, encoding 3
(QPSK 3/4, 9 Mbit/s su canale 10 MHz). Source MAC `04:e5:48:20:3b:c8`,
destination e BSSID broadcast, come nel file originale. Il `config.ini` originale
non generava traffico nella catena connessa: l'ingresso effettivo è UDP.

Per RX e TX contemporanei, collegare l'antenna RX a **RX2** e l'antenna TX a
**TX/RX**. Il flowgraph originale selezionava TX/RX anche per la ricezione;
il programma usa RX2 come valore predefinito per poter mantenere i due percorsi.
In ricezione soltanto si può usare `--mode rx --rx-antenna TX/RX`.

```sh
./avvia.command --freq 5890000000 --encoding 0 --rx-gain 0.75 --tx-gain 0.5
./avvia.command --mode rx --pcap acquisizione.pcap
./avvia.command --mode tx --tx-port 52001
./avvia.command --help
```

I gain sono normalizzati tra 0 e 1, come negli USRP source/sink originali.
L'ampiezza digitale di default è 0.15, distinta dal gain RF. In TX viene aggiunto
padding nullo a ogni burst e controllato il BURST_ACK UHD; errori asincroni e
timeout fermano il programma con un errore, senza conteggiare un TX riuscito.

## Funzioni PHY

Preambolo STF/LTF, rilevamento pacchetto, stima CFO grossolana e fine,
equalizzazione LS da LTF, correzione fase e pendenza dai piloti, L-SIG con
parità, scrambler/descrambler, codice convoluzionale K=7 e Viterbi soft,
puncturing, interleaving, BPSK/QPSK/16-QAM/64-QAM, OFDM FFT64 e CP16,
FCS CRC32. Supporta tutti gli otto encoding legacy del flowgraph.

| `--encoding` | Modulazione | Codice | Mbit/s a 10 MHz |
|---|---|---|---|
| 0 | BPSK | 1/2 | 3 |
| 1 | BPSK | 3/4 | 4.5 |
| 2 | QPSK | 1/2 | 6 |
| 3 | QPSK | 3/4 | 9 |
| 4 | 16-QAM | 1/2 | 12 |
| 5 | 16-QAM | 3/4 | 18 |
| 6 | 64-QAM | 2/3 | 24 |
| 7 | 64-QAM | 3/4 | 27 |

Il sample rate può essere 5, 10 o 20 MS/s; FFT/CP restano identici e i tempi
simbolo cambiano coerentemente. 802.11p corrisponde al valore 10 MS/s.
La frequenza di centro è unica per TX e RX. Sono implementati LS, LMS decision
directed (alpha 0.5), Linear Comb dai piloti (alpha 0.2) e STA (alpha 0.5,
beta 2), seguendo le formule dei sorgenti forniti. La sincronizzazione e la
correzione fase/pendenza dai piloti restano quelle del modem Python: non viene
dichiarata equivalenza numerica bit per bit dell'intera ricezione GNU Radio.
Non include Gqrx o il display GPU fosphor, che non compaiono nella GUI del
flowgraph fornito.

## Stato della validazione e limiti

Questa è una nuova implementazione del modem, non un sostituto già qualificato
di gr-ieee802-11. Il test interno confronta TX e RX di questa implementazione;
non basta da solo a dimostrare interoperabilità con una radio esterna.
I test di regressione aggiungono errori FCS, pacchetti consecutivi, acquisizioni
interrotte, PCAP e confronto delle funzioni di codifica con i sorgenti C++ forniti.

La B200 non è stata rilevata nell'ambiente di sviluppo, quindi non sono stati
validati streaming USB, emissione RF, sensibilità o interoperabilità con RSU/OBU.
Per una prova di interoperabilità occorre verificare entrambe le direzioni
con un apparato indipendente, inizialmente a encoding 0 e traffico ridotto.

Non sono implementati CSMA/CA, CCA, ACK/SIFS, ritrasmissioni MAC o DCC: neppure
il flowgraph fornito realizzava questi protocolli. Non equivale a una scheda
Wi-Fi OCB del kernel. Non implementa PHY HT/VHT/HE o LDPC.

Il decoder Python richiede molto più tempo dell'airtime dei pacchetti. Il
programma legge la radio in un thread separato, ha una coda IQ limitata e
segnala overflow e blocchi scartati. Dopo ogni discontinuità riparte con una
nuova sincronizzazione, senza concatenare campioni non contigui. Numba aiuta
il decoder ma non garantisce real time a pieno carico. Non sono state misurate
prestazioni della variante JIT se Numba non è disponibile nell'ambiente di test.
I timestamp PCAP derivano dall'orologio UHD inizializzato sull'ora dell'host:
non sono sincronizzazione GNSS/PPS né una base validata per misure di latenza.

## Test

```sh
./avvia.command --self-test
.venv/bin/python -m unittest discover -s tests -v
```

Python >=3.10. `test_reference.py` usa opzionalmente clang++ e i sorgenti
`../gr-ieee802-11` per verificare la codifica senza linkare GNU Radio.
Il test ABI richiede gli header UHD Homebrew.

Protocol tables and conventions derived from bastibl/gr-ieee802-11, copyright
Bastian Bloessl, GPL-3.0-or-later. The standalone implementation is distributed
under GPL-3.0-or-later; see LICENSE.
