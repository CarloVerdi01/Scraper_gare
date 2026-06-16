# Scraper Bandi Provincia di Pistoia

Programma per estrarre automaticamente i bandi di gara pubblicati sul sito
della Provincia di Pistoia (https://www.provincia.pistoia.it/gare), integrarli
con i dati ufficiali ANAC (CUP, CPV, aggiudicatario) e salvare tutto in un
file Excel formattato.

## Cosa fa il programma

1. Chiede all'utente alcuni filtri di ricerca (parola chiave, CIG, stato gara,
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
├── main.py            # Interfaccia utente: chiede i filtri e avvia la ricerca
├── scraper.py         # Logica di scraping (sito Provincia + API ANAC)
├── save_data.py       # Salvataggio dei risultati in Excel
└── requirements.txt   # Librerie necessarie
```

### main.py

Contiene il menu interattivo (selezione filtri con le freccette tramite la
libreria `pick`), le mappe di traduzione tra le opzioni leggibili e i codici
richiesti dal sito, e la funzione `avvia_ricerca_bandi` che coordina tutto
il processo.

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

Avvia il programma con:

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

A questo punto il programma cerca i bandi corrispondenti, mostra a console
l'avanzamento bando per bando (inclusi i dati ANAC recuperati per ogni CIG)
e, al termine, salva tutto in un file `.xlsx` nella cartella del progetto.

### Esempio di avvio

```
=========================================
        SCRAPER PROVINCIA PISTOIA        
=========================================
Compila i campi di testo o premi INVIO per saltarli.

Inserisci parola chiave OGGETTO: 
Inserisci codice CIG specifico: 
-> Stato Gara selezionato: Aggiudicata
-> Tipologia Gara selezionato: Appalto Di Lavori
-> Scelta Del Contraente selezionato: Procedura Negoziata Art. 50 D. Lgs. 36/2023

Vuoi filtrare i bandi in base alla data di pubblicazione? (s/n): n
-> Nessun filtro data applicato.

Come vuoi chiamare il file Excel? (premi INVIO per nome automatico): Lista_Bandi_2026

[+] Avvio ricerca sul sito...
...
[+] File Excel salvato: Lista_Bandi_2026.xlsx
```

## Note

- Il programma applica piccole pause tra le richieste per non sovraccaricare
  i server della Provincia e dell'ANAC.
- Le chiamate all'API ANAC includono un sistema di retry automatico (fino a
  10 tentativi) poiché i server ANAC sono talvolta lenti o temporaneamente
  irraggiungibili. Alla fine dell'esecuzione viene mostrato un conteggio dei
  CIG per cui non è stato possibile recuperare i dati ANAC.
- Se un bando ha più CIG (es. bandi multi-lotto), viene generata una riga
  separata nell'Excel per ciascun CIG, mantenendo invariati i dati comuni
  del bando (tipologia, enti, date).