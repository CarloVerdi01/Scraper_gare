"""
Interfaccia web per la ricerca dei bandi della Provincia di Pistoia.

AUTONOMA da main.py: tutta la logica di orchestrazione (scraping -> filtro
operatore -> PDF -> ANAC -> salvataggio) vive qui e chiama direttamente i
moduli di logica scraper, scraper_pdf e save_data. main.py resta separato,
usato solo per il debug da terminale; questa web app non lo importa.

Versione SEMPLICE: la ricerca gira in un thread di sfondo (una scansione dura
minuti e bloccherebbe la pagina), il browser ne interroga lo stato e a fine
lavoro scarica l'Excel. L'avanzamento in tempo reale e' un passo successivo,
gia' predisposto: lo stato del job tiene un campo pronto a ospitare il log.
"""
import os
import sys
import re
import time
import uuid
import threading
import requests
from datetime import datetime

from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, abort)

# Import diretti dai moduli di LOGICA (mai da main.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import (genera_url_con_filtri, estrai_lista_bandi, BASE_URL,
                     estrai_dati_json_anac, scarica_json_anac, estrai_dettagli_bando,
                     reimposta_via_anac)
from scraper_pdf import (estrai_dati_pdf_esito, estrai_link_pdf_esito,
                         seleziona_pdf_per_cig, seleziona_lotto_per_cig, risolvi_cig,
                         costruisci_lista_cig, cig_compatibile, invitato_con_piva)
from save_data import salva_in_excel


# =====================================================================
# MAPPE DEI FILTRI (codici del sito della Provincia)
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
# ORCHESTRAZIONE DELLA RICERCA
# Spostata qui da main.py: e' il cuore che coordina i moduli di logica.
# =====================================================================
def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, data_fine=None, piva_invitato=None, nome_file=None, deve_fermarsi=None, segnala_progresso=None):
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


# =====================================================================
# WEB APP
# =====================================================================
app = Flask(__name__)

CARTELLA_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(CARTELLA_OUTPUT, exist_ok=True)


def _svuota_output():
    """
    Rimuove i file .xlsx dalla cartella output. Chiamata all'avvio di ogni nuova
    ricerca: l'ultimo file resta disponibile (scaricabile piu' volte) finche'
    non se ne avvia un'altra, poi viene rimosso per non accumulare file vecchi.
    Eventuali errori (file aperto, permessi) vengono ignorati: la pulizia e'
    un'operazione "best effort" che non deve mai bloccare la ricerca.
    """
    try:
        for nome in os.listdir(CARTELLA_OUTPUT):
            if nome.lower().endswith(".xlsx"):
                try:
                    os.remove(os.path.join(CARTELLA_OUTPUT, nome))
                except OSError:
                    pass
    except OSError:
        pass

_job = {}
_lock = threading.Lock()


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


def _iso(data_it):
    """gg/mm/aaaa -> aaaa-mm-gg, il formato che l'orchestrazione si aspetta."""
    try:
        return datetime.strptime(data_it, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _esegui_ricerca(id_job, filtri):
    """Gira nel thread di sfondo: chiama l'orchestrazione e registra l'esito."""
    try:
        nome_file = filtri.get("nome_file") or \
            f"bandi_pistoia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        if not nome_file.lower().endswith(".xlsx"):
            nome_file += ".xlsx"
        percorso = os.path.join(CARTELLA_OUTPUT, nome_file)

        # Callback di avanzamento: registra nello stato del job a che bando si
        # e' arrivati, cosi' il polling del browser puo' disegnare la barra.
        def _progresso(fatti, totale):
            with _lock:
                if id_job in _job:
                    _job[id_job]["fatti"] = fatti
                    _job[id_job]["totale"] = totale

        esito = avvia_ricerca_bandi(
            parola_chiave=filtri.get("parola_chiave", ""),
            cig=filtri.get("cig", ""),
            stato=filtri.get("stato", "Qualsiasi"),
            tipologia=filtri.get("tipologia", "Qualsiasi"),
            contraente=filtri.get("contraente", "Qualsiasi"),
            data_limite=filtri.get("data_limite"),
            data_fine=filtri.get("data_fine"),
            piva_invitato=filtri.get("piva_invitato"),
            nome_file=percorso,
            deve_fermarsi=lambda: _job.get(id_job, {}).get("stop", False),
            segnala_progresso=_progresso,
        )
        # Se l'utente ha interrotto, l'orchestrazione esce senza creare il file:
        # lo stato diventa "interrotto", non "finito".
        with _lock:
            if _job[id_job].get("stop"):
                _job[id_job].update(stato="interrotto")
            else:
                # Avviso se ANAC risultava irraggiungibile (tutti i CIG falliti):
                # il file c'e' comunque, ma le colonne ANAC saranno vuote.
                avviso = None
                if esito and esito.get("anac_giu"):
                    avviso = ("Il servizio ANAC non era raggiungibile: la tabella "
                              "e' stata generata, ma le colonne con i dati ANAC "
                              "(oggetto, CUP, CPV, aggiudicatario) risultano vuote. "
                              "Riprova piu' tardi per avere i dati completi.")
                _job[id_job].update(stato="finito", file=nome_file, avviso=avviso)
    except Exception as e:
        with _lock:
            _job[id_job].update(stato="errore", errore=str(e))


@app.route("/")
def home():
    anno_corrente = datetime.now().year
    return render_template(
        "index.html",
        stati=list(MAPPA_STATO.keys()),
        tipologie=list(MAPPA_TIPOLOGIA.keys()),
        contraenti=list(MAPPA_CONTRAENTE.keys()),
        giorni=[f"{g:02d}" for g in range(1, 32)],
        mesi=[f"{m:02d}" for m in range(1, 13)],
        anni=[str(a) for a in range(2010, anno_corrente + 1)],
        anno_corrente=anno_corrente,
    )


@app.route("/avvia", methods=["POST"])
def avvia():
    d = request.get_json(force=True)
    data_inizio = (d.get("data_inizio") or "").strip() or None
    data_fine = (d.get("data_fine") or "").strip() or None

    # Validazione lato SERVER: quella nel browser si puo' aggirare, quindi i
    # controlli vanno rifatti qui. Una data non valida (es. 31/02) o un nome
    # file con caratteri proibiti fermano la richiesta con un messaggio, senza
    # avviare la ricerca.
    errori = []
    if data_inizio:
        data_inizio = _iso(data_inizio)
        if data_inizio is None:
            errori.append("La data di inizio non e' una data valida.")
    if data_fine:
        data_fine = _iso(data_fine)
        if data_fine is None:
            errori.append("La data di fine non e' una data valida.")
    if data_inizio and data_fine and data_fine < data_inizio:
        errori.append("La data di fine precede quella di inizio.")

    nome_file = (d.get("nome_file") or "").strip()
    vietati = set('/\\:*?"<>|')
    if nome_file and (set(nome_file) & vietati):
        trovati = " ".join(sorted(set(nome_file) & vietati))
        errori.append(f"Il nome del file contiene caratteri non ammessi: {trovati}")

    # P.IVA (11 cifre) o codice fiscale (16 caratteri alfanumerici): stesso
    # criterio usato altrove nel progetto. Si controlla solo se compilato.
    piva = (d.get("piva_invitato") or "").strip()
    if piva:
        pulito = re.sub(r'[\s.\-/]', '', piva).upper()
        if pulito.startswith("IT") and len(pulito) > 2:
            pulito = pulito[2:]
        if len(pulito) not in (11, 16):
            errori.append("La P.IVA deve avere 11 cifre, il codice fiscale 16 caratteri.")

    if errori:
        return jsonify(errore=" ".join(errori)), 400

    filtri = {
        "parola_chiave": (d.get("parola_chiave") or "").strip(),
        "cig": (d.get("cig") or "").strip(),
        "stato": d.get("stato") or "Qualsiasi",
        "tipologia": d.get("tipologia") or "Qualsiasi",
        "contraente": d.get("contraente") or "Qualsiasi",
        "data_limite": data_inizio,
        "data_fine": data_fine,
        "piva_invitato": (d.get("piva_invitato") or "").strip() or None,
        "nome_file": nome_file,
    }

    # Verifica preventiva: se ANAC non risponde, si avvisa subito senza avviare
    # la scansione (che durerebbe minuti per poi dare colonne ANAC vuote).
    # Verifica preventiva ANAC. Se non risponde, NON si blocca: si avvisa
    # l'utente e lo si lascia decidere. Alla prima richiesta, se ANAC e' giu' e
    # l'utente non ha ancora confermato, si restituisce l'avviso; se ha
    # confermato (salta_anac), la ricerca parte comunque, senza dati ANAC.
    salta_anac = bool(d.get("salta_anac"))
    if not salta_anac and not anac_raggiungibile():
        return jsonify(
            anac_giu=True,
            avviso=("AVVISO: i server ANAC al momento non sono raggiungibili. "
                    "Puoi procedere comunque, ma l'estrazione sara' priva dei "
                    "dati ANAC (oggetto, CUP, CPV, aggiudicatario).")
        ), 200

    # Nuova ricerca: si svuota la cartella dai file delle ricerche precedenti.
    # Cosi' l'ultimo file resta scaricabile piu' volte finche' non se ne avvia
    # un'altra, ma la cartella non accumula file vecchi all'infinito.
    _svuota_output()

    id_job = uuid.uuid4().hex
    with _lock:
        _job[id_job] = {"stato": "in_corso", "file": None, "errore": None,
                        "stop": False, "senza_anac": salta_anac,
                        "fatti": 0, "totale": 0}
    threading.Thread(target=_esegui_ricerca, args=(id_job, filtri), daemon=True).start()
    return jsonify(id_job=id_job)


@app.route("/interrompi/<id_job>", methods=["POST"])
def interrompi(id_job):
    # Alza la bandiera di stop: il thread la controlla all'inizio di ogni bando
    # e esce ordinatamente. Non ferma nulla di colpo.
    with _lock:
        if id_job in _job:
            _job[id_job]["stop"] = True
            return jsonify(ok=True)
    return jsonify(ok=False), 404


@app.route("/stato/<id_job>")
def stato(id_job):
    with _lock:
        s = _job.get(id_job)
    if not s:
        abort(404)
    return jsonify(s)


@app.route("/scarica/<nome>")
def scarica(nome):
    return send_from_directory(CARTELLA_OUTPUT, nome, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)