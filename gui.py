"""
Interfaccia grafica desktop dello scraper dei bandi (PyQt6).

Apre una finestra in cui impostare i filtri, avviare la ricerca e seguirne
l'avanzamento. Non contiene logica di scraping: quella sta nei moduli
condivisi, che questo file coordina tramite avvia_ricerca_bandi, funzione
identica a quella di web/app.py.

Come resta reattiva durante la ricerca
    Una scansione dura minuti: eseguirla nel thread della finestra la
    congelerebbe. Gira percio' in un thread separato, che pero' non puo'
    toccare i widget. I due mondi comunicano quindi per messaggi:

        thread di sfondo  ->  queue.Queue  ->  QTimer  ->  finestra

    Il thread deposita nella coda messaggi come "sono al bando 7 di 30"; un
    timer la svuota dieci volte al secondo (_controlla_coda) e traduce ogni
    messaggio in un aggiornamento visibile. L'interruzione viaggia in senso
    opposto, tramite un threading.Event che il motore consulta all'inizio di
    ogni bando, cosi' lo stop avviene sempre a un punto pulito.

Avvio
    python gui.py

Non stampa nulla in console: i messaggi diagnostici dei moduli condivisi
restano spenti (vedi console.py) perche' qui l'utente ha la finestra.
"""

import sys
import os
import threading
import time
import queue
import re
import requests
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QProgressBar,
    QFileDialog, QFrame, QSizePolicy, QLayout, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from scraper import (genera_url_con_filtri, estrai_lista_bandi, BASE_URL,
                     estrai_dati_json_anac, scarica_json_anac, estrai_dettagli_bando,
                     reimposta_via_anac)
from scraper_pdf import (estrai_dati_pdf_esito, estrai_link_pdf_esito,
                         seleziona_pdf_per_cig, seleziona_lotto_per_cig, risolvi_cig,
                         costruisci_lista_cig, cig_compatibile, invitato_con_piva)
from save_data import salva_in_excel


def cartella_download():
    """
    Percorso della cartella Download dell'utente, o stringa vuota se non c'e'.

    E' la destinazione predefinita dei file Excel.

    La stringa vuota fa da ripiego elegante: os.path.join("", "bandi.xlsx")
    restituisce "bandi.xlsx", cioe' il salvataggio nella cartella del
    progetto. Serve per i sistemi dove
    la cartella Download non esiste o ha un altro nome.
    """
    percorso = os.path.join(os.path.expanduser("~"), "Downloads")
    return percorso if os.path.isdir(percorso) else ""

# =====================================================================
# MAPPE FILTRI
# =====================================================================
MAPPA_STATO = {
    "Qualsiasi": "All", "Aperta": "AP", "Aggiudicata": "AG",
    "Deserta": "DE", "Non Aggiudicata": "NA", "Revocata": "RE",
    "Sospesa": "SO", "Chiusa": "CH"
}
MAPPA_TIPOLOGIA = {
    "Qualsiasi": "All", "Alienazioni": "159", "Asta Pubblica": "154",
    "Appalto di Forniture": "144", "Appalto di Lavori": "145",
    "Appalto di Servizi": "146", "Concessione di Lavori": "148",
    "Concessione di Servizi": "147", "Incarichi Professionali": "158"
}
MAPPA_CONTRAENTE = {
    "Qualsiasi": "All", "Procedura Aperta": "113", "Procedura Ristretta": "114",
    "Adesione ad Accordo Quadro/Convenzione": "771",
    "Procedura Negoziata Art. 50 D. Lgs. 36/2023": "899",
    "Procedura Negoziata Art. 36 D. Lgs. 50/2016": "126",
    "Previa Manifestazione di Interesse": "128",
    "Previa Consultazione Albo Fornitori": "129",
    "Affidamento Diretto": "130", "Affidamento attraverso MEPA": "131",
    "RDO - Richiesta di Offerta": "132", "ODA - Ordine Diretto d'Acquisto": "133",
    "Trattativa Diretta": "134",
    "Procedura Negoziata senza Previa Pubblicazione": "127",
    "Altre Procedure": "115", "Asta Pubblica": "139",
    "Dialogo Competitivo": "137", "Partenariato per l'Innovazione": "138",
    "Procedura Competitiva con Negoziazione": "135",
    "Project Financing": "140", "Somma Urgenza": "157"
}

# =====================================================================
# STILE GLOBALE (QSS): stile CCS per personalizzare la grafica
# =====================================================================
STILE_APPLICAZIONE = """
    QMainWindow {
        background-color: #ffffff;   /*Sfondo principale della finestra */
    }
    /*Sfondo e font per tutti i contenitori generici*/
    QWidget {
        background-color: #ffffff;
        color: #2c3e50;             /*Colore del testo principale*/
        font-family: 'Helvetica Neue', Helvetica, Arial;    
    }
    /*Etichette di testo (Label)*/
    QLabel {
        font-size: 13px;
        color: #34495e;
    }
    /*Stile per i campi di testo (Oggetto, CIG, Nome File)*/
    QLineEdit {
        border: 1.5px solid #1a73e8;    /*Bordo blu fisso coordinato*/
        border-radius: 6px;             /*Arrotondamento degli angoli*/
        padding: 5px 10px;              /*Spazio interno tra il testo e il bordo*/
        background-color: #ffffff;      /*Sfondo bianco come i menu*/
        font-size: 13px;
    }
    QLineEdit:hover {
        border: 1.5px solid #155cb4;    /*Il bordo si scurisce al passaggio del mouse*/
    }
    /*Effetto quando l'utente clicca dentro per scrivere (focus)*/
    QLineEdit:focus {
        border: 2px solid #155cb4;   /*Il bordo diventa leggermente più spesso quando l'utente scrive*/
        background-color: #ffffff;
    }

    /*Stile per i Menu a tendina (Filtri e Date)*/
    QComboBox {
        border: 1.5px solid #1a73e8;       /* bordo blu sempre presente */
        border-radius: 8px;
        padding: 3px 12px;                   /* padding verticale ridotto: il bordo inferiore non viene tagliato */
        min-height: 22px;
        background-color: #ffffff;           /* sfondo bianco (test giallo rimosso) */
        color: #1c1c1c;
        selection-background-color: #f2f7fd;
    }
    QComboBox:focus {
        border: 1.5px solid #1a73e8;        /* bordo blu solo quando e' attivo/selezionato */
    }
    QComboBox:hover {
        border: 1.5px solid #1a73e8;
        background-color: #fbfdff;          /* leggerissimo azzurrino al passaggio del mouse */
    }
    QComboBox:disabled {
        border: 1.5px solid #e3e6ea;
        background-color: #f4f5f7;
        color: #9aa5b1;
    }
    /* Area della freccina rimossa: nessun pulsante a destra, solo il bordo. */
    QComboBox::drop-down {
        border: none;
        width: 0px;
    }
    QComboBox::down-arrow {
        image: none;
        width: 0px;
        height: 0px;
    }
    /* Lista che si apre: voci spaziate, angoli arrotondati, riga evidenziata */
    QComboBox QAbstractItemView {
        border: 1px solid #d0d7de;
        border-radius: 8px;
        background-color: #ffffff;
        outline: none;                      /* toglie il bordo tratteggiato di focus */
        padding: 4px;
        selection-background-color: #f2f7fd; /* evidenziazione chiara: e' questa che macOS usa davvero */
        selection-color: #1a73e8;
    }
    QComboBox QAbstractItemView::item {
        min-height: 28px;
        padding: 4px 10px;
        margin: 2px 4px;                    /* stacca la voce dai bordi: l'evidenziazione arrotondata si vede */
        border-radius: 6px;                 /* angoli morbidi, coerenti col resto della pagina */
        color: #1c1c1c;
    }
    QComboBox QAbstractItemView::item:hover {
        background-color: #f2f7fd;           /* riga sotto il mouse: azzurro molto chiaro */
        color: #1c1c1c;
    }
    QComboBox QAbstractItemView::item:selected {
        background-color: #f2f7fd;           /* voce scelta: stesso azzurro chiarissimo */
        color: #1a73e8;                      /* testo blu su sfondo chiaro: ben leggibile */
    }
    /*Caselle di spunta (Checkbox data)*/
    QCheckBox {
        font-size: 13px;
        spacing: 8px;       /*Spazio tra il quadratino e il testo*/
    }
    /*Il quadratino vuoto della casella*/
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #dcdde1;
        border-radius: 4px;
        background-color: #f8f9fa;
    }
    /*Il quadratino quando è spuntato (diventa blu)*/
    QCheckBox::indicator:checked {
        background-color: #1a73e8;
        border: 1px solid #1a73e8;
    }
    /*Barra di caricamento*/
    QProgressBar {
        border: 1px solid #dcdde1;
        border-radius: 9px;
        text-align: center;     /*Centra la percentuale*/
        background-color: #f1f2f6;
        color: #2c3e50;
        font-weight: bold;
    }
    /*La "barra" che si riempie all'interno*/
    QProgressBar::chunk {
        background-color: #1a73e8;
        border-radius: 8px;
    }
    /*Separatori orizzontali*/
    QFrame[frameShape="4"] { 
        border: none;
        background-color: #f1f2f6;
        height: 1px;
        max-height: 1px;
    }
"""


# ============================================================
# MOTORE DI RICERCA — copiato da web/app.py, IDENTICO.
# Coordina scraping, PDF, ANAC e salvataggio. La GUI e la web app
# ne condividono la stessa logica; qui viene chiamato con callback
# che parlano con la coda della finestra (progresso, interruzione).
# ============================================================

def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, data_fine=None, piva_invitato=None, nome_file=None, deve_fermarsi=None, segnala_progresso=None):
    """
    Esegue una ricerca completa e ne salva il risultato in un file Excel.

    E' il cuore del programma: coordina i tre moduli di logica, che da soli non
    si conoscono fra loro. Per ogni bando trovato:

      1. costruisce l'URL di ricerca con i filtri e ne ricava l'elenco dei
         bandi (scraper.py);
      2. ne legge la pagina di dettaglio: tipologia, enti, date, CIG;
      3. scarica i PDF di esito e li interpreta, ricavando invitati, lotti e
         gli eventuali CIG assenti dalla pagina (scraper_pdf.py);
      4. interroga l'API ANAC per ciascun CIG: oggetto, CUP, CPV,
         aggiudicatario (scraper.py);
      5. accumula tutto e, alla fine, genera l'Excel (save_data.py).

    Questa funzione e' identica a quella di web/app.py: le due interfacce
    condividono lo stesso motore, e ogni correzione va riportata su entrambe.

    Parametri dei filtri (tutti facoltativi: se omessi non restringono nulla)
        parola_chiave   testo cercato nell'oggetto del bando
        cig             CIG cercato, anche parziale
        stato,          voci dei menu, tradotte nei codici del sito
        tipologia,      tramite le mappe MAPPA_* di questo file
        contraente
        data_limite     data di pubblicazione minima, formato ISO (aaaa-mm-gg)
        data_fine       data di pubblicazione massima, stesso formato
        piva_invitato   P.IVA o codice fiscale: tiene solo i bandi in cui quel
                        soggetto compare fra gli invitati dichiarati nei PDF

    Altri parametri
        nome_file       percorso del file Excel da creare
        deve_fermarsi   funzione senza argomenti che restituisce True quando
                        l'utente ha chiesto di interrompere. Viene interrogata
                        all'inizio di ogni bando, mai a meta' di un'operazione
        segnala_progresso  funzione (fatti, totale) chiamata a ogni bando
                        completato, per aggiornare la barra di avanzamento

    Restituisce
        Un dizionario con la chiave "anac_giu": True se ANAC e' stato
        interrogato ma non ha risposto per NESSUN CIG, cioe' se il servizio era
        guasto. Serve ad avvisare che le colonne ANAC sono vuote
        per un guasto, non perche' quelle gare non fossero pubblicate.
        Restituisce None se l'utente ha interrotto la ricerca: in quel caso non
        viene prodotto alcun file.
    """
    codice_stato = MAPPA_STATO[stato]
    codice_tipologia = MAPPA_TIPOLOGIA[tipologia]
    codice_contraente = MAPPA_CONTRAENTE[contraente]

    url_ricerca = genera_url_con_filtri(
        parola_chiave=parola_chiave, cig=cig, stato=codice_stato,
        tipologia=codice_tipologia, contraente=codice_contraente
    )

    elenco_link = estrai_lista_bandi(url_ricerca, data_limite=data_limite, data_fine=data_fine)

    lista_risultati = []
    contatore_falliti = 0
    # CIG effettivamente interrogati su ANAC: serve a distinguere un guasto del
    # servizio (falliscono TUTTI) da singole gare non pubblicate (ne fallisce
    # qualcuna, cosa normale).
    contatore_anac_tentati = 0

    for i, link in enumerate(elenco_link, 1):
        # Interruzione richiesta dall'utente: si controlla all'inizio di ogni
        # bando, cosi' lo stop avviene a un punto pulito (mai a meta' di una
        # chiamata ANAC o di una scrittura). Il bando in corso non viene
        # ripreso e, per scelta, NON si salva nulla del lavoro parziale.
        if deve_fermarsi is not None and deve_fermarsi():
            return
        # Avanzamento: annuncia il bando che si sta elaborando ORA ("bando i di
        # N"). Si segnala all'inizio, cosi' la barra dice cosa e' in corso: parte
        # dal primo e arriva all'ultimo mentre lo elabora. In terminale e' inerte.
        if segnala_progresso is not None:
            segnala_progresso(i, len(elenco_link))
        if i > 1:
            time.sleep(2)

        url_completo = f"{BASE_URL}{link}" if not link.startswith("http") else link

        dati_bando = estrai_dettagli_bando(url_completo)

        lista_cig = dati_bando.get("cig_list", [])
        # Alfabeto italiano completo (21 lettere, senza J K W X Y) — fix bando con 13 CIG (IndexError)
        lettere_lotti = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'L', 'M', 'N',
                         'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'Z']

        lista_pdf = estrai_link_pdf_esito(url_completo)

        # — FILTRO PER P.IVA DELL'INVITATO —
        # La P.IVA degli invitati esiste SOLO dentro i PDF: non e' un filtro
        # del sito ne' un dato ANAC, quindi i PDF vanno comunque scaricati e
        # letti. Il controllo si fa pero' QUI, prima di interrogare ANAC:
        # per i bandi che non interessano si
        # risparmiano tutte le chiamate al servizio (con la verifica Mosparo
        # attiva sono cinque richieste HTTP per ogni CIG).
        if piva_invitato:
            _match = None
            _motivo = None
            _cache_filtro = {}
            for _p in (lista_pdf or []):
                try:
                    _cache_filtro[_p] = estrai_dati_pdf_esito(_p)
                except Exception:
                    continue
                _match = invitato_con_piva(_cache_filtro[_p], piva_invitato)
                if _match:
                    _motivo = "piva"
                    break
            if not _match:
                # Nessun ripiego sul nome: si riconosce l'operatore SOLO dal suo
                # codice. La ragione sociale non e' un identificativo affidabile
                # (esistono imprese diverse con lo stesso nome, es. "S2R" o
                # "BANCHELLI REMO" che in archivio hanno due P.IVA distinte), e
                # dedurre l'associazione nome->P.IVA significherebbe affermare
                # qualcosa che il documento non dice. Meglio perdere i bandi che
                # non dichiarano il codice — una riga mancante e' visibile e
                # onesta, una riga sbagliata no.
                continue

        if not lista_cig:
            dati_pdf = {}
            if lista_pdf:
                dati_pdf = estrai_dati_pdf_esito(lista_pdf[0], lotto_corrente=None)

            # Risoluzione del CIG (pagina -> PDF -> "Non trovato"): la logica
            # sta in scraper_pdf.risolvi_cig, riusabile da qualunque frontend.
            cig_effettivo = risolvi_cig(None, dati_pdf)
            dati_anac = {}
            if cig_effettivo != "Non trovato":
                # ANAC con il CIG recuperato dal PDF: stessa struttura del blocco
                # (oggi commentato) del loop multi-CIG, cosi' alla riattivazione
                # di quello i due rami restano gemelli.
                contatore_anac_tentati += 1
                json_anac = scarica_json_anac(cig_effettivo)
                if json_anac:
                    dati_anac = estrai_dati_json_anac(json_anac)
                else:
                    contatore_falliti += 1
            lista_risultati.append({
                "provincia": dati_bando,
                "anac": dati_anac,
                "cig_corrente": cig_effettivo,
                "pdf": dati_pdf
            })
        else:
            # Cache delle estrazioni PDF: con l'aggancio CIG->PDF per contenuto
            # ogni PDF puo' dover essere letto per capire a quale CIG appartiene;
            # la cache evita di scaricare/estrarre due volte lo stesso PDF quando
            # si itera su piu' CIG della stessa gara.
            _cache_pdf = {}

            # IL PDF COMANDA, LA PAGINA INTEGRA: la lista dei CIG da processare
            # viene ricostruita dai CIG dichiarati nei PDF (per-lotto o testata),
            # cosi' TUTTI i lotti escono anche se la pagina espone CIG monchi o
            # mancanti; la lista di pagina resta il fallback per i PDF muti.
            _cig_pagina = list(lista_cig)
            lista_cig = costruisci_lista_cig(_cig_pagina, lista_pdf, cache=_cache_pdf)[0]

            if not lista_cig and lista_pdf:
                # Tutti i CIG di pagina scartati e nessun CIG dichiarato nei PDF:
                # il PDF va processato COMUNQUE — lo scarto vale per ANAC (che ha
                # comunque la guardia sulla lunghezza), NON per l'estrazione dei
                # dati. "N.A." e' il segnaposto storico del progetto per il CIG
                # mancante.
                lista_cig = ["N.A."]

            for idx, cig_singolo in enumerate(lista_cig):
                # cig_compatibile: il filtro utente aggancia anche se ha digitato
                # il CIG troncato visto in pagina (prefisso del CIG pieno)
                if cig and not cig_compatibile(cig_singolo, cig):
                    continue

                # Indicizzazione sicura: oltre le lettere disponibili usa l'etichetta numerica ("22", ecc.)
                if len(lista_cig) > 1:
                    lotto_corrente = lettere_lotti[idx] if idx < len(lettere_lotti) else str(idx + 1)
                else:
                    lotto_corrente = None

                dati_pdf = {}
                if lista_pdf:
                    if len(lista_pdf) > 1:
                        # Aggancio CIG->PDF per CONTENUTO (CIG dichiarato in testata),
                        # con fallback posizionale: vedi _seleziona_pdf_per_cig.
                        _sel = seleziona_pdf_per_cig(lista_pdf, idx, cig_singolo, cache=_cache_pdf)
                        if _sel is not None:
                            dati_pdf = _sel
                        else:
                            # idx oltre i PDF disponibili e nessun CIG dichiarato combacia:
                            # comportamento precedente (PDF unico indicizzato per lotto)
                            dati_pdf = estrai_dati_pdf_esito(lista_pdf[0], indice_lotto=idx if len(lista_cig) > 1 else None)
                    else:
                        # PDF unico: se il costruttore della lista CIG lo ha gia'
                        # estratto (cache) e i lotti dichiarano i loro CIG, si riusa
                        # quell'estrazione completa e l'aggancio al lotto avviene per
                        # CONTENUTO piu' sotto (niente ri-estrazione, niente indice).
                        _dati_cache = _cache_pdf.get(lista_pdf[0])
                        if _dati_cache is not None and any(
                                l.get("cig_lotto", "Non presente") != "Non presente"
                                for l in _dati_cache.get("lotti", [])):
                            dati_pdf = _dati_cache
                        else:
                            dati_pdf = estrai_dati_pdf_esito(lista_pdf[0], indice_lotto=idx if len(lista_cig) > 1 else None)

                    # Formato multi_lotto_std: un PDF, piu' lotti ognuno col suo CIG.
                    # Si restringe al SOLO lotto di questo CIG (aggancio per contenuto
                    # via cig_lotto, fallback posizionale) cosi' l'Excel porta
                    # i dati giusti — la logica sta in scraper_pdf.
                    _ha_cig_lotto = any(l.get("cig_lotto", "Non presente") != "Non presente"
                                        for l in dati_pdf.get("lotti", []))
                    # Restrizione anche SENZA cig_lotto: se il PDF e' unico e i
                    # suoi lotti sono tanti quanti i CIG di pagina, l'ordine dei
                    # lotti nel PDF corrisponde a quello dei CIG (Esito_F-2/F-3:
                    # 2 CIG in pagina, "Lotto 1 campi sportivi - Lotto 2
                    # palazzetto"). Senza questo ogni CIG si sarebbe portato
                    # dietro TUTTI i lotti, duplicandoli a ogni giro del ciclo.
                    _posizionale = (not _ha_cig_lotto
                                    and len(lista_pdf) == 1
                                    and len(lista_cig) > 1
                                    and len(dati_pdf.get("lotti", [])) == len(lista_cig))
                    if _ha_cig_lotto or _posizionale:
                        _lotto_sel = seleziona_lotto_per_cig(dati_pdf, cig_singolo, indice_lotto=idx)
                        if _lotto_sel is not None:
                            # Il ciclo itera PER CIG e qui si restringe al solo
                            # lotto di questo CIG. Il conteggio complessivo va
                            # pero' conservato: senza, il salvataggio vedrebbe
                            # sempre un solo lotto e classificherebbe come
                            # "Lotto singolo" anche i bandi multi-lotto.
                            dati_pdf = {**dati_pdf, "lotti": [_lotto_sel],
                                        "_totale_lotti": len(dati_pdf.get("lotti", []))}

                # Dati ANAC
                dati_anac = {}
                if cig_singolo.upper() == "N.A.":
                    contatore_falliti += 1
                else:
                    contatore_anac_tentati += 1
                    json_anac = scarica_json_anac(cig_singolo)
                    if json_anac:
                        dati_anac = estrai_dati_json_anac(json_anac)
                    else:
                        contatore_falliti += 1

                lista_risultati.append({
                    "provincia": dati_bando,
                    "anac": dati_anac,
                    "cig_corrente": cig_singolo,
                    "pdf": dati_pdf
                })

                time.sleep(2)

    if lista_risultati:
        salva_in_excel(lista_risultati, nome_file=nome_file, piva_invitato=piva_invitato)

    # Bilancio ANAC per il chiamante: se sono stati tentati dei CIG e sono
    # falliti TUTTI, il servizio era verosimilmente irraggiungibile; se ne e'
    # fallita solo una parte, e' la normale assenza di alcune gare da ANAC.
    anac_giu = (contatore_anac_tentati > 0 and contatore_falliti >= contatore_anac_tentati)
    return {
        "bandi": len(elenco_link),
        "anac_tentati": contatore_anac_tentati,
        "anac_falliti": contatore_falliti,
        "anac_giu": anac_giu,
    }


def anac_raggiungibile(tentativi=3, pausa=2):
    """
    Verifica che il servizio ANAC dei CIG risponda DAVVERO, prima di avviare
    la ricerca.

    Non basta pingare la homepage: la ricerca usa l'endpoint 'consultaCIG'
    (via diretta o Mosparo), che puo' essere giu' anche quando il sito
    principale risponde. Quindi si interroga proprio quell'endpoint con un CIG
    NOTO e sempre presente su ANAC: se torna un risultato, il servizio dei dati
    funziona; se torna vuoto, e' irraggiungibile.

    Usando un CIG che esiste con certezza, un esito negativo non puo' dipendere
    dal CIG mancante (caso normale e legittimo) ma solo dal servizio che non
    risponde: cosi' l'avviso 'ANAC giu'' non scatta a sproposito.

    Fa fino a 'tentativi' prove, uscendo al PRIMO successo: un singolo intoppo
    di rete non basta a dichiarare ANAC irraggiungibile (meno falsi allarmi),
    ma quando ANAC risponde subito il controllo finisce in un colpo. Solo se
    TUTTI i tentativi falliscono il servizio e' considerato giu'.
    """
    CIG_TEST = "A040010618"  # CIG reale e stabile (Chiesina Uzzanese, gia' verificato)
    for n in range(1, tentativi + 1):
        try:
            reimposta_via_anac()
            if scarica_json_anac(CIG_TEST, tentativi=3) is not None:
                return True
        except Exception:
            pass
        if n < tentativi:
            time.sleep(pausa)  # breve attesa prima di riprovare
    return False


class BandiPistoiaApp(QMainWindow):
    def __init__(self):
        """
        Costruisce la finestra e tutto il suo contenuto.

        Oltre ai widget prepara i tre strumenti che permettono alla ricerca di
        girare senza congelare l'interfaccia: un Event usato come bandiera di
        interruzione, una Queue su cui il thread di sfondo deposita i messaggi,
        e un QTimer che ogni decimo di secondo la svuota. E' la regola di ogni
        interfaccia grafica: solo il thread principale puo' toccare i widget,
        quindi il lavoro lungo sta altrove e comunica per messaggi.

        Le sezioni della finestra sono costruite dai metodi _crea_*, chiamati
        in fondo nell'ordine in cui compaiono a schermo.
        """
        super().__init__()  # Chiama l'inizializzazione della classe genitore (QMainWindow)

        self.setWindowTitle(
            "Bandi Provincia di Pistoia")  # Imposta il titolo che appare in alto nella finestra del sistema operativo

        self.resize(760, 600)  # Dimensione iniziale: piu' bassa, per stare in schermi piccoli.
        # Altezza minima contenuta: sotto questa soglia compare lo scroll invece
        # di tagliare la parte bassa (pulsanti, barra).
        self.setMinimumSize(680, 400)

        self.setStyleSheet(STILE_APPLICAZIONE)  # Applica alla finestra lo stile CSS definito sopra

        # VARIABILI PER LA GESTIONE DEI THREAD E DEI PROCESSI
        self._interrompi = threading.Event()  # Evento usato come "bandiera": se alzato (set), dice al thread dello scraper di fermarsi

        self._coda = queue.Queue()  # Coda di messaggi (Queue), serve per passare in sicurezza i dati dal thread dello scraper
        # (che lavora in background) al thread della GUI (che gestisce la grafica).

        self._ricerca_in_corso = False  # Variabile booleana per sapere se c'è un'elaborazione attiva in questo momento

        # Cartella di destinazione scelta dall'utente; None = destinazione
        # predefinita. Il percorso NON si ricava dal testo dell'etichetta:
        # quel testo e' un'informazione per l'occhio, non un dato del programma.
        self._cartella_scelta = None
        # True dopo che l'utente ha accettato di procedere senza dati ANAC:
        # evita di richiedere la conferma a ogni click sul pulsante.
        self._anac_confermato = False
        self._cartella_predefinita = cartella_download()
        self._etichetta_predefinita = ("Cartella Download (default)"
                                       if self._cartella_predefinita
                                       else "Cartella del progetto (default)")

        # Crea un Timer. Questo timer scatterà a intervalli regolari (es. ogni 100ms)
        # e chiamerà la funzione `_controlla_coda` per vedere se lo scraper ha inviato nuovi messaggi
        self._timer = QTimer()
        self._timer.timeout.connect(self._controlla_coda)

        # COSTRUZIONE LAYOUT PRINCIPALE
        centrale = QWidget()  # Crea il widget (contenitore) invisibile che farà da base per tutto

        # Imposta la politica di Focus: se l'utente clicca sul bianco (sul contenitore), toglie il focus (il cursore lampeggiante) dai campi di testo.
        centrale.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        # STRUTTURA: un contenitore esterno senza margini impila due parti —
        # l'HEADER istituzionale (bande blu a tutta larghezza, fuori dallo
        # scroll) e sotto l'AREA SCORREVOLE coi filtri. Cosi' le bande occupano
        # davvero tutta la larghezza, mentre i filtri conservano i loro margini.
        contenitore = QWidget()
        contenitore.setStyleSheet("background-color: #ffffff;")
        colonna = QVBoxLayout(contenitore)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(0)

        # Header a tutta larghezza (creato da _crea_intestazione, aggiunto qui).
        self._header_layout = colonna

        # AREA SCORREVOLE: il contenuto (filtri in poi) vive dentro una
        # QScrollArea. Cosi', se la finestra e' piu' bassa del contenuto, compare
        # la barra di scorrimento e la parte bassa resta sempre raggiungibile.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)  # il contenuto si allarga in orizzontale con la finestra
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(centrale)

        self._layout = QVBoxLayout(centrale)  # Layout verticale dei filtri (dentro lo scroll)

        self._layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinimumSize)  # Definisce spazio minimo per non far schiacciare i componenti
        self._layout.setSpacing(12)  # Spazio verticale tra i vari blocchi
        self._layout.setContentsMargins(30, 20, 30, 25)  # Spazio ai bordi (i filtri restano rientrati)

        # IMPLEMENTAZIONE INTERFACCIA
        # Prima l'header (va nel contenitore esterno, a tutta larghezza), poi i
        # filtri (vanno nel layout dentro lo scroll).
        self._crea_intestazione()
        # Aggiunge lo scroll sotto l'header e imposta il contenitore come centrale.
        colonna.addWidget(scroll)
        self.setCentralWidget(contenitore)

        self._aggiungi_separatore()
        self._crea_filtri()
        self._aggiungi_separatore()
        self._crea_sezione_data()
        self._aggiungi_separatore()
        self._crea_sezione_operatore()
        self._aggiungi_separatore()
        self._crea_sezione_salvataggio()
        self._aggiungi_separatore()
        self._crea_pulsanti()
        self._crea_barra_avanzamento()

        # Aggiunge uno spazio flessibile alla fine per spingere tutto in alto se la finestra è grande
        self._layout.addStretch()

        self.setFocus()  # Assicura che all'avvio nessun campo di testo sia selezionato di default

    def _aggiungi_separatore(self):
        """Inserisce una linea orizzontale per separare due blocchi di filtri."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        self._layout.addWidget(sep)

    def _crea_intestazione(self):
        """
        Costruisce l'intestazione istituzionale: barra scura con il nome
        dell'ente e fascia blu con stemma e titoli.

        E' l'unica parte che sta fuori dall'area scorrevole, cosi' le bande
        restano a tutta larghezza e ferme mentre il resto scorre. Riprende
        l'aspetto della web app perche' le due interfacce sembrino lo stesso
        programma.
        """
        # Barra ente (sottile, blu scuro) in cima, a tutta larghezza.
        barra_ente = QLabel("Regione Toscana - Provincia di Pistoia")
        barra_ente.setStyleSheet(
            "background-color: #004080; color: #ffffff; font-size: 12px; "
            "padding: 6px 16px;"
        )
        barra_ente.setContentsMargins(0, 0, 0, 0)

        # Fascia intestazione (blu Italia) con stemma e titoli.
        fascia = QWidget()
        fascia.setStyleSheet("background-color: #0066CC;")
        f_lay = QHBoxLayout(fascia)
        f_lay.setContentsMargins(16, 18, 16, 18)
        f_lay.setSpacing(14)

        # Stemma segnaposto: cerchio bianco con "PT" (da sostituire con lo stemma
        # reale quando disponibile).
        stemma = QLabel("PT")
        stemma.setFixedSize(48, 48)
        stemma.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stemma.setStyleSheet(
            "background-color: #ffffff; color: #0066CC; border-radius: 24px; "
            "font-size: 18px; font-weight: bold;"
        )
        f_lay.addWidget(stemma)

        # Titolo e sottotitolo, impilati a sinistra dello stemma.
        testi = QWidget()
        testi.setStyleSheet("background-color: transparent;")
        t_lay = QVBoxLayout(testi)
        t_lay.setContentsMargins(0, 0, 0, 0)
        t_lay.setSpacing(2)

        titolo = QLabel("Ricerca Bandi di Gara")
        titolo.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold; background: transparent;")
        sottotitolo = QLabel("Estrazione dati appalti e generazione tabella Excel")
        sottotitolo.setStyleSheet("color: #eaf2fb; font-size: 13px; background: transparent;")
        t_lay.addWidget(titolo)
        t_lay.addWidget(sottotitolo)
        f_lay.addWidget(testi)
        f_lay.addStretch()

        # L'header (barra + fascia) va nel contenitore ESTERNO, fuori dallo
        # scroll: cosi' occupa tutta la larghezza della finestra senza margini
        # bianchi ai lati. Nessun trucco di margini negativi.
        self._header_layout.addWidget(barra_ente)
        self._header_layout.addWidget(fascia)

    def _crea_filtri(self):
        """
        Costruisce la sezione dei filtri principali: parola chiave, CIG e i tre
        menu a tendina (stato, tipologia, scelta del contraente).

        Le voci dei menu arrivano dalle mappe MAPPA_* definite in cima al file,
        non sono scritte qui: aggiungere una tipologia significa modificare un
        solo punto del progetto.
        """

        # creiamo una "scatola" contenitore con layout verticale
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)  # Distanza tra i vari campi di input
        layout.setContentsMargins(0, 0, 0, 0)

        # Titolo
        titolo = QLabel("FILTRI DI RICERCA")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        # CAMPO OGGETTO
        lbl_ogg = QLabel("Parola chiave oggetto:")
        lbl_ogg.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        layout.addWidget(lbl_ogg)  # Aggiunge la scritta
        self.campo_oggetto = QLineEdit()  # Crea la casella in cui l'utente può scrivere
        self.campo_oggetto.setPlaceholderText("Qualsiasi...")  # Testo grigio di suggerimento che scompare quando scrivi
        self.campo_oggetto.setFixedHeight(35)
        layout.addWidget(self.campo_oggetto)  # Aggiunge la casella al layout

        # CAMPO CIG
        lbl_cig = QLabel("Codice CIG specifico:")
        lbl_cig.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        layout.addWidget(lbl_cig)
        self.campo_cig = QLineEdit()
        self.campo_cig.setPlaceholderText("Qualsiasi...")
        self.campo_cig.setFixedHeight(35)
        layout.addWidget(self.campo_cig)

        # SOTTO-LAYOUT ORIZZONTALE (Per affiancare Stato e Tipologia) li mettiamo accanto invece di uno sopra l'altro
        griglia = QHBoxLayout()
        griglia.setSpacing(15)  # Spazio orizzontale tra la colonna di sinistra e quella di destra

        # Colonna di Sinistra: Stato gara
        col1 = QVBoxLayout()  # Mini-layout verticale per impilare Scritta + Menu a tendina
        col1.setSpacing(4)
        lbl_st = QLabel("Stato gara:")
        lbl_st.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        col1.addWidget(lbl_st)

        self.menu_stato = QComboBox()  # Crea il menu a tendina
        self.menu_stato.addItems(
            list(MAPPA_STATO.keys()))  # Lo riempie prendendo le "chiavi" dal dizionario in cima al file
        self.menu_stato.setFixedHeight(38)
        col1.addWidget(self.menu_stato)  # Lo aggiunge

        # Colonna di Destra: Tipologia gara
        col2 = QVBoxLayout()
        col2.setSpacing(4)
        lbl_tip = QLabel("Tipologia gara:")
        lbl_tip.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        col2.addWidget(lbl_tip)

        self.menu_tipologia = QComboBox()
        self.menu_tipologia.addItems(list(MAPPA_TIPOLOGIA.keys()))
        self.menu_tipologia.setFixedHeight(38)
        col2.addWidget(self.menu_tipologia)

        # Aggiunge le due colonne alla riga orizzontale
        griglia.addLayout(col1)
        griglia.addLayout(col2)

        # Aggiunge l'intera riga orizzontale al layout verticale della scatola "Filtri"
        layout.addLayout(griglia)

        # CAMPO SCELTA CONTRAENTE
        lbl_con = QLabel("Scelta contraente:")
        lbl_con.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        layout.addWidget(lbl_con)

        self.menu_contraente = QComboBox()
        self.menu_contraente.addItems(list(MAPPA_CONTRAENTE.keys()))
        self.menu_contraente.setFixedHeight(38)
        layout.addWidget(self.menu_contraente)
        # Piccolo spazio sotto l'ultimo menu, cosi' il suo bordo inferiore non
        # viene a contatto con il separatore della sezione successiva.
        layout.addSpacing(4)

        # Aggiunge l'intero blocco "Filtri" al layout della finestra principale
        self._layout.addWidget(frame)

    def _crea_sezione_data(self):
        """
        Costruisce il filtro sull'intervallo di pubblicazione: una casella di
        attivazione e sei menu (giorno, mese, anno per la data di inizio e per
        quella di fine).

        I menu invece di un campo libero impediscono in partenza le date
        scritte male; restano da verificare solo quelle inesistenti, come il
        31 febbraio, di cui si occupa _valida_data.
        """
        # Creiamo il contenitore principale per la sezione data
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # Titolo
        titolo = QLabel("FILTRO DATA DI PUBBLICAZIONE")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        # CheckBox di attivazione
        self.checkbox_data = QCheckBox("Attiva limite temporale sui bandi")
        self.checkbox_data.setStyleSheet("font-weight: 500;")

        # SIGNAL & SLOT: Quando lo stato della casella cambia (cliccata o deselezionata),
        # PyQt chiama automaticamente la nostra funzione `_toggle_data`
        self.checkbox_data.stateChanged.connect(self._toggle_data)
        layout.addWidget(self.checkbox_data)

        # Riga orizzontale dei menu data
        riga_data = QHBoxLayout()
        riga_data.setSpacing(8)

        lbl_dm = QLabel("Bandi pubblicati a partire dal:")
        lbl_dm.setStyleSheet("color: #5c6b73;")
        riga_data.addWidget(lbl_dm)

        anno_corrente = datetime.now().year  # Recupera l'anno attuale dal sistema

        # MENU GIORNO
        self.menu_giorno = QComboBox()
        # "--" in testa: il giorno e' opzionale. Da solo o col mese l'anno resta
        # obbligatorio; i pezzi lasciati a "--" vengono completati automaticamente.
        self.menu_giorno.addItems(["--"] + [str(g).zfill(2) for g in range(1, 32)])
        self.menu_giorno.setEnabled(False)  # Disabilitato all'avvio (fino a che non si spunta la checkbox)
        self.menu_giorno.setFixedSize(65, 32)
        self.menu_giorno.currentTextChanged.connect(
            self._valida_data)  # Se l'utente cambia il giorno, chiama la funzione `_valida_data` per controllare se è corretto

        # MENU MESE
        self.menu_mese = QComboBox()
        self.menu_mese.addItems(["--"] + [str(m).zfill(2) for m in range(1, 13)])  # "--" + mesi 01-12
        self.menu_mese.setEnabled(False)
        self.menu_mese.setFixedSize(65, 32)
        self.menu_mese.currentTextChanged.connect(self._valida_data)

        # MENU ANNO
        self.menu_anno = QComboBox()
        # "--" + elenco dal 2010 fino all'anno corrente
        self.menu_anno.addItems(["--"] + [str(a) for a in range(2010, anno_corrente + 1)])
        self.menu_anno.setEnabled(False)
        self.menu_anno.setFixedSize(85, 32)
        self.menu_anno.currentTextChanged.connect(self._valida_data)

        # Aggiunge i tre menu alla riga orizzontale
        riga_data.addWidget(self.menu_giorno)
        riga_data.addWidget(self.menu_mese)
        riga_data.addWidget(self.menu_anno)
        riga_data.addStretch()  # Spinge tutto a sinistra lasciando vuoto lo spazio a destra
        layout.addLayout(riga_data)

        # Riga DATA FINE: opzionale. Con "--" la data fine non viene impostata e
        # si estrae fino ai bandi piu' recenti; e' anche possibile impostare solo
        # la fine lasciando l'inizio, o viceversa.
        riga_fine = QHBoxLayout()
        riga_fine.setSpacing(8)
        lbl_df = QLabel("fino al (opzionale):")
        lbl_df.setStyleSheet("color: #5c6b73;")
        riga_fine.addWidget(lbl_df)

        self.menu_giorno_fine = QComboBox()
        self.menu_giorno_fine.addItems(["--"] + [str(g).zfill(2) for g in range(1, 32)])
        self.menu_giorno_fine.setEnabled(False)
        self.menu_giorno_fine.setFixedSize(65, 32)
        self.menu_giorno_fine.currentTextChanged.connect(self._valida_data)

        self.menu_mese_fine = QComboBox()
        self.menu_mese_fine.addItems(["--"] + [str(m).zfill(2) for m in range(1, 13)])
        self.menu_mese_fine.setEnabled(False)
        self.menu_mese_fine.setFixedSize(65, 32)
        self.menu_mese_fine.currentTextChanged.connect(self._valida_data)

        self.menu_anno_fine = QComboBox()
        self.menu_anno_fine.addItems(["--"] + [str(a) for a in range(2010, anno_corrente + 1)])
        self.menu_anno_fine.setEnabled(False)
        self.menu_anno_fine.setFixedSize(85, 32)
        self.menu_anno_fine.currentTextChanged.connect(self._valida_data)

        riga_fine.addWidget(self.menu_giorno_fine)
        riga_fine.addWidget(self.menu_mese_fine)
        riga_fine.addWidget(self.menu_anno_fine)
        riga_fine.addStretch()
        layout.addLayout(riga_fine)

        # MESSAGGIO DI ERRORE DATA
        # Etichetta pronta a mostrare errori se la combinazione di data è errata. All'inizio è vuota (""), quindi invisibile.
        self.label_errore_data = QLabel("")
        self.label_errore_data.setStyleSheet("color: #d63031; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.label_errore_data)

        self._layout.addWidget(frame)

    def _crea_sezione_operatore(self):
        """
        Costruisce il filtro per operatore: casella di attivazione e campo per
        la P.IVA o il codice fiscale.

        Si chiede il codice e non la ragione sociale perche' l'identificazione
        avviene solo su quello: esistono imprese diverse con lo stesso nome, e
        i PDF non sempre associano nome e codice in modo affidabile.
        """
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        titolo = QLabel("RICERCA PER OPERATORE INVITATO")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        self.checkbox_piva = QCheckBox("Filtra per un operatore specifico (P.IVA o C.F.)")
        self.checkbox_piva.setStyleSheet("font-weight: 500;")
        self.checkbox_piva.stateChanged.connect(self._toggle_piva)
        layout.addWidget(self.checkbox_piva)

        riga = QHBoxLayout()
        riga.setSpacing(8)
        lbl = QLabel("P.IVA o C.F. invitato:")
        lbl.setStyleSheet("color: #5c6b73;")
        riga.addWidget(lbl)

        self.campo_piva = QLineEdit()
        self.campo_piva.setPlaceholderText("11 cifre (P.IVA) o 16 caratteri (C.F.)")
        self.campo_piva.setEnabled(False)
        # Parte disattivato: bordo neutro. Diventa blu quando si spunta la casella.
        self.campo_piva.setStyleSheet("QLineEdit { border: 1.5px solid #dcdde1; background-color: #f1f2f6; }")
        self.campo_piva.setFixedHeight(32)
        self.campo_piva.textChanged.connect(self._valida_piva)
        riga.addWidget(self.campo_piva)
        layout.addLayout(riga)

        # Nota: in tabella finiscono solo i bandi in cui l'operatore e' fra gli invitati.
        nota = QLabel("Solo i bandi in cui l'operatore compare fra gli invitati.")
        nota.setStyleSheet("color: #8a9ba8; font-size: 11px;")
        layout.addWidget(nota)

        self.label_errore_piva = QLabel("")
        self.label_errore_piva.setStyleSheet("color: #d63031; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.label_errore_piva)

        self._layout.addWidget(frame)

    def _toggle_piva(self):
        """
        Attiva o disattiva il campo P.IVA seguendo la sua casella di spunta,
        cambiandone il bordo perche' si veda a colpo d'occhio se e' in uso.
        """
        attivo = self.checkbox_piva.isChecked()
        self.campo_piva.setEnabled(attivo)
        if attivo:
            # Attivo: bordo blu, come una casella di filtro in uso.
            self.campo_piva.setStyleSheet("QLineEdit { border: 1.5px solid #1a73e8; }")
        else:
            # Spento: bordo neutro e sfondo grigino, per segnalare che e' inattivo.
            self.campo_piva.setStyleSheet("QLineEdit { border: 1.5px solid #dcdde1; background-color: #f1f2f6; }")
            self.label_errore_piva.setText("")
        self._valida_piva()

    def _valida_piva(self):
        """
        Controlla che il codice inserito sia lungo 11 cifre (P.IVA) o 16
        caratteri (codice fiscale), e restituisce True se va bene.

        Prima del controllo toglie spazi, punti, trattini e l'eventuale
        prefisso "IT", cosi' chi incolla "IT 01824600470" non viene respinto
        per un motivo di sola forma. Il messaggio di errore compare sotto il
        campo; un campo vuoto e' valido, perche' il filtro e' facoltativo.
        """
        if not self.checkbox_piva.isChecked():
            self.label_errore_piva.setText("")
            return True
        testo = self.campo_piva.text().strip()
        if not testo:
            self.label_errore_piva.setText("")
            return True
        pulito = re.sub(r'[\s.\-/]', '', testo).upper()
        if pulito.startswith("IT") and len(pulito) > 2:
            pulito = pulito[2:]
        if len(pulito) in (11, 16):
            self.label_errore_piva.setText("")
            return True
        self.label_errore_piva.setText("La P.IVA deve avere 11 cifre, il C.F. 16 caratteri.")
        return False

    def _toggle_data(self):
        """
        Attiva o disattiva i sei menu delle date seguendo la loro casella di
        spunta, e rivalida subito l'intervallo.
        """
        # Controlla se la casella è spuntata (True) o no (False)
        attivo = self.checkbox_data.isChecked()

        # Abilita o disabilita fisicamente i tre menu
        self.menu_giorno.setEnabled(attivo)
        self.menu_mese.setEnabled(attivo)
        self.menu_anno.setEnabled(attivo)
        # Anche i menu della data fine seguono la stessa casella.
        self.menu_giorno_fine.setEnabled(attivo)
        self.menu_mese_fine.setEnabled(attivo)
        self.menu_anno_fine.setEnabled(attivo)

        # Lo stato disabilitato ha gia' il suo stile nel foglio globale
        # (QComboBox:disabled): non serve piu' impostarlo a mano qui, cosi' i
        # menu data spenti hanno lo stesso aspetto curato di tutti gli altri.

        # controlliamo se la data inserita (anche se appena riattivata) è valida
        self._valida_data()

    def _valida_data(self):
        """
        Verifica che le due date esistano davvero e siano in ordine, e
        restituisce True se l'intervallo e' utilizzabile.

        Delega a _componi_data il lavoro sulle singole date (combinazioni
        ammesse e completamento dei pezzi mancanti) e qui aggiunge il controllo
        che la fine non preceda l'inizio. Gli errori compaiono sotto i menu.
        """

        # Se la spunta non è attiva, non c'è bisogno di validare nulla. Cancelliamo eventuali errori.
        if not self.checkbox_data.isChecked():
            self.label_errore_data.setText("")
            return True
        # Valida entrambe le date (inizio e fine) con le regole di combinazione
        # e completamento. _componi_data restituisce (iso, errore).
        _, err_i = self._componi_data("inizio")
        _, err_f = self._componi_data("fine")
        if err_i:
            self.label_errore_data.setText(f"⚠ Data inizio: {err_i}")
            return False
        if err_f:
            self.label_errore_data.setText(f"⚠ Data fine: {err_f}")
            return False
        # Coerenza intervallo: fine non prima di inizio.
        iso_i, _ = self._componi_data("inizio")
        iso_f, _ = self._componi_data("fine")
        if iso_i and iso_f and iso_f < iso_i:
            self.label_errore_data.setText("⚠ La data di fine precede quella di inizio.")
            return False
        self.label_errore_data.setText("")
        return True

    def _componi_data(self, quale):
        """
        Compone una data dai tre menu (giorno/mese/anno) di 'inizio' o 'fine',
        permettendo di ometterne dei pezzi con "--".

        Combinazioni valide (si compila da sinistra): solo anno, anno+mese,
        anno+mese+giorno. Mese senza anno o giorno senza mese sono incoerenti.
        I pezzi mancanti si completano secondo il verso:
          inizio -> primo istante (01/01 o 01 del mese)
          fine   -> ultimo istante (31/12 o ultimo giorno reale del mese)

        Restituisce (data_iso "aaaa-mm-gg" o "", errore "" o testo).
        """
        import calendar
        if quale == "inizio":
            g = self.menu_giorno.currentText()
            m = self.menu_mese.currentText()
            a = self.menu_anno.currentText()
        else:
            g = self.menu_giorno_fine.currentText()
            m = self.menu_mese_fine.currentText()
            a = self.menu_anno_fine.currentText()
        # "--" equivale a vuoto.
        g = "" if g == "--" else g
        m = "" if m == "--" else m
        a = "" if a == "--" else a

        if not a and not m and not g:
            return "", ""  # niente impostato
        if not a and (m or g):
            return "", "serve almeno l'anno."
        if a and not m and g:
            return "", "con il giorno indica anche il mese."

        anno = int(a)
        mese = int(m) if m else (12 if quale == "fine" else 1)
        if g:
            giorno = int(g)
        else:
            giorno = calendar.monthrange(anno, mese)[1] if quale == "fine" else 1
        # Verifica che la data esista (es. 31/02 non valido).
        try:
            datetime(anno, mese, giorno)
        except ValueError:
            return "", "data non valida (controlla il giorno)."
        return f"{anno:04d}-{mese:02d}-{giorno:02d}", ""

    def _crea_sezione_salvataggio(self):
        """
        Costruisce la sezione del salvataggio: nome del file Excel e scelta
        della cartella di destinazione.

        Entrambi facoltativi. Senza nome se ne genera uno con data e ora;
        senza cartella si usa quella predefinita (Download), indicata
        dall'etichetta accanto al pulsante Sfoglia.
        """
        # Scatola contenitore con layout verticale per questa sezione
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # Titolo del blocco
        titolo = QLabel("IMPOSTAZIONI DI ESPORTAZIONE")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        # Scritta informativa per il nome del file
        lbl_nf = QLabel("Nome file di output (Excel):")
        lbl_nf.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        layout.addWidget(lbl_nf)

        # Campo di testo in cui l'utente può digitare il nome dell'Excel
        self.campo_nome_file = QLineEdit()
        self.campo_nome_file.setPlaceholderText("Lascia vuoto per generazione automatica")
        self.campo_nome_file.setFixedHeight(35)

        # Ogni volta che il testo nella casella cambia viene attivato il controllo tramite la funzione _valida_nome_file
        self.campo_nome_file.textChanged.connect(self._valida_nome_file)
        layout.addWidget(self.campo_nome_file)

        # Etichetta di errore dedicata ai simboli vietati (inizialmente vuota)
        self.label_errore_nome = QLabel("")
        self.label_errore_nome.setStyleSheet("color: #d63031; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.label_errore_nome)

        # Riga orizzontale per la sezione dell cartella
        riga_cartella = QHBoxLayout()
        riga_cartella.setSpacing(10)

        # mostra il percorso scelto
        lbl_dest = QLabel("Destinazione:")
        lbl_dest.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        riga_cartella.addWidget(lbl_dest)

        self.label_cartella = QLabel(self._etichetta_predefinita)
        self.label_cartella.setStyleSheet("color: #7f8c8d; font-style: italic;")
        self.label_cartella.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        riga_cartella.addWidget(self.label_cartella)

        # Pulsante che attiva l'esplorazione delle risors
        self.pulsante_sfoglia = QPushButton("Sfoglia...")
        self.pulsante_sfoglia.setFixedWidth(100)
        self.pulsante_sfoglia.setFixedHeight(32)
        self.pulsante_sfoglia.setStyleSheet("""
            QPushButton {
                background-color: #f8f9fa;
                border: 1px solid #dcdde1;
                border-radius: 6px;
                font-weight: bold;
                color: #2c3e50;
            }
            QPushButton:hover {
                background-color: #f1f2f6;
                border: 1px solid #b2bec3;
            }
        """)
        self.pulsante_sfoglia.clicked.connect(
            self._scegli_cartella)  # Quando viene cliccato, si apre la finestra nativa di scelta cartella
        riga_cartella.addWidget(self.pulsante_sfoglia)

        # Aggiungiamo la riga orizzontale della cartella al layout principale di questa sezione
        layout.addLayout(riga_cartella)

        self._layout.addWidget(frame)

    def _scegli_cartella(self):
        """
        Apre la finestra di sistema per scegliere la cartella di destinazione.

        Il percorso scelto viene memorizzato in _cartella_scelta, che e' il
        dato usato al salvataggio: l'etichetta serve solo a mostrarlo.
        """
        cartella = QFileDialog.getExistingDirectory(self,
                                                    "Scegli cartella di destinazione")  # apre la finestra standard per scegliere una cartella, restituisce una stringa con il percorso
        # Se è stata scelta una cartella (la stringa non è vuota)
        if cartella:
            self._cartella_scelta = cartella  # memorizza la scelta: e' questo il dato usato al salvataggio
            self.label_cartella.setText(cartella)  # Aggiorna il testo dell'etichetta mostrando il percorso reale
            self.label_cartella.setStyleSheet("color: #2c3e50; font-style: normal; font-weight: 500;")

    def _valida_nome_file(self):
        """
        Controlla che il nome del file non contenga caratteri vietati dai
        sistemi operativi, e restituisce True se e' utilizzabile.

        Sono i nove caratteri proibiti da Windows, il piu' restrittivo dei tre
        sistemi: accettarli qui significherebbe produrre un file che non si
        puo' salvare, con un errore poco chiaro a fine ricerca.
        """
        nome = self.campo_nome_file.text().strip()  # Prende il testo digitato
        caratteri_vietati = {'/', '\\', ':', '*', '?', '"', '<', '>', '|'}  # Set di caratteri vietati
        trovati = [c for c in nome if c in caratteri_vietati]  # lista contenente solo i caratteri vietati inseriti
        if trovati:  ## Se la lista "trovati" contiene qualcosa, c'è un errore
            self.label_errore_nome.setText(
                "⚠ Il nome contiene simboli non validi per i file di sistema.")  # appare scritta di errore
            return False
        self.label_errore_nome.setText("")  # se non ci sono caratteri vietati cancella il testo e dà il via libera
        return True

    def _crea_pulsanti(self):
        """Costruisce i due pulsanti in fondo alla finestra: Reset e Avvia."""
        frame = QWidget()
        layout = QHBoxLayout(frame)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 5, 0, 0)

        # Pulsante di reset
        self.pulsante_reset = QPushButton("Reset Filtri")
        self.pulsante_reset.setFixedWidth(140)
        self.pulsante_reset.setFixedHeight(45)
        self.pulsante_reset.setStyleSheet("""
            QPushButton {
                background-color: #ffffff; 
                color: #7f8c8d; 
                font-weight: bold; 
                border: 1px solid #dcdde1;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #fafafa;
                color: #c0392b;
                border: 1px solid #c0392b;
            }
        """)
        self.pulsante_reset.clicked.connect(self._reset_filtri)  # con il click esegue funzione '_reset_filtri'

        # Pulsante avvia ricerca
        self.pulsante_ricerca = QPushButton("Avvia ricerca")
        self.pulsante_ricerca.setFixedHeight(45)
        self.pulsante_ricerca.setStyleSheet("""
            QPushButton {
                background-color: #1a73e8; 
                color: white; 
                font-weight: bold; 
                font-size: 14px; 
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #155cb4;
            }
        """)
        self.pulsante_ricerca.clicked.connect(
            self._gestisci_pulsante)  # con il click esgue il metodo '_gestisci_pulsante'

        # Li aggiungiamo al layout orizzontale (Reset a sinistra, Ricerca a destra)
        layout.addWidget(self.pulsante_reset)
        layout.addWidget(self.pulsante_ricerca)

        # Aggiunge il layout alla finestra
        self._layout.addWidget(frame)

    def _reset_filtri(self):
        """
        Riporta tutti i campi allo stato iniziale.

        Non fa nulla se una ricerca e' in corso: i filtri in quel momento sono
        congelati, e svuotarli darebbe l'impressione sbagliata di aver
        cambiato una ricerca gia' avviata.
        """
        # Se c'è una ricerca in corso blocca il reset
        if self._ricerca_in_corso:
            return

        # ripristina tutti i campi
        self.campo_oggetto.clear()
        self.campo_cig.clear()
        self.campo_nome_file.clear()
        self._cartella_scelta = None
        # Azzera anche la conferma ANAC: dopo un ripristino il pulsante torna
        # "Avvia Ricerca" e la verifica viene rifatta al prossimo avvio.
        self._anac_confermato = False
        self.pulsante_ricerca.setText("Avvia ricerca")
        self.label_cartella.setText(self._etichetta_predefinita)
        self.label_cartella.setStyleSheet("color: #7f8c8d; font-style: italic;")
        self.menu_stato.setCurrentIndex(0)
        self.menu_tipologia.setCurrentIndex(0)
        self.menu_contraente.setCurrentIndex(0)
        self.checkbox_data.setChecked(False)
        # Azzera anche i campi nuovi.
        self.checkbox_piva.setChecked(False)
        self.campo_piva.clear()
        self.menu_giorno_fine.setCurrentIndex(0)  # "--"
        self.menu_mese_fine.setCurrentIndex(0)
        self.menu_anno_fine.setCurrentIndex(0)
        self.etichetta_finale.setText("")
        self.etichetta_finale.hide()

    def _gestisci_pulsante(self):
        """
        Risponde al pulsante principale, che cambia funzione secondo lo stato:
        avvia la ricerca se e' ferma, ne chiede l'interruzione se e' in corso.

        Prima di avviare esegue tutte le validazioni (date, P.IVA, nome del
        file) e prepara il percorso di destinazione, generando il nome con
        data e ora se l'utente non l'ha indicato.
        """

        # Se la ricerca è già attiva allora il click è avvenuto su interrompi
        if self._ricerca_in_corso:
            self._interrompi.set()  # interrompe il thread
            self.etichetta_stato.setText("Interruzione in corso...")
            self.pulsante_ricerca.setEnabled(False)  # disattiva il pulsante per evitare click multipli
            return

        # Se la ricerca non è attiva ma i controlli di validita della data non vengono superati si ferma qui
        if not self._valida_data():
            return

        # Validazione P.IVA/C.F. (se il filtro operatore e' attivo)
        if not self._valida_piva():
            return

        nome = self.campo_nome_file.text().strip()  # prende il nome inserito
        if not self._valida_nome_file():  # se il nome non supera i controlli di attività si ferma
            return

        # se non è stato inserito il nome lo genera
        if not nome:
            nome = f"bandi_pistoia_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # controlla se il nome ha l'estensione excel, senno la aggiunge
        if not nome.endswith(".xlsx"):
            nome += ".xlsx"

        # Percorso finale: la cartella scelta dall'utente, altrimenti quella
        # predefinita (Download). os.path.join mette il separatore giusto per il
        # sistema operativo, e con destinazione vuota lascia il solo nome file,
        # cioe' la cartella del progetto.
        percorso = os.path.join(self._cartella_scelta or self._cartella_predefinita, nome)

        # Verifica preventiva della disponibilita' ANAC, come nella web app.
        # Se il servizio non risponde non si parte: si avvisa l'utente e lo si
        # lascia decidere, perche' una scansione da decine di minuti che nasce
        # gia' priva dei dati ANAC e' quasi sempre da rifare. Alla conferma il
        # pulsante cambia etichetta e il secondo click avvia comunque.
        if not self._anac_confermato and not anac_raggiungibile():
            self._anac_confermato = True
            self.etichetta_messaggio.setText(
                "AVVISO: i server ANAC al momento non sono raggiungibili. "
                "Puoi procedere comunque, ma l'estrazione sara' priva dei "
                "dati ANAC (oggetto, CUP, CPV, aggiudicatario)."
            )
            self.etichetta_messaggio.setStyleSheet(
                "color: #c0392b; font-weight: bold; padding: 6px;")
            self.etichetta_messaggio.setVisible(True)
            self.pulsante_ricerca.setText("Procedi senza dati ANAC")
            return

        # Lancia la funzione che avvia lo scraping, passando il percorso in cui salvare
        self._avvia_ricerca(percorso)

    def _crea_barra_avanzamento(self):
        """
        Costruisce gli elementi che raccontano l'andamento della ricerca:
        l'etichetta di stato, la barra di avanzamento e il messaggio finale.

        Barra e messaggio nascono nascosti e compaiono solo quando servono.
        """

        # Etichetta di stato, serve per mostrare messaggi tipo "Connessione al portale..." o "Elaborazione bando 5 di 10"
        self.etichetta_stato = QLabel("")
        self.etichetta_stato.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.etichetta_stato.setStyleSheet("color: #2c3e50; font-weight: 500; font-size: 12px;")
        self._layout.addWidget(self.etichetta_stato)

        # Barra di progresso
        self.barra = QProgressBar()
        self.barra.setFixedHeight(16)
        self.barra.hide()  # Si nasconde all'avvio del programma, si mostra solo durante la ricerca
        self._layout.addWidget(self.barra)

        # Etichetta finale, mostra il messaggio conclusivo
        self.etichetta_finale = QLabel("")
        self.etichetta_finale.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.etichetta_finale.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        self.etichetta_finale.hide()  # Nascosta, si mostra solo alla fine della ricerca
        self._layout.addWidget(self.etichetta_finale)

    def _blocca_campi(self, blocca):
        """
        Disabilita (blocca=True) o riabilita (False) tutti i campi dei filtri.
        Durante la ricerca restano congelati: modificarli non avrebbe effetto,
        perche' la scansione usa gia' i valori di partenza. A fine ricerca o
        dopo l'interruzione tornano modificabili, rispettando di nuovo lo stato
        delle caselle di attivazione (data e P.IVA).
        """
        campi = [
            self.campo_oggetto, self.campo_cig, self.menu_stato,
            self.menu_tipologia, self.menu_contraente, self.campo_nome_file,
            self.pulsante_sfoglia, self.checkbox_data, self.checkbox_piva,
        ]
        for c in campi:
            c.setEnabled(not blocca)
        # I menu data e il campo P.IVA seguono le loro caselle quando si sblocca.
        if blocca:
            for m in (self.menu_giorno, self.menu_mese, self.menu_anno,
                      self.menu_giorno_fine, self.menu_mese_fine, self.menu_anno_fine,
                      self.campo_piva):
                m.setEnabled(False)
        else:
            self._toggle_data()  # riallinea i menu data allo stato della casella
            self._toggle_piva()  # riallinea il campo P.IVA allo stato della casella

    def _avvia_ricerca(self, percorso_file):
        """
        Prepara la finestra per la ricerca e lancia il thread che la esegue.

        Congela i filtri, trasforma il pulsante Avvia in Interrompi, mostra la
        barra, raccoglie i valori dei campi e fa partire _esegui_ricerca in
        sottofondo insieme al timer che sorveglia la coda dei messaggi.

        Il thread e' daemon: se si chiude la finestra mentre lavora, muore con
        essa invece di tenere vivo il programma.
        """
        self._interrompi.clear()  # Pulisce l'evento di interruzione (nel caso fosse rimasto "alzato" da una ricerca precedente)
        self._ricerca_in_corso = True  # Segnala che c'è una ricerca attiva
        self.etichetta_finale.hide()  # Nasconde eventuali messaggi di fine operazione precedenti
        self._blocca_campi(True)  # Congela tutti i filtri durante la ricerca

        # Cambia l'aspetto e la funzione del tasto "Avvia", facendolo diventare "Interrompi" (rosso)
        self.pulsante_ricerca.setText("Interrompi ricerca")
        self.pulsante_ricerca.setStyleSheet("""
            QPushButton {
                background-color: #d63031; 
                color: white; 
                font-weight: bold; 
                font-size: 14px; 
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #b32424;
            }
        """)
        self.pulsante_ricerca.setEnabled(True)

        # Disabilita i pulsanti e i campi che non devono essere toccati mentre lo scraper lavora
        self.pulsante_reset.setEnabled(False)
        self.pulsante_sfoglia.setEnabled(False)
        self.campo_nome_file.setEnabled(False)

        # Inizializza la barra di progresso
        self.barra.setMaximum(0)
        self.barra.show()
        self.etichetta_stato.setText(
            "Ricerca avviata. Puo' richiedere alcuni minuti, non chiudere la finestra.")

        # Raccolta dei filtri. Si passano le ETICHETTE (es. "Aggiudicata"): la
        # traduzione nei codici del sito la fa il motore, come per la web app.
        # Le date sono gia' composte e completate da _componi_data (ISO o None).
        _iso_inizio = self._componi_data("inizio")[0] if self.checkbox_data.isChecked() else ""
        _iso_fine = self._componi_data("fine")[0] if self.checkbox_data.isChecked() else ""
        filtri = {
            "parola_chiave": self.campo_oggetto.text().strip(),
            "cig": self.campo_cig.text().strip(),
            "stato": self.menu_stato.currentText(),
            "tipologia": self.menu_tipologia.currentText(),
            "contraente": self.menu_contraente.currentText(),
            "data_limite": _iso_inizio or None,
            "data_fine": _iso_fine or None,
            "piva_invitato": (
                self.campo_piva.text().strip()
                if self.checkbox_piva.isChecked() and self.campo_piva.text().strip()
                else None
            ),
        }

        # LANCIO DEL THREAD DI BACKGROUND E DEL TIMER
        # Qui avviene la magia: invece di eseguire la ricerca sul processo principale (che bloccherebbe la grafica),
        # diciamo a Python: "Esegui _esegui_ricerca in parallelo, passandogli questi argomenti".
        # daemon=True fa in modo che se l'utente chiude la finestra con la X, anche questo thread muoia subito.
        threading.Thread(target=self._esegui_ricerca, args=(percorso_file, filtri), daemon=True).start()

        # Fa partire il timer che ogni 100 millisecondi controllerà la coda per aggiornare i testi e la barra
        self._timer.start(100)

    def _controlla_coda(self):
        """
        Svuota la coda dei messaggi e aggiorna la finestra di conseguenza.

        Chiamata dal timer ogni decimo di secondo, e' il punto in cui le
        notizie del thread di sfondo diventano modifiche visibili: e' infatti
        l'unico modo consentito, perche' i widget puo' toccarli solo il thread
        principale. Riconosce quattro tipi di messaggio: 'stato' per il testo,
        'barra_determinata' per il totale dei bandi, 'barra_valore' per
        l'avanzamento e 'fine' per la conclusione.
        """
        try:
            ## Ciclo infinito per svuotare TUTTI i messaggi accumulati nella coda in quel millesimo di secondo
            while True:
                # get_nowait() tenta di prendere un messaggio dalla coda.
                # Se la coda è vuota, non aspetta ma lancia immediatamente l'eccezione queue.Empty
                messaggio = self._coda.get_nowait()
                # Estrae il tipo di messaggio (es: "stato", "barra_valore", "fine")
                tipo = messaggio.get("tipo")

                # Aggiornamento del testo di stato
                if tipo == "stato":
                    # Cambia il testo dell'etichetta mostrando cosa sta facendo lo scraper
                    self.etichetta_stato.setText(messaggio.get("testo", ""))
                # Impostazione del totale dei bandi da elaborare
                elif tipo == "barra_determinata":
                    # Lo scraper ha scoperto quanti bandi ci sono in totale (es. 15).
                    # Impostiamo questo valore come massimo della barra e la azzeriamo.
                    self.barra.setMaximum(messaggio.get("totale", 100))
                    self.barra.setValue(0)
                # Avanzamento della barra di caricamento
                elif tipo == "barra_valore":
                    # Aggiorna il quadratino blu della barra (es. bando 4 di 15, poi 5 di 15...)
                    self.barra.setValue(messaggio.get("valore", 0))
                # Processo terminato (con successo, errore o interruzione)
                elif tipo == "fine":
                    # Ferma il Timer: non c'è più bisogno di controllare la coda
                    self._timer.stop()
                    # Chiama il metodo per ripristinare i pulsanti e mostrare il verdetto finale
                    self._fine_ricerca(messaggio.get("percorso"), messaggio.get("testo", ""))
                    return  # Esce dal metodo immediatamente
        except queue.Empty:
            pass  # Questa eccezione viene catturata quando la coda si svuota completamente.
            # È il segnale per uscire dal ciclo 'while True' e ridare il controllo alla GUI,
            # in attesa del prossimo scatto del Timer.

    def _esegui_ricerca(self, percorso_file, filtri):
        """
        Adattatore fra la GUI e il motore condiviso avvia_ricerca_bandi.

        Non contiene piu' la logica di scraping (quella e' nel motore, identico
        a quello della web app): qui si limita a tradurre fra i due mondi.
        - segnala_progresso -> messaggi 'barra_determinata'/'barra_valore'/'stato'
          che la coda della GUI gia' sa interpretare;
        - deve_fermarsi     -> l'Event di interruzione della GUI;
        - a fine lavoro mette in coda il messaggio 'fine'.
        Gira nel thread di sfondo che la GUI ha gia' avviato.
        """
        try:
            # La disponibilita' ANAC e' gia' stata verificata prima di avviare
            # il thread (_gestisci_pulsante), come nella web app: se il servizio
            # non rispondeva, l'utente ha esplicitamente confermato di voler
            # procedere senza quei dati. Qui non si ripete il controllo.

            # Progresso: il motore chiama con (fatti, totale). Al primo colpo si
            # imposta il massimo della barra; a ogni bando se ne aggiorna il valore
            # e il testo di stato.
            self._barra_impostata = False

            def _progresso(fatti, totale):
                """Traduce l'avanzamento del motore in messaggi per la coda."""
                if not self._barra_impostata:
                    self._coda.put({"tipo": "barra_determinata", "totale": totale})
                    self._barra_impostata = True
                # Percentuale sui bandi CONCLUSI (fatti - 1), come nella web
                # app: la barra mostra il lavoro finito, la scritta quello in
                # corso. Cosi' le due interfacce riportano lo stesso numero.
                _perc = round(((fatti - 1) / totale) * 100) if totale else 0
                self._coda.put({"tipo": "stato",
                                "testo": f"Elaborazione bando {fatti} di {totale} ({_perc}%)"})
                self._coda.put({"tipo": "barra_valore", "valore": fatti})

            def _deve_fermarsi():
                """Riferisce al motore se l'utente ha premuto Interrompi."""
                return self._interrompi.is_set()

            # Chiamata al motore condiviso: stessa logica della web app.
            esito = avvia_ricerca_bandi(
                parola_chiave=filtri["parola_chiave"],
                cig=filtri["cig"],
                stato=filtri["stato"],
                tipologia=filtri["tipologia"],
                contraente=filtri["contraente"],
                data_limite=filtri["data_limite"],
                data_fine=filtri["data_fine"],
                piva_invitato=filtri["piva_invitato"],
                nome_file=percorso_file,
                deve_fermarsi=_deve_fermarsi,
                segnala_progresso=_progresso,
            )

            # Interruzione: il motore esce restituendo None se fermato.
            if self._interrompi.is_set():
                self._coda.put({"tipo": "fine", "percorso": None,
                                "testo": "Ricerca interrotta. Nessun file prodotto."})
                return

            # Esito e messaggio finale.
            messaggio = "Completato: tabella salvata con successo."
            if esito and esito.get("anac_giu"):
                messaggio = ("Completato, ma ANAC non era raggiungibile: le colonne "
                             "con i dati ANAC risultano vuote.")
            elif esito and esito.get("anac_falliti"):
                messaggio += f" ({esito['anac_falliti']} CIG senza dati ANAC)"

            self._coda.put({"tipo": "fine", "percorso": percorso_file, "testo": messaggio})

        except Exception as e:
            self._coda.put({"tipo": "fine", "percorso": None, "testo": f"Errore: {e}"})

    def _fine_ricerca(self, percorso_file, messaggio):
        """
        Riporta la finestra allo stato di riposo e mostra il verdetto.

        Sblocca i filtri, ripristina il pulsante Avvia, nasconde la barra e
        scrive il messaggio finale: verde se il file e' stato prodotto,
        arancione se la ricerca e' stata interrotta, non ha dato risultati o
        si e' fermata per un errore.
        """

        self._ricerca_in_corso = False  # Reset dello stato interno, dice che non c'è più nessuna ricerca in corso

        self._blocca_campi(False)  # Riabilita i filtri (a fine ricerca o dopo interruzione)

        self.barra.hide()  # Nasconde la barra di caricamento

        self.etichetta_stato.setText("")  # Nasconde la scritta che mostrava i passaggi

        # Ripristina il pulsante principale, trasforma il pulsante rosso interrompi nel pulsante originale avvia ricerca
        self.pulsante_ricerca.setText("Avvia ricerca")
        self.pulsante_ricerca.setStyleSheet("""
            QPushButton {
                background-color: #1a73e8; 
                color: white; 
                font-weight: bold; 
                font-size: 14px; 
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #155cb4;
            }
        """)
        self.pulsante_ricerca.setEnabled(True)  # Riattiva il pulsante

        # Riattiva tutti i pulsanti che erano stati disattivati per sicurezza durante la ricerca
        self.pulsante_reset.setEnabled(True)
        self.pulsante_sfoglia.setEnabled(True)
        self.campo_nome_file.setEnabled(True)

        # Logica dei colori: se 'percorso_file' esiste (True), significa che il file è stato salvato con successo
        # e usa il verde. Se è None (False), significa errore, nessun bando o interruzione, e usa l'arancione.
        colore = "#2ecc71" if percorso_file else "#e67e22"
        self.etichetta_finale.setStyleSheet(
            f"color: {colore}; font-weight: bold;")  # Applica il colore scelto e imposta il grassetto
        self.etichetta_finale.setText(
            messaggio)  # Inserisce il testo (es. "Completato! 10 bandi elaborati" oppure "Operazione interrotta")
        self.etichetta_finale.show()  # Rende finalmente visibile il messaggio di risultato in fondo all'applicazione


if __name__ == "__main__":
    # Controlla se la versione di Qt in uso possiede l'attributo per lo scaling automatico.
    # Se presente, lo attiva per evitare che i font e i pulsanti si vedano sfocati o minuscoli.
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    # Fa la stessa verifica per le icone e le immagini (Pixmap),
    # garantendo che vengano renderizzate a doppia densità dove supportato.
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # INIZIALIZZAZIONE E AVVIO DELL'APPLICAZIONE
    # Crea l'oggetto QApplication fondamentale. Gestisce il flusso di controllo,
    # le impostazioni principali e riceve gli argomenti passati da riga di comando (sys.argv).
    app = QApplication(sys.argv)

    # Stile "Fusion": rende l'interfaccia indipendente dal tema nativo del
    # sistema operativo. Serve soprattutto su macOS, dove il tema di default
    # ignora alcune personalizzazioni CSS dei menu a tendina (la freccina e il
    # colore delle voci selezionate). Con Fusion tutte le regole di stile
    # vengono rispettate in modo uniforme su Windows, Mac e Linux.
    app.setStyle("Fusion")

    # Istanzia la classe della nostra interfaccia grafica (chiamando il metodo __init__ )
    finestra = BandiPistoiaApp()

    # Rende la finestra visibile sullo schermo dell'utente
    finestra.show()

    # Avvia l'event loop principale di PyQt (app.exec()). Il programma rimane "vivo" e in ascolto
    # dei click dell'utente finché la finestra non viene chiusa.
    # sys.exit passera al sistema operativo il codice di uscita corretto (0 se si chiude normalmente).
    sys.exit(app.exec())