# Scraper Bandi Provincia di Pistoia

Programma per raccogliere automaticamente i bandi di gara pubblicati sul sito
della Provincia di Pistoia (https://www.provincia.pistoia.it/gare), arricchirli
con i dati dei PDF di esito e con quelli ufficiali dell'ANAC, e salvare il tutto
in un file Excel formattato.

Sono disponibili tre interfacce che condividono la stessa logica: una finestra
desktop, una pagina web e una versione da terminale.

## Cosa fa

Per ogni bando che rispetta i filtri impostati, il programma raccoglie i dati da
**tre fonti diverse** e li unisce in un'unica tabella:

1. **Il sito della Provincia** — tipologia di gara, scelta del contraente, enti
   coinvolti, data di pubblicazione, scadenza della manifestazione di interesse,
   scadenza della gara, uno o piu' CIG.
2. **I PDF di esito allegati al bando** — l'elenco degli operatori che hanno
   manifestato interesse e di quelli invitati con le rispettive P.IVA e codici
   fiscali, le offerte ricevute, ammesse ed escluse, l'aggiudicatario, il
   ribasso e il valore dell'offerta. Sono dati che esistono **solo** dentro i
   documenti: ne' il sito ne' l'ANAC li espongono.
3. **L'API ufficiale ANAC** — numero gara, oggetto, CUP, CPV con descrizione,
   tipo di scelta del contraente, aggiudicatario e relativo codice fiscale. La
   protezione anti-bot Mosparo viene superata automaticamente.

## Struttura del progetto

```
.
├── gui.py             # Interfaccia grafica desktop (PyQt6)
├── main.py            # Versione da terminale, usata per il debug
├── scraper.py         # Logica: sito della Provincia + API ANAC
├── scraper_pdf.py     # Logica: lettura e interpretazione dei PDF di esito
├── save_data.py       # Generazione del file Excel
├── console.py         # Interruttore dei messaggi diagnostici
├── requirements.txt   # Librerie necessarie
└── web/
    ├── app.py         # Interfaccia web (Flask)
    ├── templates/
    │   └── index.html # Pagina dell'interfaccia web
    └── output/        # (creata da sola) file Excel prodotti dalla web app
```

I tre file di logica (`scraper.py`, `scraper_pdf.py`, `save_data.py`) non
dipendono da nessuna interfaccia: sono gli stessi identici file per tutte e tre.

L'interfaccia web sta in una sottocartella propria perche' si porta dietro i
suoi file (la pagina HTML e la cartella dei risultati). Per raggiungere i moduli
di logica, che stanno un livello piu' su, `app.py` aggiunge la cartella
principale del progetto ai percorsi di ricerca di Python:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

Il percorso e' calcolato a partire dalla posizione del file stesso, quindi
`app.py` funziona da qualunque cartella lo si avvii.

## Installazione

Serve **Python 3.10 o superiore**. Dalla cartella del progetto:

```bash
pip install -r requirements.txt
```

## Come si usa

Tutti i filtri sono facoltativi: lasciandoli vuoti si ottengono tutti i bandi.
Quelli disponibili sono gli stessi nelle tre interfacce:

| Filtro | Effetto |
|---|---|
| Parola chiave | cerca nell'oggetto del bando |
| CIG | cerca un codice specifico (funziona anche troncato) |
| Stato gara | Aperta, Aggiudicata, Deserta, Chiusa, ... |
| Tipologia | Appalto di Lavori, di Servizi, di Forniture, ... |
| Scelta del contraente | Procedura Aperta, Affidamento Diretto, ... |
| Intervallo di date | bandi pubblicati fra due date |
| P.IVA / C.F. invitato | solo i bandi in cui quell'operatore era fra gli invitati |

### Interfaccia grafica (`gui.py`)

```bash
python gui.py
```

Si apre una finestra in cui compilare i filtri, scegliere nome e cartella di
destinazione del file Excel e avviare la ricerca. Durante l'elaborazione una
barra mostra a che bando si e' arrivati, e il pulsante **Interrompi Ricerca**
ferma tutto a un punto pulito (in tal caso non viene prodotto alcun file).

Se non viene scelta alcuna cartella, il file finisce nella **cartella Download**
dell'utente. Sui sistemi dove quella cartella non esiste o ha un nome diverso —
per esempio su Linux con interfaccia in italiano, dove si chiama "Scaricati" —
il programma ripiega sulla cartella del progetto, e l'etichetta accanto al
pulsante *Sfoglia* indica sempre quale delle due verra' usata.

### Interfaccia web (`web/app.py`)

Dalla cartella principale del progetto:

```bash
python web/app.py
```

Poi aprire il browser su **http://127.0.0.1:5000**. La ricerca gira in
sottofondo e la pagina ne segue l'avanzamento; al termine compare il pulsante
per scaricare l'Excel.

I file prodotti finiscono nella cartella `web/output/`, creata automaticamente.
All'avvio di ogni nuova ricerca i vecchi `.xlsx` vengono rimossi: l'ultimo
risultato resta scaricabile finche' non se ne avvia un'altra. Il file vero e
proprio lo salva poi il browser, dove l'utente ha impostato i suoi download.

**Nota per macOS.** La porta 5000 puo' essere gia' occupata dal Ricevitore
AirPlay, attivo per impostazione predefinita su alcune versioni di macOS: in tal
caso all'avvio compare l'errore "Address already in use". Si risolve
disattivandolo (Impostazioni di Sistema → Generali → AirDrop e Handoff) oppure
cambiando porta nell'ultima riga di `web/app.py`:

```python
app.run(host="127.0.0.1", port=5001, debug=False)
```

ricordandosi allora di aprire il browser su `http://127.0.0.1:5001`.

### Versione da terminale (`main.py`)

```bash
python main.py
```

Il programma chiede i filtri uno alla volta (i menu si navigano con le
freccette) e infine il nome del file Excel.

E' la versione da usare **quando qualcosa non funziona**: e' l'unica che stampa
in console il dettaglio di cio' che sta accadendo, compresi i messaggi
diagnostici dei moduli di logica — tentativi verso l'ANAC, errori Mosparo, PDF
scansionati e cosi' via. Le altre due interfacce lavorano in silenzio.

## Il file Excel prodotto

La tabella ha **una riga per operatore invitato, per ogni lotto**. Non una riga
per bando: e' questa forma che permette di filtrare per operatore e contarne le
ricorrenze, cosa impossibile se tutti gli invitati stessero in una sola cella.
Un bando multi-lotto con molti invitati genera quindi parecchie righe, e i lotti
sono distinti da colori diversi.

Le colonne sono venti: CIG, Oggetto Gara, Tipologia, Scelta Contraente, Enti,
Data Pubblicazione, Scadenza Manif. Interesse, Data Scadenza, CUP, CPV,
Descrizione CPV, Tipo Scelta Contraente (ANAC), Tipologia lotto, Invitato,
P.IVA invitato, C.F. invitato, Aggiudicatario, CF/P.IVA Aggiudicatario,
Numero Gara, URL Bando.

Se non viene indicato un nome, il file si chiama `bandi_pistoia_` seguito da
data e ora.

## Note tecniche

### I messaggi diagnostici e `console.py`

I moduli di logica non decidono dove finiscono i loro messaggi: si limitano a
dichiararli con `log()`, e chi li usa sceglie se mostrarli. `main.py` accende
l'interruttore con `console.VERBOSE = True`; `gui.py` e `app.py` non lo fanno e
restano silenziosi.

Per riaccendere i messaggi ovunque basta cambiare il valore predefinito in
`console.py`. Attenzione: va usato `import console` seguito da
`console.VERBOSE = True`. La forma `from console import VERBOSE` non funziona,
perche' copierebbe il valore invece di modificarlo.

### Tempi e cortesia verso i server

Fra una richiesta e l'altra il programma attende qualche secondo, per non
sovraccaricare i server della Provincia e dell'ANAC. Una ricerca su un centinaio
di bandi richiede quindi diversi minuti: e' normale.

### Robustezza verso l'ANAC

I server ANAC sono spesso lenti o momentaneamente irraggiungibili, quindi ogni
CIG viene ritentato fino a dieci volte, gestendo anche i blocchi per eccesso di
richieste (errore 429). `gui.py` e `app.py` controllano inoltre che il servizio
risponda **prima** di iniziare, ed avvisano l'utente se le colonne ANAC
rischiano di restare vuote. `main.py` non fa questo controllo preliminare: parte
comunque e l'esito si vede dai messaggi in console.

### Riconoscimento degli operatori

La ricerca per P.IVA riconosce un operatore **solo** dal codice dichiarato nel
PDF, mai dalla ragione sociale: esistono imprese diverse con lo stesso nome, e
dedurre l'associazione nome-P.IVA significherebbe affermare qualcosa che il
documento non dice. I bandi che elencano gli invitati senza P.IVA ne' codice
fiscale restano quindi esclusi dal risultato. E' una scelta voluta: una riga
mancante e' visibile e onesta, una riga sbagliata no.

### Limiti noti

- I PDF **scansionati** (immagini senza testo) non sono leggibili: i campi
  documentali restano a "Non presente". Servirebbe un OCR.
- Il programma dipende dalla struttura delle pagine della Provincia e dal
  meccanismo di verifica dell'ANAC. Se cambiano, va aggiornato di conseguenza.