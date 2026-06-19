# Scraper Bandi Provincia di Pistoia

Programma per estrarre automaticamente i bandi di gara pubblicati sul sito
della Provincia di Pistoia (https://www.provincia.pistoia.it/gare), integrarli
con i dati ufficiali ANAC (CUP, CPV, aggiudicatario) e salvare tutto in un
file Excel formattato. Disponibile sia con interfaccia grafica desktop che
da riga di comando.

## Cosa fa il programma

1. Permette di impostare filtri di ricerca (parola chiave, CIG, stato gara,
   tipologia, scelta del contraente, data di pubblicazione minima).
2. Naviga tutte le pagine dei risultati del sito della Provincia applicando
   quei filtri.
3. Per ogni bando trovato, entra nella pagina dedicata ed estrae:
   - Tipologia di gara
   - Scelta del contraente
   - Enti coinvolti
   - Data di pubblicazione, scadenza manifestazione di interesse, scadenza gara
   - Uno o più CIG (alcuni bandi multi-lotto ne hanno diversi)
4. Per ogni CIG trovato, interroga direttamente l'API ufficiale dell'ANAC
   (superando la protezione anti-bot Mosparo in modo automatico) per
   recuperare:
   - Numero gara, oggetto gara
   - CUP e CPV
   - Tipo di scelta del contraente (versione ANAC)
   - Aggiudicatario e relativo codice fiscale
5. Salva tutti i risultati in un file Excel (`.xlsx`), con intestazioni
   colorate, righe alternate, filtri automatici e colonne dimensionate in
   base al contenuto.

## Struttura del progetto

```
.
├── gui.py             # Interfaccia grafica desktop (PyQt6) — modalità consigliata
├── main.py            # Interfaccia a riga di comando (terminale)
├── scraper.py         # Logica di scraping (sito Provincia + API ANAC)
├── save_data.py       # Salvataggio dei risultati in Excel
└── requirements.txt   # Librerie necessarie
```

### gui.py

Interfaccia grafica desktop costruita con PyQt6. Presenta una finestra con:
- Campi di testo per parola chiave oggetto e codice CIG
- Menu a tendina per stato gara, tipologia e scelta del contraente
- Sezione filtro data con validazione in tempo reale
- Sezione impostazioni di esportazione (nome file e cartella di destinazione)
- Pulsante "Avvia Ricerca" che diventa "Interrompi Ricerca" durante l'esecuzione
- Barra di avanzamento con indicazione del bando in elaborazione
- Messaggio finale con esito dell'operazione

### main.py

Versione a riga di comando. Contiene il menu interattivo (selezione filtri
con le freccette tramite la libreria `pick`), le mappe di traduzione tra le
opzioni leggibili e i codici richiesti dal sito, e la funzione
`avvia_ricerca_bandi` che coordina tutto il processo.

### scraper.py

Contiene tutte le funzioni di scraping:

- `genera_url_con_filtri` — costruisce l'URL di ricerca con i filtri scelti
- `estrai_lista_bandi` — scorre tutte le pagine di risultati e raccoglie i
  link ai singoli bandi, applicando eventualmente il filtro sulla data
- `estrai_dettagli_bando` — entra nella pagina di un singolo bando ed estrae
  i dati (tipologia, enti, date, lista CIG)
- `scarica_json_anac` — interroga l'API ANAC per un CIG specifico,
  gestendo in automatico il sistema di verifica anti-spam Mosparo (calcolo
  del proof-of-work e dei token di validazione)
- `estrai_dati_json_anac` — estrae dal JSON ANAC i campi di interesse
  (oggetto gara, CUP, CPV, aggiudicatario, ecc.)

### save_data.py

Contiene `salva_in_excel`, che riceve l'elenco dei risultati e genera il
file `.xlsx` formattato (intestazioni colorate, righe alternate, bordi,
filtri automatici, colonne dimensionate al contenuto).

## Installazione

Assicurati di avere Python 3.11 o superiore. Installa le dipendenze con:

```bash
pip install -r requirements.txt
```

## Utilizzo

### Interfaccia grafica (consigliata)

Avvia l'interfaccia grafica con:

```bash
python gui.py
```

Si aprirà una finestra desktop in cui:

1. Compila i filtri desiderati (tutti opzionali)
2. Imposta eventualmente un filtro sulla data minima di pubblicazione
3. Inserisci il nome del file Excel e scegli la cartella di destinazione
4. Premi **Avvia Ricerca**

Durante l'elaborazione la barra di avanzamento mostra il progresso bando per
bando. È possibile interrompere la ricerca in qualsiasi momento premendo il
pulsante **Interrompi Ricerca**. Al termine viene mostrato un messaggio con
l'esito dell'operazione.

### Riga di comando

Avvia la versione testuale con:

```bash
python main.py
```

Il programma chiederà in sequenza:

1. Una parola chiave per l'oggetto del bando (opzionale, INVIO per saltare)
2. Un codice CIG specifico da cercare (opzionale)
3. Lo stato della gara, la tipologia e la scelta del contraente, selezionabili
   con le freccette della tastiera
4. Se applicare un filtro sulla data di pubblicazione minima
5. Il nome da dare al file Excel risultante (opzionale, altrimenti viene
   generato automaticamente con data e ora)

## Note

- Il programma applica piccole pause tra le richieste per non sovraccaricare
  i server della Provincia e dell'ANAC.
- Le chiamate all'API ANAC includono un sistema di retry automatico (fino a
  10 tentativi) poiché i server ANAC sono talvolta lenti o temporaneamente
  irraggiungibili. Al termine viene mostrato un conteggio dei CIG per cui
  non è stato possibile recuperare i dati ANAC.
- Se un bando ha più CIG (es. bandi multi-lotto), viene generata una riga
  separata nell'Excel per ciascun CIG, mantenendo invariati i dati comuni
  del bando (tipologia, enti, date).
- L'interfaccia grafica (`gui.py`) è compatibile con Mac, Windows e Linux.