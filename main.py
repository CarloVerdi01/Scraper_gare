from pick import pick  # <--- Libreria super leggera per le freccette
from scraper import genera_url_con_filtri, estrai_lista_bandi, BASE_URL, estrai_dati_json_anac, scarica_json_anac, estrai_dettagli_bando
from datetime import datetime
from save_data import salva_in_excel
import time
from scraper_pdf import estrai_dati_pdf_esito, estrai_link_pdf_esito, seleziona_pdf_per_cig, seleziona_lotto_per_cig, risolvi_cig, costruisci_lista_cig, cig_compatibile, invitato_con_piva, normalizza_piva

import console
# Versione da terminale: e' il file usato per il debug, quindi accende i
# messaggi diagnostici dei moduli condivisi (scraper, scraper_pdf,
# save_data). gui.py e app.py non lo fanno e restano silenziosi.
console.VERBOSE = True


# =====================================================================
# DIZIONARI DI TRADUZIONE E VINCOLO (I FILTRI REALI DEL SITO)
# =====================================================================
MAPPA_STATO = {
    "qualsiasi": "All",
    "aperta": "AP",
    "aggiudicata": "AG",
    "deserta": "DE",
    "non aggiudicata": "NA",
    "revocata": "RE",
    "sospesa": "SO",
    "chiusa": "CH"
}

MAPPA_TIPOLOGIA = {
    "qualsiasi": "All",
    "alienazioni": "159",
    "asta pubblica": "154",
    "appalto di forniture": "144",
    "appalto di lavori": "145",
    "appalto di servizi": "146",
    "concessione di lavori": "148",
    "concessione di servizi": "147",
    "incarichi professionali": "158"
}

MAPPA_CONTRAENTE = {
    "qualsiasi": "All",
    "procedura aperta": "113",
    "procedura ristretta": "114",
    "adesione ad accordo quadro/convenzione": "771",
    "procedura negoziata art. 50 d. lgs. 36/2023": "899",
    "procedura negoziata art. 36 d. lgs. 50/2016": "126",
    "previa manifestazione di interesse": "128",
    "previa consultazione albo fornitori": "129",
    "affidamento diretto": "130",
    "affidamento attraverso mepa": "131",
    "rdo - richiesta di offerta": "132",
    "oda - ordine diretto d'acquisto": "133",
    "trattativa diretta": "134",
    "procedura negoziata senza previa pubblicazione": "127",
    "altre procedure": "115",
    "asta pubblica": "139",
    "dialogo competitivo": "137",
    "partenariato per l'innovazione": "138",
    "procedura competitiva con negoziazione": "135",
    "project financing": "140",
    "somma urgenza": "157"
}


# =====================================================================
# FUNZIONE DI SELEZIONE CON LE FRECCETTE
# =====================================================================
def selezione_filtri(nome_filtro, dizionario_mappa):
    """
    Mostra un menu interattivo nativo. L'utente si muove con ↑ e ↓
    e conferma premendo INVIO.
    """
    opzioni_visibili = [chiave.title() for chiave in dizionario_mappa.keys()]
    titolo_menu = f"\nSeleziona {nome_filtro.upper()} (Usa le freccette ↑ ↓ e premi INVIO):"
    opzione_scelta, indice = pick(opzioni_visibili, titolo_menu, indicator="=>")
    print(f"-> {nome_filtro.title()} selezionato: {opzione_scelta}")
    return opzione_scelta.lower()


# =====================================================================
# HELPER: stampa un operatore (manifestante o invitato) da dict o stringa
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




# =====================================================================
# FUNZIONE DI RICHIESTA DATA COMPATIBILE ED INTERATTIVA
# =====================================================================
def _chiedi_data(etichetta, obbligatoria=True):
    """
    Chiede una data nel formato gg/mm/aaaa e la restituisce come 'YYYY-MM-DD'.
    Se obbligatoria=False, l'Invio a vuoto vale come "nessuna data" e torna None.
    """
    anno_corrente = datetime.now().year
    suffisso = "" if obbligatoria else " (INVIO per saltare)"
    while True:
        testo = input(f"  {etichetta} [gg/mm/aaaa]{suffisso}: ").strip()
        if not testo and not obbligatoria:
            return None
        try:
            data = datetime.strptime(testo, '%d/%m/%Y')
        except ValueError:
            print("  [-] Formato non valido. Esempio corretto: 15/03/2024\n")
            continue
        if data.year < 2010 or data.year > anno_corrente:
            print(f"  [-] Anno fuori intervallo: deve essere tra il 2010 e il {anno_corrente}.\n")
            continue
        return data.strftime('%Y-%m-%d')


def richiedi_data_limite():
    """
    Chiede all'utente se vuole filtrare per data di pubblicazione.

    Ritorna la coppia (data_inizio, data_fine) in formato 'YYYY-MM-DD', con
    None dove il filtro non e' stato impostato. La data di fine e' facoltativa:
    lasciandola vuota si ottiene il comportamento storico "dal giorno X in poi".
    """
    risposta = input("\nVuoi filtrare i bandi in base alla data di pubblicazione? (s/n): ").strip().lower()
    if risposta != 's':
        print("-> Nessun filtro data applicato.")
        return None, None

    print("\nIndica il periodo di pubblicazione dei bandi da estrarre:")
    while True:
        data_inizio = _chiedi_data("Data di INIZIO", obbligatoria=True)
        data_fine = _chiedi_data("Data di FINE", obbligatoria=False)
        # La fine non puo' precedere l'inizio: sarebbe un intervallo vuoto e
        # la ricerca non restituirebbe alcun bando.
        if data_fine and data_fine < data_inizio:
            print("  [-] La data di fine precede quella di inizio: intervallo non valido. Riprova.\n")
            continue
        break

    _fmt = lambda d: datetime.strptime(d, '%Y-%m-%d').strftime('%d/%m/%Y')
    if data_fine:
        print(f"-> Filtro data applicato: bandi pubblicati dal {_fmt(data_inizio)} al {_fmt(data_fine)}.")
    else:
        print(f"-> Filtro data applicato: bandi pubblicati dal {_fmt(data_inizio)} in poi.")
    return data_inizio, data_fine


def richiedi_piva_invitato():
    """
    Chiede se filtrare i bandi per un operatore invitato.

    Ritorna il codice inserito (P.IVA o codice fiscale) oppure None. Il
    confronto a valle e' tollerante su spazi, punti e prefisso "IT", quindi
    qui basta una validazione minima sulla lunghezza.
    """
    risposta = input("\nVuoi cercare i bandi in cui e' stato invitato un operatore specifico? (s/n): ").strip().lower()
    if risposta != 's':
        print("-> Nessun filtro sull'operatore invitato.")
        return None

    while True:
        codice = input("  Inserisci la P.IVA o il codice fiscale dell'operatore: ").strip()
        pulito = normalizza_piva(codice)
        if not pulito:
            print("  [-] Nessun codice inserito. Riprova.\n")
            continue
        # P.IVA: 11 cifre; C.F. di persona fisica: 16 caratteri alfanumerici
        if len(pulito) not in (11, 16):
            print(f"  [-] Codice di {len(pulito)} caratteri: una P.IVA ne ha 11, "
                  f"un codice fiscale 16. Riprova.\n")
            continue
        print(f"-> Verranno estratti solo i bandi che hanno {pulito} fra gli INVITATI.")
        print("   (i PDF vanno comunque letti tutti: la P.IVA non e' un filtro del sito)")
        return pulito


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
# METODO DI CONTROLLO E AVVIO RICERCA
# =====================================================================
def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, data_fine=None, piva_invitato=None, nome_file=None):
    codice_stato = MAPPA_STATO[stato]
    codice_tipologia = MAPPA_TIPOLOGIA[tipologia]
    codice_contraente = MAPPA_CONTRAENTE[contraente]

    filtri_attivi = []
    if parola_chiave: filtri_attivi.append(f"Oggetto/Parola chiave: '{parola_chiave}'")
    if cig: filtri_attivi.append(f"CIG: '{cig}'")
    if stato != "qualsiasi": filtri_attivi.append(f"Stato: '{stato}' (Codice: {codice_stato})")
    if tipologia != "qualsiasi": filtri_attivi.append(f"Tipologia: '{tipologia}' (Codice: {codice_tipologia})")
    if contraente != "qualsiasi": filtri_attivi.append(f"Scelta Contraente: '{contraente}' (Codice: {codice_contraente})")
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

    for i, link in enumerate(elenco_link, 1):
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

# =====================================================================
# INTERFACCIA UTENTE PRINCIPALE
# =====================================================================
if __name__ == "__main__":
    print("=========================================")
    print("        SCRAPER PROVINCIA PISTOIA        ")
    print("=========================================")
    print("Compila i campi di testo o premi INVIO per saltarli.\n")

    scelta_oggetto = input("Inserisci parola chiave OGGETTO: ").strip()
    scelta_cig = input("Inserisci codice CIG specifico: ").strip()

    scelta_stato = selezione_filtri("Stato Gara", MAPPA_STATO)
    scelta_tipologia = selezione_filtri("Tipologia Gara", MAPPA_TIPOLOGIA)
    scelta_contraente = selezione_filtri("Scelta del Contraente", MAPPA_CONTRAENTE)

    scelta_data_limite, scelta_data_fine = richiedi_data_limite()

    scelta_piva = richiedi_piva_invitato()

    caratteri_vietati = {'/', '\\', ':', '*', '?', '"', '<', '>', '|'}

    while True:
        nome_file = input("\nCome vuoi chiamare il file Excel? (premi INVIO per nome automatico): ").strip()
        if nome_file:
            caratteri_trovati = [c for c in nome_file if c in caratteri_vietati]
            if caratteri_trovati:
                print(f"Errore: il nome contiene caratteri non validi. Caratteri non consentiti: / \\ : * ? \" < > |. Riprova.")
                continue
            if not nome_file.endswith(".xlsx"):
                nome_file += ".xlsx"
        else:
            nome_file = None
        break

    avvia_ricerca_bandi(
        parola_chiave=scelta_oggetto,
        cig=scelta_cig,
        stato=scelta_stato,
        tipologia=scelta_tipologia,
        contraente=scelta_contraente,
        data_limite=scelta_data_limite,
        data_fine=scelta_data_fine,
        piva_invitato=scelta_piva,
        nome_file=nome_file
    )