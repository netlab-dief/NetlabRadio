# Verifiche eseguite

Data: 24 novembre 2026. Mac ARM64, Python 3.13, NumPy 2.4.4, UHD 4.11.

- 34 test unittest completati con esito positivo, inclusa la vista spettrale centrata.
- Loopback software: 32 frame, tutti gli 8 encoding, payload 0/37/500/1500
  byte, multipath a due tap, rumore, CFO 22 kHz e chunk di 511 campioni.
- Pacchetti consecutivi con chunk di lunghezza variabile e CFO positivo e negativo.
- Controllo FCS: frame corrotto scartato e frame successivo recuperato.
- Reset dopo una discontinuità; rumore senza pacchetti.
- 32 confronti della catena di codifica dati con `utils.cc` C++ dei sorgenti
  gr-ieee802-11 forniti: encoding 0..7 e PSDU di 28/127/528/1528 byte.
  Compilazione standalone dei DSP helper, senza GNU Radio.
- Layout ctypes verificato compilando un programma C contro gli header UHD:
  dimensioni e offset di stream args, stream cmd, tune request e tune result.
- Caricamento della libuhd locale e risoluzione di tutte le 36 funzioni C usate.
- Invii UHD simulati: gestione short send, SOB/EOB, timeout e underflow.
- PCAP aperto con tshark: frequenza 5860 MHz, data rate 9 Mbit/s, FCS presente,
  source/destination MAC e sequence number decodificati correttamente.
- Sintassi Python e zsh verificate.
- GUI verificata nel browser: selezione 64QAM 3/4, equalizzatore STA,
  griglia, grafico di 480 punti e pulsante Ferma, in simulazione esplicita.
- 32 combinazioni equalizzatore/encoding verificate nel loopback software
  con CFO e multipath; il tap costellazione resta attivo anche sui frame
  con FCS errato, come nel collegamento carrier originale.
- Validazione atomica dei controlli, conservazione delle modifiche concorrenti,
  limiti della finestra di 480 punti e avvio/arresto del worker di simulazione.
- API HTTP locale: inoltro configurazioni, rigetto token errato e valori invalidi.
- Reconfigurazione durante RX verificata con radio simulata, con stop/reset/start
  e senza modificare UHD durante una lettura in corso.
- Ampiezza nota 0.3+0.4j: RMS/picco 0.5, livello −6.0206 dBFS;
  verifica durata del blocco e timestamp della forma d'onda.
- Aggregazione di blocchi consecutivi: impulso di un singolo campione
  conservato nell'inviluppo grafico e nel picco dell'intervallo, anche quando
  il successivo blocco contiene soltanto zeri.
- Fondo scala misurato sulle componenti I/Q, silenzio senza NaN/inf, reset
  delle finestre su gap e storico limitato a dieci secondi.
- Ricezione simulata senza frame: ampiezza aggiornata con costellazione vuota
  e contatore pacchetti RX a zero, verificando il tap prima della decodifica.
- Vista ampiezza verificata nel browser in simulazione: inviluppo, selettore
  dBFS, livelli RMS/picco, storico e pausa indipendente.
- Finestra fissa: coordinate dei punti invariate al passare del tempo,
  nuova scansione a dieci secondi e ripartenza su reset; selettore fisso/
  scorrimento e scala Y fissa verificati nella GUI.
- Spettro FFT: tono noto su bin positivo con ampiezza 0.5 misurato a
  −6.0206 dBFS/bin, offset negativo sul lato RF inferiore, passo bin e
  larghezza equivalente Hann verificati numericamente.
- Max hold conservato a frequenza invariata e resettato sul cambio di centro;
  silenzio con dati JSON finiti, senza NaN o infinito.
- GUI verificata in simulazione: spettro 5855..5865 MHz con centro a 5860,
  Max hold e successivo ricentramento a 5890 MHz (5885..5895 MHz).
- Costo indicativo del monitor sul Mac di sviluppo: circa 0.31 ms per blocco
  da 65536 campioni; misura singola, non una garanzia di throughput RF.

Misura indicativa locale senza Numba, su un singolo frame con 500 byte di
payload: codifica circa 2.2 ms, decodifica circa 21.3 ms, airtime 0.512 ms.
Non è un benchmark statistico; mostra che il decoder NumPy non sostiene
traffico continuo a pieno carico. La coda IQ e i contatori espongono le perdite.

Non verificati: compatibilità RF con apparati indipendenti, sensibilità,
streaming reale USB RX/TX, PHY performance, Numba compilato e installazione
online iniziale del lanciatore. `uhd_find_devices --args type=b200` non ha
trovato una B200 nell'ambiente disponibile. Il risultato va considerato un
prototipo sperimentale verificato in software, non un modem validato sul campo.
