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
                         costruisci_lista_cig, cig_compatibile, invitato_con_piva,
                         normalizza_piva)
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
# HELPER DI STAMPA (usati dall'orchestrazione)
# =====================================================================
def _stampa_operatore(op, indent="            ", numero=None):
    """Stampa un operatore sia se è un dict {"nome":..,"piva":..,"cf":..} sia se è una stringa."""
    prefisso = f"{numero}. " if numero is not None else "* "
    if isinstance(op, dict):
        _pv = op.get("piva", "Non presente")
        _cf = op.get("cf", "Non presente")
        # Il C.F. si mostra solo quando AGGIUNGE informazione: se coincide con
        # la P.IVA (etichetta unica "CF/P.IVA") ripeterlo appesantirebbe il log.
        # Resta invece essenziale per i professionisti persone fisiche, il cui
        # C.F. e' alfanumerico, e per le imprese con i due codici distinti.
        _codici = []
        if _pv != "Non presente":
            _codici.append(f"P.IVA: {_pv}")
        if _cf != "Non presente" and _cf != _pv:
            _codici.append(f"C.F.: {_cf}")
        if _codici:
            print(f"{indent}{prefisso}{op['nome']} ({', '.join(_codici)})")
        else:
            print(f"{indent}{prefisso}{op['nome']}")
    else:
        print(f"{indent}{prefisso}{op}")


def _stampa_lista_operatori(operatori, dichiarati, etichetta, piva_cercata=None):
    """
    Stampa una lista di operatori (manifestanti o invitati).

    Quando e' attiva la ricerca per operatore, elencare tutti gli invitati
    seppellirebbe l'unica riga che interessa sotto centinaia di nomi (i bandi
    piu' grandi ne hanno oltre 300): si stampa allora il solo operatore
    cercato, indicando comunque quanti erano in totale. La lista COMPLETA
    resta nei dati e finira' regolarmente nel salvataggio: qui cambia solo
    cio' che si vede a schermo.
    """
    print(f"        -> [PDF] {etichetta}: {dichiarati}")
    if not piva_cercata:
        for j, op in enumerate(operatori or [], 1):
            _stampa_operatore(op, numero=j)
        return
    cercata = normalizza_piva(piva_cercata)
    trovati = [op for op in (operatori or [])
               if isinstance(op, dict)
               and cercata in (normalizza_piva(op.get("piva")), normalizza_piva(op.get("cf")))]
    for op in trovati:
        _stampa_operatore(op)
    if not trovati:
        print(f"            (operatore cercato non presente in questa lista)")
    elif len(operatori or []) > len(trovati):
        print(f"            ... e altri {len(operatori) - len(trovati)} operatori non mostrati")


# =====================================================================
# ORCHESTRAZIONE DELLA RICERCA
# Spostata qui da main.py: e' il cuore che coordina i moduli di logica.
# =====================================================================
def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, data_fine=None, piva_invitato=None, nome_file=None, deve_fermarsi=None, segnala_progresso=None):
    codice_stato = MAPPA_STATO[stato]
    codice_tipologia = MAPPA_TIPOLOGIA[tipologia]
    codice_contraente = MAPPA_CONTRAENTE[contraente]

    filtri_attivi = []
    if parola_chiave: filtri_attivi.append(f"Oggetto/Parola chiave: '{parola_chiave}'")
    if cig: filtri_attivi.append(f"CIG: '{cig}'")
    if stato != "Qualsiasi": filtri_attivi.append(f"Stato: '{stato}' (Codice: {codice_stato})")
    if tipologia != "Qualsiasi": filtri_attivi.append(f"Tipologia: '{tipologia}' (Codice: {codice_tipologia})")
    if contraente != "Qualsiasi": filtri_attivi.append(f"Scelta Contraente: '{contraente}' (Codice: {codice_contraente})")
    if piva_invitato: filtri_attivi.append(f"Solo bandi con invitato P.IVA/C.F.: '{piva_invitato}'")
    if data_limite and data_fine: filtri_attivi.append(f"Pubblicati dal {data_limite} al {data_fine}")
    elif data_limite: filtri_attivi.append(f"Pubblicati dal: '{data_limite}'")
    elif data_fine: filtri_attivi.append(f"Pubblicati fino al: '{data_fine}'")

    print("\n[+] Avvio ricerca sul sito...")
    if filtri_attivi:
        print("  Filtri applicati:")
        for f in filtri_attivi: print(f"    -> {f}")
    else:
        print("  Nessun filtro specifico inserito (mostro tutti i bandi)")

    url_ricerca = genera_url_con_filtri(
        parola_chiave=parola_chiave, cig=cig, stato=codice_stato,
        tipologia=codice_tipologia, contraente=codice_contraente
    )

    elenco_link = estrai_lista_bandi(url_ricerca, data_limite=data_limite, data_fine=data_fine)

    print(f"\n[+] Trovati {len(elenco_link)} bandi corrispondenti ai filtri e alle date.")
    print("[+] Avvio estrazione dettagli dalle singole pagine...\n")

    lista_risultati = []
    # Grafie sotto cui l'operatore cercato e' gia' stato visto CON il suo
    # codice: si costruiscono man mano, dai PDF letti durante questa stessa
    # scansione. Servono a riconoscerlo anche nei bandi che elencano gli
    # invitati senza alcun identificativo.
    # Conteggio dei bandi in cui l'operatore cercato e' stato trovato.
    _trovati_1a = {}
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
            print(f"\n[!] Ricerca interrotta dall'utente dopo {i - 1} bandi. Nessun file prodotto.")
            return
        # Avanzamento: annuncia il bando che si sta elaborando ORA ("bando i di
        # N"). Si segnala all'inizio, cosi' la barra dice cosa e' in corso: parte
        # dal primo e arriva all'ultimo mentre lo elabora. In terminale e' inerte.
        if segnala_progresso is not None:
            segnala_progresso(i, len(elenco_link))
        if i > 1:
            time.sleep(2)

        url_completo = f"{BASE_URL}{link}" if not link.startswith("http") else link
        print(f"[{i}] Analizzo: {url_completo}")

        dati_bando = estrai_dettagli_bando(url_completo)

        print(f"    -> Tipologia Gara: {dati_bando['tipologia']}")
        print(f"    -> Scelta Contraente: {dati_bando['scelta_contraente']}")
        print(f"    -> Ente/Comune: {dati_bando['enti']}")
        print(f"    -> Pubblicato il: {dati_bando['data_pubblicazione']}")
        print(f"    -> Scadenza Manif. Interesse: {dati_bando['scadenza_manifestazione']}")
        print(f"    -> Scadenza Gara: {dati_bando['data_scadenza']}")

        lista_cig = dati_bando.get("cig_list", [])
        # Alfabeto italiano completo (21 lettere, senza J K W X Y) — fix bando con 13 CIG (IndexError)
        lettere_lotti = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'L', 'M', 'N',
                         'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'Z']

        print(f"    [..] Ricerca PDF esito...")
        lista_pdf = estrai_link_pdf_esito(url_completo)
        dati_pdf_comuni = None

        # — FILTRO PER P.IVA DELL'INVITATO —
        # La P.IVA degli invitati esiste SOLO dentro i PDF: non e' un filtro
        # del sito ne' un dato ANAC, quindi i PDF vanno comunque scaricati e
        # letti. Il controllo si fa pero' QUI, prima di stampare e soprattutto
        # prima di interrogare ANAC: per i bandi che non interessano si
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
                print(f"    [~] Operatore {piva_invitato} non presente fra gli invitati: bando saltato.\n")
                continue
            _trovati_1a["piva"] = _trovati_1a.get("piva", 0) + 1
            print(f"    [+] TROVATO fra gli invitati: {_match['nome']}"
                  f" (P.IVA: {_match.get('piva', 'Non presente')}"
                  + (f", C.F.: {_match['cf']}" if _match.get('cf', 'Non presente') not in
                     ('Non presente', _match.get('piva')) else "") + ")")

        if lista_pdf:
            print(f"    [+] PDF trovati: {len(lista_pdf)}")
            for pdf_url in lista_pdf:
                print(f"        -> {pdf_url}")
            if len(lista_pdf) == 1:
                # UN solo PDF: manifestanti/invitati sono davvero comuni alla gara
                # (anche nei multi-lotto interni, dove i lotti dividono solo le offerte)
                dati_pdf_comuni = estrai_dati_pdf_esito(lista_pdf[0])

                # Manifestanti comuni (saltati se i lotti hanno i PROPRI:
                # verranno stampati dentro ogni sezione [CIG: ...])
                _manif_nei_lotti = any(l.get("num_manifestanti", "Non presente") != "Non presente"
                                       for l in dati_pdf_comuni.get("lotti", []))
                if dati_pdf_comuni["num_operatori_manifestanti"] != "Non presente" and not _manif_nei_lotti:
                    _stampa_lista_operatori(dati_pdf_comuni["operatori_manifestanti"], dati_pdf_comuni['num_operatori_manifestanti'],
                                            "Operatori manifestanti", piva_invitato)

                # Invitati comuni (saltati se propagati ai lotti: verranno
                # stampati dentro ogni sezione [CIG: ...], senza duplicare)
                _inv_nei_lotti = any(l.get("num_invitati", "Non presente") != "Non presente"
                                     for l in dati_pdf_comuni.get("lotti", []))
                if dati_pdf_comuni["num_operatori_invitati"] != "Non presente" and not _inv_nei_lotti:
                    _stampa_lista_operatori(dati_pdf_comuni["operatori_invitati"], dati_pdf_comuni['num_operatori_invitati'],
                                            "Operatori invitati", piva_invitato)
            else:
                # PIU' PDF (un PDF per lotto): ogni PDF ha i SUOI manifestanti e
                # invitati (es. gara SP17/SP24: 134 nel Lotto A, 136 nel Lotto B),
                # quindi niente blocco comune: si stampano dentro ogni sezione
                # [CIG: ...] dal PDF agganciato a quel CIG. Si evita anche
                # un'estrazione doppia del primo PDF (ci pensa la cache del loop).
                print(f"        -> Manifestanti, invitati e offerte stampati per lotto (un PDF per lotto)")
        else:
            print(f"    -> Nessun PDF esito trovato.")

        if not lista_cig:
            print("    -> CIG: Non trovato")
            dati_pdf = {}
            if lista_pdf:
                dati_pdf = estrai_dati_pdf_esito(lista_pdf[0], lotto_corrente=None)
                # Il CIG puo' mancare in pagina ma esserci nel PDF: stamparlo qui
                # lo recupera comunque (utile per identificare la gara)
                print(f"        -> [PDF] CIG dichiarato nel PDF: {dati_pdf.get('cig_pdf', 'Non presente')}")
                for lotto in dati_pdf["lotti"]:
                    # Etichetta del lotto: senza questa, con piu' lotti i blocchi
                    # stampati di seguito non erano attribuibili (Esito-205/208,
                    # 9 lotti, gara senza alcun CIG ne' in pagina ne' nel PDF).
                    if lotto.get("nome_lotto"):
                        print(f"        -> [PDF] {lotto['nome_lotto']}:")
                    if lotto.get("cig_lotto", "Non presente") != "Non presente":
                        print(f"        -> [PDF] CIG del lotto: {lotto['cig_lotto']}")

                    # Manifestanti e invitati PER LOTTO: il ramo senza CIG non li
                    # stampava affatto (li stampava solo il ramo con CIG, riga ~392),
                    # per cui nei multi-lotto sembravano mancanti pur essendo estratti.
                    if lotto.get("num_manifestanti", "Non presente") != "Non presente":
                        _stampa_lista_operatori(lotto.get("manifestanti", []), lotto['num_manifestanti'],
                                                "Manifestanti", piva_invitato)

                    if lotto.get("num_invitati", "Non presente") != "Non presente":
                        _stampa_lista_operatori(lotto.get("invitati", []), lotto['num_invitati'],
                                                "Invitati", piva_invitato)

                    if lotto["num_offerte_ricevute"] != "Non presente":
                        print(f"        -> [PDF] Offerte ricevute: {lotto['num_offerte_ricevute']}")
                        for j, o in enumerate(lotto["offerte_ricevute"], 1):
                            print(f"            {j}. {o}")
                    if lotto["num_offerte_ammesse"] != "Non presente":
                        print(f"        -> [PDF] Offerte ammesse: {lotto['num_offerte_ammesse']}")
                        for j, o in enumerate(lotto.get("offerte_ammesse", []), 1):
                            print(f"            {j}. {o}")
                    if lotto["aggiudicatario_pdf"] != "Non presente":
                        print(f"        -> [PDF] Aggiudicatario: {lotto['aggiudicatario_pdf']}")
                    if lotto["aggiudicatario_piva"] != "Non presente":
                        print(f"        -> [PDF] P.IVA: {lotto['aggiudicatario_piva']}")
                    # C.F. mostrato solo se aggiunge informazione (diverso dalla P.IVA)
                    if (lotto.get("aggiudicatario_cf", "Non presente") != "Non presente"
                            and lotto["aggiudicatario_cf"] != lotto.get("aggiudicatario_piva")):
                        print(f"        -> [PDF] C.F.: {lotto['aggiudicatario_cf']}")
                    if lotto["ribasso"] != "Non presente":
                        print(f"        -> [PDF] Ribasso: {lotto['ribasso']}")
                    if lotto["valore_offerta"] != "Non presente":
                        print(f"        -> [PDF] Valore offerta: {lotto['valore_offerta']}")

            # Risoluzione del CIG (pagina -> PDF -> "Non trovato"): la logica
            # sta in scraper_pdf.risolvi_cig, riusabile da qualunque frontend.
            cig_effettivo = risolvi_cig(None, dati_pdf)
            dati_anac = {}
            if cig_effettivo != "Non trovato":
                print(f"        -> CIG recuperato dal PDF, usato come CIG della gara: {cig_effettivo}")
                # ANAC con il CIG recuperato dal PDF: stessa struttura del blocco
                # (oggi commentato) del loop multi-CIG, cosi' alla riattivazione
                # di quello i due rami restano gemelli.
                print(f"    [..] Recupero dati ANAC per CIG {cig_effettivo}...")
                contatore_anac_tentati += 1
                json_anac = scarica_json_anac(cig_effettivo)
                if json_anac:
                    dati_anac = estrai_dati_json_anac(json_anac)
                    print(f"        -> [ANAC] Numero Gara: {dati_anac['numero_gara']}")
                    print(f"        -> [ANAC] Oggetto Gara: {dati_anac['oggetto_gara']}")
                    print(f"        -> [ANAC] CUP: {dati_anac['cup']}")
                    print(f"        -> [ANAC] CPV: {dati_anac['cod_cpv']} - {dati_anac['descrizione_cpv']}")
                    print(f"        -> [ANAC] Tipo Scelta Contraente: {dati_anac['tipo_scelta_contraente']}")
                    print(f"        -> [ANAC] Aggiudicatario: {dati_anac['aggiudicatario']} (CF: {dati_anac['aggiudicatario_cf']})")
                else:
                    print("        -> [ANAC] Impossibile recuperare i dati.")
                    contatore_falliti += 1
            lista_risultati.append({
                "provincia": dati_bando,
                "anac": dati_anac,
                "cig_corrente": cig_effettivo,
                "pdf": dati_pdf
            })
        else:
            print(f"    -> CIG trovati: {len(lista_cig)} -> {', '.join(lista_cig)}")

            # Cache delle estrazioni PDF: con l'aggancio CIG->PDF per contenuto
            # ogni PDF puo' dover essere letto per capire a quale CIG appartiene;
            # la cache evita di scaricare/estrarre due volte lo stesso PDF quando
            # si itera su piu' CIG della stessa gara.
            _cache_pdf = {}

            # IL PDF COMANDA, LA PAGINA INTEGRA: la lista dei CIG da processare
            # viene ricostruita dai CIG dichiarati nei PDF (per-lotto o testata),
            # cosi' TUTTI i lotti escono anche se la pagina espone CIG monchi o
            # mancanti; la lista di pagina resta il fallback per i PDF muti e i
            # suoi CIG non riscontrati nei PDF vengono solo segnalati.
            _cig_pagina = list(lista_cig)
            lista_cig, _cig_non_riscontrati, _cig_integrati, _cig_scartati, _cig_divergenti = costruisci_lista_cig(
                _cig_pagina, lista_pdf, cache=_cache_pdf, con_divergenti=True)
            if lista_cig != _cig_pagina:
                print(f"    -> CIG effettivi (pagina + PDF): {len(lista_cig)} -> {', '.join(lista_cig)}")
            for _c in _cig_integrati:
                print(f"    [+] CIG integrato dal PDF (assente in pagina): {_c}")
            for _c in _cig_non_riscontrati:
                print(f"    [!] CIG di pagina non riscontrato nei PDF (possibile refuso o incoerenza): {_c}")
            for _c in _cig_scartati:
                print(f"    [!] CIG di pagina NON VALIDO ({len(_c)} caratteri, nessun PDF lo completa): {_c} — scartato")
            # Gara mono-lotto con UN solo PDF che dichiara un CIG DIVERSO da
            # quello di pagina: non e' un lotto in piu' da processare, e'
            # un'incoerenza fra le due fonti. Si usa quello di pagina (valido
            # per ANAC) e si segnala l'altro, senza iterare due volte.
            for _c in _cig_divergenti:
                print(f"    [!] Il PDF dichiara {_c}, diverso dal CIG di pagina "
                      f"{', '.join(_cig_pagina) if _cig_pagina else 'assente'}: "
                      f"si usa quello di pagina (gara mono-lotto)")

            if not lista_cig and lista_pdf:
                # Tutti i CIG di pagina scartati e nessun CIG dichiarato nei PDF:
                # il PDF va processato COMUNQUE — lo scarto vale per ANAC (che ha
                # comunque la guardia sulla lunghezza), NON per l'estrazione dei
                # dati. "N.A." e' il segnaposto storico del progetto per il CIG
                # mancante.
                print("    [!] Nessun CIG valido disponibile: processo comunque il PDF (CIG = N.A., niente ANAC)")
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

                print(f"\n    [CIG: {cig_singolo}]")

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
                    # via cig_lotto, fallback posizionale) cosi' stampa ed Excel
                    # portano i dati giusti — la logica sta in scraper_pdf.
                    _ha_cig_lotto = any(l.get("cig_lotto", "Non presente") != "Non presente"
                                        for l in dati_pdf.get("lotti", []))
                    # Restrizione anche SENZA cig_lotto: se il PDF e' unico e i
                    # suoi lotti sono tanti quanti i CIG di pagina, l'ordine dei
                    # lotti nel PDF corrisponde a quello dei CIG (Esito_F-2/F-3:
                    # 2 CIG in pagina, "Lotto 1 campi sportivi - Lotto 2
                    # palazzetto"). Senza questo ogni CIG stampava TUTTI i lotti,
                    # duplicando l'intero blocco a ogni giro del ciclo.
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

                    # CIG dichiarato in testata del PDF: stampato PRIMA degli altri
                    # dati, cosi' e' subito verificabile a occhio l'aggancio CIG->PDF.
                    # (Nel multi_lotto_std lo sostituisce il CIG del lotto, stampato
                    # nel blocco del lotto: quello di testata sarebbe sempre il primo.)
                    _cig_pdf = dati_pdf.get("cig_pdf", "Non presente")
                    if not _ha_cig_lotto:
                        print(f"        -> [PDF] CIG dichiarato nel PDF: {_cig_pdf}")
                    if (len(lista_pdf) > 1 and _cig_pdf != "Non presente"
                            and _cig_pdf.upper() != cig_singolo.upper()):
                        # Puo' accadere solo col fallback posizionale: il PDF preso
                        # per indice dichiara un CIG DIVERSO da quello cercato.
                        print(f"        [!] ATTENZIONE: il PDF dichiara {_cig_pdf}, "
                              f"diverso dal CIG cercato {cig_singolo} (aggancio posizionale)")

                    if len(lista_pdf) > 1:
                        # Un PDF per lotto: manifestanti e invitati appartengono a
                        # QUESTO lotto e si stampano nella sua sezione [CIG: ...]
                        if dati_pdf.get("num_operatori_manifestanti", "Non presente") != "Non presente":
                            _stampa_lista_operatori(dati_pdf["operatori_manifestanti"], dati_pdf['num_operatori_manifestanti'],
                                                    "Operatori manifestanti", piva_invitato)
                        if dati_pdf.get("num_operatori_invitati", "Non presente") != "Non presente":
                            _stampa_lista_operatori(dati_pdf["operatori_invitati"], dati_pdf['num_operatori_invitati'],
                                                    "Operatori invitati", piva_invitato)

                    for lotto in dati_pdf["lotti"]:
                        if lotto["nome_lotto"]:
                            print(f"        -> [PDF] {lotto['nome_lotto']}:")
                        if lotto.get("cig_lotto", "Non presente") != "Non presente":
                            print(f"        -> [PDF] CIG del lotto: {lotto['cig_lotto']}")

                        # Manifestanti per lotto
                        if "num_manifestanti" in lotto and lotto["num_manifestanti"] != "Non presente":
                            _stampa_lista_operatori(lotto.get("manifestanti", []), lotto["num_manifestanti"],
                                                    "Manifestanti", piva_invitato)

                        # Invitati per lotto
                        if "num_invitati" in lotto and lotto["num_invitati"] != "Non presente":
                            _stampa_lista_operatori(lotto.get("invitati", []), lotto["num_invitati"],
                                                    "Invitati", piva_invitato)

                        if piva_invitato:
                            continue
                        if lotto["num_offerte_ricevute"] != "Non presente":
                            print(f"        -> [PDF] Offerte ricevute: {lotto['num_offerte_ricevute']}")
                            for j, o in enumerate(lotto["offerte_ricevute"], 1):
                                print(f"            {j}. {o}")
                        if lotto["num_offerte_ammesse"] != "Non presente":
                            print(f"        -> [PDF] Offerte ammesse: {lotto['num_offerte_ammesse']}")
                            for j, o in enumerate(lotto.get("offerte_ammesse", []), 1):
                                print(f"            {j}. {o}")
                        if lotto["num_offerte_escluse"] != "Non presente":
                            print(f"        -> [PDF] Offerte escluse: {lotto['num_offerte_escluse']}")
                        if lotto["aggiudicatario_pdf"] != "Non presente":
                            print(f"        -> [PDF] Aggiudicatario: {lotto['aggiudicatario_pdf']}")
                        if lotto["aggiudicatario_piva"] != "Non presente":
                            print(f"        -> [PDF] P.IVA: {lotto['aggiudicatario_piva']}")
                        if (lotto.get("aggiudicatario_cf", "Non presente") != "Non presente"
                                and lotto["aggiudicatario_cf"] != lotto.get("aggiudicatario_piva")):
                            print(f"        -> [PDF] C.F.: {lotto['aggiudicatario_cf']}")
                        if lotto["ribasso"] != "Non presente":
                            print(f"        -> [PDF] Ribasso: {lotto['ribasso']}")
                        if lotto["valore_offerta"] != "Non presente":
                            print(f"        -> [PDF] Valore offerta: {lotto['valore_offerta']}")

                # Dati ANAC
                dati_anac = {}
                if cig_singolo.upper() == "N.A.":
                    print(f"    -> CIG non disponibile, dati ANAC non recuperabili.")
                    contatore_falliti += 1
                else:
                    print(f"    [..] Recupero dati ANAC per CIG {cig_singolo}...")
                    contatore_anac_tentati += 1
                    json_anac = scarica_json_anac(cig_singolo)
                    if json_anac:
                        dati_anac = estrai_dati_json_anac(json_anac)
                        print(f"        -> [ANAC] Numero Gara: {dati_anac['numero_gara']}")
                        print(f"        -> [ANAC] Oggetto Gara: {dati_anac['oggetto_gara']}")
                        print(f"        -> [ANAC] CUP: {dati_anac['cup']}")
                        print(f"        -> [ANAC] CPV: {dati_anac['cod_cpv']} - {dati_anac['descrizione_cpv']}")
                        print(f"        -> [ANAC] Tipo Scelta Contraente: {dati_anac['tipo_scelta_contraente']}")
                        print(f"        -> [ANAC] Aggiudicatario: {dati_anac['aggiudicatario']} (CF: {dati_anac['aggiudicatario_cf']})")
                    else:
                        print("        -> [ANAC] Impossibile recuperare i dati.")
                        contatore_falliti += 1

                lista_risultati.append({
                    "provincia": dati_bando,
                    "anac": dati_anac,
                    "cig_corrente": cig_singolo,
                    "pdf": dati_pdf
                })

                time.sleep(2)

        print("-" * 60)

    # — RIEPILOGO DELLA RICERCA PER OPERATORE —
    if piva_invitato:
        _n_piva = _trovati_1a.get("piva", 0)
        print("\n" + "=" * 60)
        print(f"[=] RICERCA OPERATORE {piva_invitato} — RIEPILOGO")
        print(f"    bandi analizzati     : {len(elenco_link)}")
        print(f"    TROVATI              : {_n_piva}")
        print(f"    senza corrispondenza : {len(elenco_link) - _n_piva}")
        print("    NOTA: il riconoscimento avviene solo sul codice dichiarato nel PDF.")
        print("          I bandi che elencano gli invitati senza P.IVA ne' C.F. non")
        print("          possono essere ricondotti a un operatore e restano esclusi.")
        print("=" * 60)

    if lista_risultati:
        salva_in_excel(lista_risultati, nome_file=nome_file, piva_invitato=piva_invitato)
        print(f"\n[!] CIG senza dati ANAC: {contatore_falliti}")

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