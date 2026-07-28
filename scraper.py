#introduco il filtro per partita iva
import requests
from bs4 import BeautifulSoup
import urllib.parse  # Serve per gestire correttamente gli spazi e i caratteri speciali nei filtri
import json
import time
import hashlib
import pdfplumber
import io
import re

BASE_URL = "https://www.provincia.pistoia.it"


def genera_url_con_filtri(parola_chiave="", cig="", stato="All", contraente="All", tipologia="All"):
    """
    Questa funzione è il cuore generico: prende i parametri (filtri) che decidi tu
    e costruisce l'URL perfetto per il sito.
    """
    # Puliamo la parola chiave per renderla leggibile da un URL (es. trasforma gli spazi in %20)
    parola_chiave_pulita = urllib.parse.quote_plus(parola_chiave)

    # Genera l'url da usare per andare alla pagina dei bandi con i filtri applicati
    url_finale = (
        f"{BASE_URL}/gare?"
        f"title={parola_chiave_pulita}&"
        f"field_cig_value={cig}&"
        f"field_stato_gara_value={stato}&"
        f"field_scelta_del_contraente_target_id={contraente}&"
        f"field_tipologia_gara_target_id={tipologia}"
    )
    return url_finale


def estrai_lista_bandi(url_con_filtri, data_limite=None, data_fine=None):
    # INIZIALIZZAZIONE
    link_bandi = []  # Lista dei link raccolti da restituire alla fine del metodo
    link_visti = set()  # Insieme per il controllo die duplicati, verificare "x" in link_visti è molto più rapido che "x" in link_bandi quando la lista cresce.
    pagina_corrente = 0  # il sito parte da pag0, poi si scorrono tutte le pagine contenenti bandi
    stop_per_data = False  # Flag per uscire dal ciclo quando si trova un bando con una data troppo vecchia

    # CICLO PRINCIPALE
    while True:
        url_pagina = f"{url_con_filtri}&page={pagina_corrente}"  # scorre le pagine contenenti i bandi partendo da pag0
        print(f"Richiedo pagina {pagina_corrente + 1}: {url_pagina}\n")

        risposta = requests.get(
            url_pagina)  # Verifica la disponibilità/funzionamento del server, se da errore interrompiamo subito
        if risposta.status_code != 200:
            print(f"Errore di connessione. Codice: {risposta.status_code}")
            break

        # Tasforma l'HTML in un oggetto navigabile e cerca la tabella dei risultati
        # se non c'è (nessun bando corrispondente ai filtri), esce
        soup = BeautifulSoup(risposta.text, 'html.parser')
        tabella = soup.find('table')
        if not tabella:
            print("Nessun bando trovato (tabella vuota).")
            break

        righe = tabella.find_all('tr')  # prende tutte le righe della tabella, inclusa l'intestazione
        righe_dati = righe[1:]  # con questo prende tutti gli elementi dalla posizione 1 in poi (salta l'intestazione)

        # CICLO SULLE RIGHE
        for riga in righe_dati:
            celle = riga.find_all('td')  # per ogni riga prende tutte le celle <td>

            # Salta righe vuote o senza abbastanza celle (se ce ne sono meno di 3)
            if len(celle) < 3:
                continue

            # Se è stato impostato un filtro data: cerca il tag <time> nella riga,
            # prende l'attributo datetime (es. "2024-08-29T12:00:00Z"), lo divide su "T" e prende solo la parte data
            # ("2024-08-29"). Confronta le stringhe data in formato YYYY-MM-DD — questo formato si confronta
            # correttamente anche come stringhe ("2024-01-01" < "2024-12-31" è True).
            # Se il bando è più vecchio del limite, imposta stop_per_data = True e fa break
            # — esce dal ciclo for (non dal while), perché i bandi sono ordinati dal più recente al più vecchio
            # , quindi tutti i successivi saranno ancora più vecchi.
            # Le due date NON sono simmetriche, perche' i bandi arrivano
            # ordinati dal piu' RECENTE al piu' vecchio:
            #  - data_limite (inizio, limite INFERIORE): quando si incontra un
            #    bando piu' vecchio si puo' interrompere del tutto, perche' tutti
            #    i successivi saranno ancora piu' vecchi. E' l'ottimizzazione che
            #    evita di scorrere l'intero archivio.
            #  - data_fine (limite SUPERIORE): i bandi piu' recenti vanno solo
            #    SALTATI e si prosegue, perche' quelli nell'intervallo si
            #    trovano piu' avanti nell'elenco.
            if data_limite or data_fine:
                tag_tempo = riga.find('time')
                if tag_tempo and tag_tempo.get('datetime'):
                    data_bando = tag_tempo['datetime'].split('T')[0]
                    if data_limite and data_bando < data_limite:
                        print(f"  [!] Bando del {data_bando} precedente alla data di inizio. Stop.")
                        stop_per_data = True
                        break
                    if data_fine and data_bando > data_fine:
                        continue  # troppo recente: si salta e si va avanti

            link_oggetto = celle[2].find(
                'a')  # terza cella, contiene il titolo/link del bando, cerca il tag <a> al suo interno
            if link_oggetto and link_oggetto.get('href'):  # prende l'href
                href = link_oggetto['href']
                if href not in link_visti:  # se non è gia statp visitato lo aggiunge sia al set che alla lista
                    link_visti.add(href)
                    link_bandi.append(href)

        # CONTROLLO PAGINA SUCCESSIVA
        pulsante_next = soup.find('li',
                                  class_='pager__item--next')  # cerca l'elemento <li> del pulsante per la pag successiva
        ha_pagina_dopo = pulsante_next is not None and 'disabled' not in pulsante_next.get('class', [])
        # ha_pagina_dopo è True solo se il pulsante per andare a pag successiva non ha la classe disabled (disattivato)
        # pulsante_next.get('class', []) restituisce la lista delle classi CSS dell'elemento (o una lista vuota se non ha l'attributo class)

        if stop_per_data or not ha_pagina_dopo:  # se abbiamo trovato un bando troppo vecchio o non ha pagina dopo usciamo dal while
            if not stop_per_data:  # se non abbiamo trovato un bando troppo vecchio
                print(f"  [✓] Ultima pagina raggiunta (pagina {pagina_corrente + 1}).")
            break

        pagina_corrente += 1  # altrimenti incrementiamo il numero di pagina e passiamo alla pagina dopo
        time.sleep(1)

    print(f"\n[+] Totale link raccolti: {len(link_bandi)}")  # stampa il totale di link raccolti
    return link_bandi  # ritorna i link raccolti


'''Passiamo ora allos scraping delle pagine dei singoli bandi'''


def estrai_dettagli_bando(url_bando):
    # Inizializza il dizionario con valori di default
    dati_bando = {
        "url_provincia": url_bando,
        "tipologia": "Non presente",
        "scelta_contraente": "Non presente",
        "enti": "Non presente",
        "cig_list": [],  # lista, serve nel caso un bando abbia più lotti ognuno con un CIG
        "data_pubblicazione": "Non presente",
        "scadenza_manifestazione": "Non presente",
        "data_scadenza": "Non presente"
    }

    try:
        risposta = requests.get(url_bando, timeout=10)  # se il server non risonde entro 10 secondi lancia un'eccezione
        if risposta.status_code != 200:
            print(f"[-] Impossibile accedere: {url_bando}")
            return dati_bando  # se in risposta arriva un codice diverso da 200, ritorna i dati standard

        # Tasforma l'HTML in un oggetto navigabile
        soup = BeautifulSoup(risposta.text, 'html.parser')

        # Per ogni dato che vogliamo estrarre, si usa soup.find() per cercare nell'HTML il <div> che ha la classe CSS
        # specifica corrispondente a quel campo (es. field--name-field-tipologia-gara),
        # e poi si estrae il testo (o l'attributo) contenuto al suo interno.

        # 1. TIPOLOGIA GARA
        div_tipo = soup.find('div', class_='field--name-field-tipologia-gara')
        if div_tipo:  # se non trova niente ritorna None
            dati_bando["tipologia"] = div_tipo.get_text(strip=True)

        # 2. SCELTA DEL CONTRAENTE
        div_contraente = soup.find('div', class_='field--name-field-scelta-del-contraente')
        if div_contraente:
            dati_bando["scelta_contraente"] = div_contraente.get_text(strip=True)

        # 3. CIG (uno o più)
        div_cig = soup.find('div', class_='field--name-field-cig')
        if div_cig:
            for link_cig in div_cig.find_all('a', href=lambda href: href and "dati.anticorruzione.it" in href):
                href_url = link_cig.get('href', '')
                testo_cig = link_cig.get_text(strip=True).upper()

                # 1. Prova a estrarre CIG dall'URL (più affidabile — sempre presente nel path)
                # {9,10}: alcune pagine riportano il CIG TRONCATO a 9 caratteri
                # (refuso di pagina, es. bando Monsummano/Pescia: il Lotto 2 in
                # pagina ha 9 char, il PDF dichiara il CIG pieno B1DE6AA5C5).
                # Il codice monco viene accettato qui e poi agganciato PER
                # PREFISSO al CIG pieno del PDF (scraper_pdf), che da lì in poi
                # è quello usato per risultati, Excel e ANAC.
                match = re.search(r'/(?:cig|smart-cig)/([A-Z0-9]{9,10})\b', href_url.upper())
                if match:
                    cig_valore = match.group(1)
                else:
                    # 2. Cerca nel testo del link — solo se è esattamente 10 caratteri alfanumerici
                    match = re.search(r'\b([A-Z0-9]{9,10})\b', testo_cig)  # {9,10}: v. sopra
                    if match:
                        cig_valore = match.group(1)
                    else:
                        cig_valore = None  # testo non è un CIG valido, skip

                if cig_valore:
                    dati_bando["cig_list"].append(cig_valore)

        # 4. ENTI
        div_enti = soup.find('div', class_='field--name-field-comune')
        if div_enti:
            dati_bando["enti"] = ", ".join(
                div.get_text(strip=True) for div in div_enti.find_all('div') if div.get_text(strip=True)
            )

        # DATA DI PUBBLICAZIONE
        # cerca il div del campo data e dentro cerca il tag time
        div_pubb = soup.find('div', class_='field--name-field-data-di-pubblicazione')
        if div_pubb and div_pubb.find(
                'time'):  # verifica entrambe le condizioni: che il div esista e che contenga un tag time
            dati_bando["data_pubblicazione"] = div_pubb.find('time')['datetime'].split('T')[
                0]  # accede all'attributo datetime del tag,
            # che ha formato "2024-08-29T12:00:00Z". .split('T')[0] divide la stringa sulla lettera "T"
            # e prende il primo pezzo: "2024-08-29".

        # FA LO STESSO PER LE ALTRE DUE DATE

        # SCADENZA MANIFESTAZIONE DI INTERESSE
        div_manif = soup.find('div', class_='field--name-field-data-scadenza-manifestazio')
        if div_manif and div_manif.find('time'):
            dati_bando["scadenza_manifestazione"] = div_manif.find('time')['datetime'].split('T')[0]

        # DATA DI SCADENZA GARA
        div_scad = soup.find('div', class_='field--name-field-data-di-scadenza')
        if div_scad and div_scad.find('time'):
            dati_bando["data_scadenza"] = div_scad.find('time')['datetime'].split('T')[0]

    except Exception as e:  # cattura qualsiasi tipo di errore
        print(f"[-] Errore durante lo scraping del bando {url_bando}: {e}")

    return dati_bando  # ritorna i dati


def estrai_dati_json_anac(
        dati_json):  # Questo meotod riceve un dizionario Python già pronto (JSON convertito) e ne estrae i campi che ci interessano
    # INIZIALIZZAZIONE
    risultato = {
        "numero_gara": "Non presente",
        "oggetto_gara": "Non presente",
        "cod_cpv": "Non presente",
        "descrizione_cpv": "Non presente",
        "cup": "Non presente",
        "tipo_scelta_contraente": "Non presente",
        "aggiudicatario": "Non presente",
        "aggiudicatario_cf": "Non presente"
    }

    if not dati_json:  # se dati_json è None perchè scarica json ha fallito o è vuoto
        return risultato  # si esce restituendo i dati di default

    blocco_bando = dati_json.get("bando",
                                 {})  # cerca la chiave bando nel dizionario principale, se non esiste restituisice un dizionario vuoto
    if blocco_bando:
        # Tre estrazioni dirette
        risultato["numero_gara"] = blocco_bando.get("NUMERO_GARA", "Non presente")
        risultato["oggetto_gara"] = blocco_bando.get("OGGETTO_GARA", "Non presente")
        risultato["tipo_scelta_contraente"] = blocco_bando.get("TIPO_SCELTA_CONTRAENTE", "Non presente")

        lista_cpv = blocco_bando.get("CPV") or []  # CPV è una lista di dizionari perchè un bando potrebbe avere piu CPV
        if lista_cpv:
            risultato["cod_cpv"] = lista_cpv[0].get("COD_CPV", "Non presente")
            risultato["descrizione_cpv"] = lista_cpv[0].get("DESCRIZIONE_CPV", "Non presente")

        lista_cup = blocco_bando.get("CUP") or []
        if lista_cup:
            risultato["cup"] = lista_cup[0].get("CUP", "Non presente")

    # Creiamo due liste distinte
    lista_nomi = []
    lista_cf = []

    # Iteriamo sui partecipanti
    for part in dati_json.get("partecipanti") or []:
        if part.get("FLAG_AGGIUDICATARIO"):  # Se è un vincitore
            denominazione = part.get("DENOMINAZIONE", "Sconosciuto")
            ruolo = part.get("RUOLO", "OPERATORE ECONOMICO")
            cf = part.get("CODICE_FISCALE", "CF non presente")

            # Aggiungiamo i dati alla rispettiva lista
            # Esempio: aggiungiamo il ruolo al nome per chiarezza
            lista_nomi.append(f"{denominazione} ({ruolo})")
            lista_cf.append(cf)

    # Inseriamo nel dizionario finale come stringhe separate
    # Così, se hai più aziende, avrai: "Nome1, Nome2" e "CF1, CF2"
    if lista_nomi:
        risultato["aggiudicatario"] = ", ".join(lista_nomi)
        risultato["aggiudicatario_cf"] = ", ".join(lista_cf)
    else:
        risultato["aggiudicatario"] = "Non presente"
        risultato["aggiudicatario_cf"] = "Non presente"

    return risultato  # ritorna i valori ottenuti


# Chiave pubblica del form Mosparo di ANAC. Non e' piu' cablata a mano: la si
# recupera da runtime-config.js (vedi ottieni_chiave_mosparo piu' sotto), il
# file dove ANAC la pubblica. Questo valore resta come RISERVA, usato solo se
# il recupero automatico fallisce, cosi' il programma non si blocca mai.
# ANAC la cambia quando reinstalla Mosparo: prima serviva aggiornare a mano
# questa costante, ora il recupero automatico prende la nuova da solo.
# Storico: jUHeENQtdJN-tmRO0FWpv1QnvTyfWpifwSHMpNOcSck (non piu' valida)
MOSPARO_PUBLIC_KEY_RISERVA = "IvdGTv3BCns5EerGuPtrT7S_mLlZPEbPZy9Y7jj1q94"

# URL del file di configurazione ANAC che espone la chiave pubblica corrente.
URL_RUNTIME_CONFIG = "https://dettaglio-cig.anticorruzione.it/runtime-config.js"

# Cache in memoria: la chiave si recupera UNA volta per sessione, non a ogni CIG.
_chiave_mosparo_cache = None


def ottieni_chiave_mosparo(forza_refresh=False):
    """
    Restituisce la chiave pubblica Mosparo, recuperandola da runtime-config.js.

    ANAC pubblica la chiave in quel file nella forma:
        MOSPARO_PUBLIC_KEY: "IvdGTv3BCns5EerGuPtrT7S_mLlZPEbPZy9Y7jj1q94",
    Il valore si legge con una regex e si conserva in cache per l'intera
    sessione (una scansione tocca decine di CIG: riscaricare il file ogni volta
    sarebbe inutile). forza_refresh=True ignora la cache e rilegge il file.

    Se il file non e' raggiungibile o non contiene la chiave, si ripiega sul
    valore di RISERVA cablato: il programma continua a funzionare con l'ultima
    chiave nota, esattamente come prima di questa modifica.
    """
    global _chiave_mosparo_cache
    if _chiave_mosparo_cache and not forza_refresh:
        return _chiave_mosparo_cache
    try:
        r = requests.get(URL_RUNTIME_CONFIG, timeout=10)
        if r.ok:
            m = re.search(r'MOSPARO_PUBLIC_KEY\s*:\s*["\']([A-Za-z0-9_-]+)["\']',
                          r.text)
            if m:
                _chiave_mosparo_cache = m.group(1)
                return _chiave_mosparo_cache
    except requests.RequestException:
        pass
    # Recupero fallito: si usa la riserva senza bloccare nulla.
    _chiave_mosparo_cache = MOSPARO_PUBLIC_KEY_RISERVA
    return _chiave_mosparo_cache


# Via da usare per le chiamate ANAC, ricordata dopo il primo rilevamento.
# None = ancora da stabilire; "mosparo" = il server ha chiesto la verifica.
# Serve a non ripetere per OGNI CIG la chiamata diretta destinata a fallire:
# quelle richieste sprecate sono anche cio' che fa scattare il rate limit 429.
# Si memorizza solo l'esito "serve mosparo", non il contrario: se la diretta
# funziona non c'e' nulla da ricordare, e' gia' la prima che si prova.
_via_anac_rilevata = None


def reimposta_via_anac():
    """
    Dimentica la via ANAC memorizzata, tornando al rilevamento automatico.
    Utile se ANAC cambia comportamento durante l'esecuzione o fra due giri
    nella stessa sessione (es. dalla GUI, senza riavviare il programma).
    """
    global _via_anac_rilevata
    _via_anac_rilevata = None


def _richiede_mosparo(risposta):
    """
    True se la risposta del server indica che i token Mosparo sono richiesti.

    Non esiste un flag esplicito: si riconoscono i due modi in cui ANAC lo
    manifesta, cioe' lo stato HTTP tipico del blocco anti-bot (401/403, e 400
    quando l'endpoint rifiuta il payload privo dei campi _mosparo_*) e la
    comparsa della parola "mosparo"/"submitToken" nel corpo della risposta.
    Il controllo e' volutamente prudente: in caso di dubbio torna False e si
    prosegue con i normali tentativi diretti, senza pagare il costo del
    proof of work quando non serve.
    """
    if risposta is None:
        return False
    if getattr(risposta, "status_code", None) in (400, 401, 403):
        return True
    try:
        corpo = (risposta.text or "").lower()
    except Exception:
        return False
    return "mosparo" in corpo or "submittoken" in corpo


def scarica_json_anac(cig, tentativi=15, via=None):
    """
    Recupera il JSON del CIG da ANAC scegliendo da sola la via giusta.

    Prova per prima la via DIRETTA, che e' quella che oggi funziona e costa
    una sola chiamata. Se il server torna a pretendere i token della verifica
    anti-bot, ripiega automaticamente sulla via con MOSPARO (submit token,
    proof of work SHA-256, validazione form). Il ripiego scatta al primo
    segnale, senza esaurire i tentativi su richieste che non possono passare.

    Il rilevamento avviene UNA VOLTA SOLA: accertato che serve la verifica,
    i CIG successivi partono direttamente da Mosparo, senza ripetere ogni
    volta una chiamata diretta che si sa gia' destinata a fallire (due
    richieste sprecate per CIG, che oltre a rallentare alimentano il rate
    limit 429). reimposta_via_anac() azzera la memoria se serve.

    via="diretto" o via="mosparo" forza una delle due strade, utile per le
    prove; None (default) lascia decidere al meccanismo automatico.

    Firma e valore di ritorno identici alla versione precedente: il dict del
    CIG, oppure None se non si recupera nulla. Nessun chiamante va adeguato.
    """
    global _via_anac_rilevata

    if via == "mosparo":
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)
    if via == "diretto":
        return _scarica_json_anac_diretto(cig, tentativi=tentativi)

    # Se un CIG precedente ha gia' accertato che il server pretende la
    # verifica, si va diritti su quella via: ripetere la chiamata diretta per
    # ogni CIG significherebbe due richieste sprecate a testa, che oltre a
    # rallentare contribuiscono a far scattare il rate limit.
    if _via_anac_rilevata == "mosparo":
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)

    # "serve_mosparo" e' una lista di un elemento usata come segnale scritto
    # dalla via diretta: se il server pretende i token, quella la imposta a
    # True e si ferma subito, cosi' qui si capisce che il fallimento non e'
    # un errore di rete da ritentare ma la richiesta di cambiare strada.
    serve_mosparo = [False]
    dati = _scarica_json_anac_diretto(cig, tentativi=tentativi,
                                      serve_mosparo=serve_mosparo)
    if dati is None and serve_mosparo[0]:
        # Rilevamento fatto una volta sola: da qui in avanti si parte da Mosparo.
        _via_anac_rilevata = "mosparo"
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)
    return dati


# =====================================================================
# VIA CON VERIFICA MOSPARO — usata come RIPIEGO automatico
# ANAC ha rimosso la verifica anti-bot dall'endpoint consultaCIG, quindi la
# via normale e' quella diretta. Questa resta pronta e viene richiamata da
# scarica_json_anac() quando il server torna a pretendere i token Mosparo.
# Flusso: cookie di sessione -> submit token -> proof of work SHA-256 ->
# validazione form -> chiamata consultaCIG con i token _mosparo_*.
# =====================================================================
def _scarica_json_anac_mosparo(cig, tentativi=15):  # vecchi tentativi 5, 10
    # Stessa guardia della via diretta: un CIG non e' mai diverso da 10
    # caratteri. Qui pesa ancora di piu', perche' ogni tentativo costa
    # QUATTRO chiamate HTTP piu' il proof of work (ciclo SHA-256 fino a
    # proofOfWorkMaxNumber): su un codice inesistente sarebbe tutto lavoro
    # sprecato, ripetuto per tutti i tentativi.
    if not cig or len(cig) != 10:
        print(f"    [-] CIG non valido ({len(cig) if cig else 0} caratteri): '{cig}' — salto la chiamata ANAC")
        return None

    for tentativo in range(1, tentativi + 1):  # itera sui tentativi
        if tentativo > 1:  # se non è il primo tentativo fa una pausa, al primo non avrebbe senso
            print(f"    [!] Tentativo {tentativo}/{tentativi}...")
            time.sleep(8)

        sessione = requests.Session()  # ad ogni tentativo viene creata una nuova sessione
        # è essenziale perché Mosparo lega il token alla sessione/cookie ottenuti nello STEP 0.
        # Crearne una nuova ad ogni tentativo garantisce di partire "pulito", senza cookie scaduti o
        # invalidati dal tentativo precedente.
        try:
            # STEP 0: Cookie di sessione
            # Visita la pagina del CIG nel browser ANAC. Non ci interessa il contenuto della risposta — l'unico scopo è far sì che il server imposti i cookie di sessione nella sessione,
            # che verranno automaticamente inclusi in tutte le richieste successive fatte con la stessa sessione
            url_pagina = f"https://dettaglio-cig.anticorruzione.it/cig/{cig}"
            sessione.get(url_pagina, timeout=20)  # vecchio 45

            # STEP 1: Submit token
            # Mosparo richiede un "submit token" prima di accettare qualsiasi dato dal form
            url_token = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/request-submit-token"
            payload_token = {
                "pageTitle": "dati-cig",
                "pageUrl": url_pagina,
                "htmlLanguage": "en",
                "publicKey": ottieni_chiave_mosparo()  # recuperata da runtime-config.js (con riserva)
            }
            risposta_token = sessione.post(url_token, json=payload_token,
                                           timeout=10)  # json=payload_token invia il dizionario come corpo JSON della richiesta POST (requests lo converte automaticamente e imposta Content-Type: application/json).
            # Controlla se il server ha risposto con un errore (es. 500, 502, 503)
            if not risposta_token.ok:
                print(f"    [-] Il server ANAC è irraggiungibile o in errore (Status: {risposta_token.status_code})")
                # 401/403 allo STEP 1 = il server RIFIUTA la chiave pubblica,
                # non e' un disservizio passeggero. Ritentare identici altri 14
                # volte e' inutile: si esce subito spiegando cosa aggiornare.
                if risposta_token.status_code in (401, 403):
                    # La chiave e' stata rifiutata. Prima di arrendersi: forse
                    # ANAC l'ha appena cambiata e la cache e' vecchia. Si rilegge
                    # runtime-config.js UNA volta; se la chiave e' davvero
                    # diversa, si ritenta con quella nuova.
                    vecchia = ottieni_chiave_mosparo()
                    nuova = ottieni_chiave_mosparo(forza_refresh=True)
                    if nuova != vecchia:
                        print("    [i] Chiave Mosparo aggiornata da runtime-config.js: ritento.")
                        continue
                    print("    [-] Chiave pubblica Mosparo rifiutata da ANAC.")
                    print("        La chiave in runtime-config.js e' la stessa gia' usata:")
                    print("        se il problema persiste, ANAC potrebbe aver cambiato il")
                    print("        meccanismo di verifica (non solo la chiave).")
                    return None
                continue  # Passa al tentativo successivo senza far crashare il programma
            dati_token = risposta_token.json()  # .json() converte la risposta (testo JSON) in un dizionario Python
            # Estraiamo 3 valori dalla risposta
            submit_token = dati_token["submitToken"]  # il token che useremo nei passi successivi
            proof_of_work_result = dati_token[
                "proofOfWorkResult"]  # l'hash target che dobbiamo "indovinare" nel passo 2
            proof_of_work_max = dati_token[
                "proofOfWorkMaxNumber"]  # il limite massimo entro cui cercare il numero giusto

            # STEP 2: Calcolo proof of work
            # Questo è il meccanismo "anti-bot" che abbiamo decodificato dal file JavaScript di Mosparo:
            # il server fornisce un hash target (proof_of_work_result) e noi dobbiamo trovare un numero n tale che
            # SHA256(submitToken + n) produca esattamente quell'hash.
            proof_number = None
            for n in range(proof_of_work_max + 1):  # prova n = 0, 1, 2, ..., proof_of_work_max
                stringa = f"{submit_token}{n}"  # Per ognuno, concatena submit_token e n come stringa, calcola l'hash SHA-256
                hash_risultato = hashlib.sha256(
                    stringa.encode('utf-8')).hexdigest()  # restituisce l'hash come stringa esadecimale)
                if hash_risultato == proof_of_work_result:  # confronta con il target
                    proof_number = n  # Quando trova la corrispondenza, salva n in proof_number
                    break  # esce dal ciclo

            if proof_number is None:  # Se nessun numero nel range produce l'hash giusto (caso anomalo, non dovrebbe succedere quasi mai), proof_number resta None
                print(f"    [-] Proof of work non trovato per CIG {cig}")
                continue  # salta al tentativo successivo del ciclo esterno — non ha senso proseguire senza questo numero.

            # STEP 3: Validazione form
            # Prepara i "dati del form" come li manderebbe il browser —
            # un oggetto che descrive il campo cig con il suo valore
            url_check = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/check-form-data"
            form_data_stringa = json.dumps({
                # converte il dizionario in una stringa JSON compatta (senza spazi dopo virgole e due punti) — necessario perché Mosparo confronta questa stringa esatta per validare l'integrità dei dati.
                "fields": [{"name": "cig", "value": cig, "fieldPath": "input[text].cig"}],
                "ignoredFields": []
            }, separators=(',', ':'))

            payload_check = {
                "formData": form_data_stringa,
                "submitToken": submit_token,
                "proofOfWorkNumber": proof_number,
                "publicKey": ottieni_chiave_mosparo()  # stessa chiave (dalla cache di sessione)
            }
            risposta_check = sessione.post(url_check, data=payload_check,
                                           timeout=10)  # Nota data=payload_check invece di json=payload_check —
            # questa è la differenza che avevamo scoperto: il browser invia
            # questi dati come form URL-encoded (Content-Type: application/x-www-form-urlencoded),
            # non come JSON. requests con data= (un dizionario) li codifica automaticamente in quel formato.
            dati_check = risposta_check.json()

            if not dati_check.get("valid"):  # Se la risposta non contiene "valid": true, la validazione è fallita
                print(f"    [-] Validazione Mosparo fallita per CIG {cig}")
                continue  # si passa al tentativo successivo

            validation_token = dati_check[
                "validationToken"]  # altrimenti si estrae il validation_token, l'ultimo "permesso" necessario.

            # STEP 4: Scarica i dati del CIG
            # Ora che abbiamo entrambi i token Mosparo (submitToken e validationToken),
            # possiamo chiamare l'endpoint vero che restituisce i dati del CIG
            url_cig = "https://dettaglio-cig.anticorruzione.it/api/v1/operations/consultaCIG/1.0/exec"
            payload_cig = {
                "cig": cig,
                "_mosparo_submitToken": submit_token,
                "_mosparo_validationToken": validation_token
            }
            risposta_cig = sessione.post(url_cig, json=payload_cig,
                                         timeout=10)  # torniamo a json= perché questo endpoint si aspetta JSON (diverso dal form Mosparo).

            if risposta_cig.status_code == 200:  # Se la risposta è 200, prendiamo il JSON
                dati = risposta_cig.json()
                return dati[0] if isinstance(dati, list) and len(
                    dati) > 0 else dati  # isinstance(dati, list) and len(dati) > 0 controlla: è una lista e non è vuota? Se sì, restituiamo il primo elemento (dati[0]) — l'API restituisce i dati del CIG dentro una lista con un solo elemento, quindi "scartiamo" il livello lista esterno, se non è una lista (o è vuota), restituiamo dati così com'è
            else:  # Se invece lo status non è 200, stampiamo l'errore e continue al tentativo successivo.
                print(f"    [-] API CIG ha risposto con codice: {risposta_cig.status_code}")
                continue

        except Exception as e:
            print(
                f"    [-] Errore tentativo {tentativo} per CIG {cig}: {e}")  # Qualsiasi eccezione (timeout, errore di rete, JSON malformato, chiave mancante in dati_token["submitToken"] se la risposta non ha quella struttura) viene catturata qui e stampata, poi il ciclo for continua al tentativo successivo automaticamente

    print(
        f"    [-] Tutti i tentativi falliti per CIG {cig}")  # Se tutti i tentativi falliscono (nessun return è stato eseguito), si arriva dopo il for e si restituisce None — che il main interpreta come "impossibile recuperare i dati ANAC".
    return None


def _scarica_json_anac_diretto(cig, tentativi=15, serve_mosparo=None):
    """
    Scarica il JSON del CIG dal sito ANAC — versione SENZA verifica Mosparo.

    ANAC ha rimosso la verifica anti-bot Mosparo dall'endpoint consultaCIG:
    il POST con il solo {"cig": ...} restituisce direttamente i dati. Si
    mantiene lo STEP 0 (visita della pagina del CIG per i cookie di sessione),
    innocuo e potenzialmente ancora richiesto dal server.
    La versione precedente con Mosparo e' archiviata qui sopra, pronta al
    ripristino se la verifica venisse reintrodotta.

    Ritorna il dict del CIG (primo elemento se l'API risponde con una lista),
    None se tutti i tentativi falliscono — contratto identico alla versione
    Mosparo, nessun cambiamento per i chiamanti.

    serve_mosparo, se passato, e' una lista di un elemento che questa funzione
    imposta a True quando il server pretende i token della verifica anti-bot:
    e' il segnale con cui il dispatcher distingue "non ci sono riuscito" da
    "serve l'altra via". Chi chiama questa funzione da sola puo' ignorarlo.
    """
    # Guardia: un CIG non e' mai diverso da 10 caratteri alfanumerici. Un codice
    # monco (es. troncato a 9 in pagina) non esiste in ANAC: chiamare l'API
    # brucerebbe TUTTI i tentativi (15 x 8s) per nulla. Si rifiuta subito,
    # qualunque sia il chiamante (main, GUI, ...).
    if not cig or len(cig) != 10:
        print(f"    [-] CIG non valido ({len(cig) if cig else 0} caratteri): '{cig}' — salto la chiamata ANAC")
        return None

    for tentativo in range(1, tentativi + 1):
        if tentativo > 1:
            print(f"    [!] Tentativo {tentativo}/{tentativi}...")
            time.sleep(8)

        sessione = requests.Session()  # sessione nuova ad ogni tentativo: si parte puliti
        try:
            # STEP 0: cookie di sessione (visita della pagina del CIG)
            url_pagina = f"https://dettaglio-cig.anticorruzione.it/cig/{cig}"
            sessione.get(url_pagina, timeout=20)

            # STEP 1: chiamata diretta all'endpoint dei dati del CIG
            url_cig = "https://dettaglio-cig.anticorruzione.it/api/v1/operations/consultaCIG/1.0/exec"
            payload_cig = {"cig": cig}  # senza Mosparo bastano i dati del CIG
            risposta_cig = sessione.post(url_cig, json=payload_cig, timeout=10)

            if risposta_cig.status_code == 200:
                dati = risposta_cig.json()
                # l'API restituisce i dati dentro una lista con un solo elemento
                return dati[0] if isinstance(dati, list) and len(dati) > 0 else dati

            # 429 = Too Many Requests: ANAC sta limitando la FREQUENZA, non
            # chiede una verifica. Ritentare subito peggiora la situazione:
            # si rispetta l'header Retry-After se c'e', altrimenti si aspetta
            # un tempo crescente col numero di tentativi.
            if risposta_cig.status_code == 429:
                try:
                    attesa = int(risposta_cig.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    attesa = 0
                attesa = attesa or min(60, 8 * tentativo)
                print(f"    [-] ANAC limita le richieste (429): attendo {attesa}s")
                time.sleep(attesa)
                continue

            # Il server pretende di nuovo i token Mosparo: inutile insistere
            # per tutti i tentativi con una richiesta che non puo' passare.
            # Si segna il motivo nel flag del chiamante e si esce subito, cosi'
            # il dispatcher sa che deve ripiegare sulla via con verifica.
            if _richiede_mosparo(risposta_cig):
                print("    [!] ANAC richiede la verifica Mosparo: passo alla via con verifica")
                if serve_mosparo is not None:
                    serve_mosparo[0] = True
                return None

            print(f"    [-] API CIG ha risposto con codice: {risposta_cig.status_code}")
            continue

        except Exception as e:
            print(f"    [-] Errore tentativo {tentativo} per CIG {cig}: {e}")

    print(f"    [-] Tutti i tentativi falliti per CIG {cig}")
    return None


'''
#Versione con chaive pubblica fissa
# Chiave pubblica del form Mosparo di ANAC. E' statica per quell'installazione
# e si legge dagli strumenti di sviluppo del browser: pagina di un CIG ->
# scheda Rete -> chiamata "request-submit-token" -> Payload -> publicKey.
# ANAC la cambia quando reinstalla Mosparo: quando succede, il server risponde
# 403 allo STEP 1 ("Il server ANAC e' irraggiungibile o in errore") e basta
# aggiornare questa costante, senza toccare il resto del flusso.
# Storico: jUHeENQtdJN-tmRO0FWpv1QnvTyfWpifwSHMpNOcSck (non piu' valida)
MOSPARO_PUBLIC_KEY = "IvdGTv3BCns5EerGuPtrT7S_mLlZPEbPZy9Y7jj1q94"

# Via da usare per le chiamate ANAC, ricordata dopo il primo rilevamento.
# None = ancora da stabilire; "mosparo" = il server ha chiesto la verifica.
# Serve a non ripetere per OGNI CIG la chiamata diretta destinata a fallire:
# quelle richieste sprecate sono anche cio' che fa scattare il rate limit 429.
# Si memorizza solo l'esito "serve mosparo", non il contrario: se la diretta
# funziona non c'e' nulla da ricordare, e' gia' la prima che si prova.
_via_anac_rilevata = None


def reimposta_via_anac():
    """
    Dimentica la via ANAC memorizzata, tornando al rilevamento automatico.
    Utile se ANAC cambia comportamento durante l'esecuzione o fra due giri
    nella stessa sessione (es. dalla GUI, senza riavviare il programma).
    """
    global _via_anac_rilevata
    _via_anac_rilevata = None


def _richiede_mosparo(risposta):
    """
    True se la risposta del server indica che i token Mosparo sono richiesti.

    Non esiste un flag esplicito: si riconoscono i due modi in cui ANAC lo
    manifesta, cioe' lo stato HTTP tipico del blocco anti-bot (401/403, e 400
    quando l'endpoint rifiuta il payload privo dei campi _mosparo_*) e la
    comparsa della parola "mosparo"/"submitToken" nel corpo della risposta.
    Il controllo e' volutamente prudente: in caso di dubbio torna False e si
    prosegue con i normali tentativi diretti, senza pagare il costo del
    proof of work quando non serve.
    """
    if risposta is None:
        return False
    if getattr(risposta, "status_code", None) in (400, 401, 403):
        return True
    try:
        corpo = (risposta.text or "").lower()
    except Exception:
        return False
    return "mosparo" in corpo or "submittoken" in corpo


def scarica_json_anac(cig, tentativi=15, via=None):
    """
    Recupera il JSON del CIG da ANAC scegliendo da sola la via giusta.

    Prova per prima la via DIRETTA, che e' quella che oggi funziona e costa
    una sola chiamata. Se il server torna a pretendere i token della verifica
    anti-bot, ripiega automaticamente sulla via con MOSPARO (submit token,
    proof of work SHA-256, validazione form). Il ripiego scatta al primo
    segnale, senza esaurire i tentativi su richieste che non possono passare.

    Il rilevamento avviene UNA VOLTA SOLA: accertato che serve la verifica,
    i CIG successivi partono direttamente da Mosparo, senza ripetere ogni
    volta una chiamata diretta che si sa gia' destinata a fallire (due
    richieste sprecate per CIG, che oltre a rallentare alimentano il rate
    limit 429). reimposta_via_anac() azzera la memoria se serve.

    via="diretto" o via="mosparo" forza una delle due strade, utile per le
    prove; None (default) lascia decidere al meccanismo automatico.

    Firma e valore di ritorno identici alla versione precedente: il dict del
    CIG, oppure None se non si recupera nulla. Nessun chiamante va adeguato.
    """
    global _via_anac_rilevata

    if via == "mosparo":
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)
    if via == "diretto":
        return _scarica_json_anac_diretto(cig, tentativi=tentativi)

    # Se un CIG precedente ha gia' accertato che il server pretende la
    # verifica, si va diritti su quella via: ripetere la chiamata diretta per
    # ogni CIG significherebbe due richieste sprecate a testa, che oltre a
    # rallentare contribuiscono a far scattare il rate limit.
    if _via_anac_rilevata == "mosparo":
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)

    # "serve_mosparo" e' una lista di un elemento usata come segnale scritto
    # dalla via diretta: se il server pretende i token, quella la imposta a
    # True e si ferma subito, cosi' qui si capisce che il fallimento non e'
    # un errore di rete da ritentare ma la richiesta di cambiare strada.
    serve_mosparo = [False]
    dati = _scarica_json_anac_diretto(cig, tentativi=tentativi,
                                      serve_mosparo=serve_mosparo)
    if dati is None and serve_mosparo[0]:
        # Rilevamento fatto una volta sola: da qui in avanti si parte da Mosparo.
        _via_anac_rilevata = "mosparo"
        return _scarica_json_anac_mosparo(cig, tentativi=tentativi)
    return dati


# =====================================================================
# VIA CON VERIFICA MOSPARO — usata come RIPIEGO automatico
# ANAC ha rimosso la verifica anti-bot dall'endpoint consultaCIG, quindi la
# via normale e' quella diretta. Questa resta pronta e viene richiamata da
# scarica_json_anac() quando il server torna a pretendere i token Mosparo.
# Flusso: cookie di sessione -> submit token -> proof of work SHA-256 ->
# validazione form -> chiamata consultaCIG con i token _mosparo_*.
# =====================================================================
def _scarica_json_anac_mosparo(cig, tentativi=15):  # vecchi tentativi 5, 10
    # Stessa guardia della via diretta: un CIG non e' mai diverso da 10
    # caratteri. Qui pesa ancora di piu', perche' ogni tentativo costa
    # QUATTRO chiamate HTTP piu' il proof of work (ciclo SHA-256 fino a
    # proofOfWorkMaxNumber): su un codice inesistente sarebbe tutto lavoro
    # sprecato, ripetuto per tutti i tentativi.
    if not cig or len(cig) != 10:
        print(f"    [-] CIG non valido ({len(cig) if cig else 0} caratteri): '{cig}' — salto la chiamata ANAC")
        return None

    for tentativo in range(1, tentativi + 1):  # itera sui tentativi
        if tentativo > 1:  # se non è il primo tentativo fa una pausa, al primo non avrebbe senso
            print(f"    [!] Tentativo {tentativo}/{tentativi}...")
            time.sleep(8)

        sessione = requests.Session()  # ad ogni tentativo viene creata una nuova sessione
        # è essenziale perché Mosparo lega il token alla sessione/cookie ottenuti nello STEP 0.
        # Crearne una nuova ad ogni tentativo garantisce di partire "pulito", senza cookie scaduti o
        # invalidati dal tentativo precedente.
        try:
            # STEP 0: Cookie di sessione
            # Visita la pagina del CIG nel browser ANAC. Non ci interessa il contenuto della risposta — l'unico scopo è far sì che il server imposti i cookie di sessione nella sessione,
            # che verranno automaticamente inclusi in tutte le richieste successive fatte con la stessa sessione
            url_pagina = f"https://dettaglio-cig.anticorruzione.it/cig/{cig}"
            sessione.get(url_pagina, timeout=20)  # vecchio 45

            # STEP 1: Submit token
            # Mosparo richiede un "submit token" prima di accettare qualsiasi dato dal form
            url_token = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/request-submit-token"
            payload_token = {
                "pageTitle": "dati-cig",
                "pageUrl": url_pagina,
                "htmlLanguage": "en",
                "publicKey": MOSPARO_PUBLIC_KEY
                # è la chiave pubblica del form Mosparo presente sul sito — è statica/fissa per quel form specifico, l'abbiamo trovata ispezionando la richiesta nel browser.
            }
            risposta_token = sessione.post(url_token, json=payload_token,
                                           timeout=10)  # json=payload_token invia il dizionario come corpo JSON della richiesta POST (requests lo converte automaticamente e imposta Content-Type: application/json).
            # Controlla se il server ha risposto con un errore (es. 500, 502, 503)
            if not risposta_token.ok:
                print(f"    [-] Il server ANAC è irraggiungibile o in errore (Status: {risposta_token.status_code})")
                # 401/403 allo STEP 1 = il server RIFIUTA la chiave pubblica,
                # non e' un disservizio passeggero. Ritentare identici altri 14
                # volte e' inutile: si esce subito spiegando cosa aggiornare.
                if risposta_token.status_code in (401, 403):
                    print("    [-] Chiave pubblica Mosparo rifiutata: probabilmente ANAC l'ha cambiata.")
                    print("        Aggiorna MOSPARO_PUBLIC_KEY leggendola dagli strumenti di sviluppo")
                    print("        (pagina CIG -> Rete -> request-submit-token -> Payload -> publicKey).")
                    return None
                continue  # Passa al tentativo successivo senza far crashare il programma
            dati_token = risposta_token.json()  # .json() converte la risposta (testo JSON) in un dizionario Python
            # Estraiamo 3 valori dalla risposta
            submit_token = dati_token["submitToken"]  # il token che useremo nei passi successivi
            proof_of_work_result = dati_token[
                "proofOfWorkResult"]  # l'hash target che dobbiamo "indovinare" nel passo 2
            proof_of_work_max = dati_token[
                "proofOfWorkMaxNumber"]  # il limite massimo entro cui cercare il numero giusto

            # STEP 2: Calcolo proof of work
            # Questo è il meccanismo "anti-bot" che abbiamo decodificato dal file JavaScript di Mosparo:
            # il server fornisce un hash target (proof_of_work_result) e noi dobbiamo trovare un numero n tale che
            # SHA256(submitToken + n) produca esattamente quell'hash.
            proof_number = None
            for n in range(proof_of_work_max + 1):  # prova n = 0, 1, 2, ..., proof_of_work_max
                stringa = f"{submit_token}{n}"  # Per ognuno, concatena submit_token e n come stringa, calcola l'hash SHA-256
                hash_risultato = hashlib.sha256(
                    stringa.encode('utf-8')).hexdigest()  # restituisce l'hash come stringa esadecimale)
                if hash_risultato == proof_of_work_result:  # confronta con il target
                    proof_number = n  # Quando trova la corrispondenza, salva n in proof_number
                    break  # esce dal ciclo

            if proof_number is None:  # Se nessun numero nel range produce l'hash giusto (caso anomalo, non dovrebbe succedere quasi mai), proof_number resta None
                print(f"    [-] Proof of work non trovato per CIG {cig}")
                continue  # salta al tentativo successivo del ciclo esterno — non ha senso proseguire senza questo numero.

            # STEP 3: Validazione form
            # Prepara i "dati del form" come li manderebbe il browser —
            # un oggetto che descrive il campo cig con il suo valore
            url_check = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/check-form-data"
            form_data_stringa = json.dumps({
                # converte il dizionario in una stringa JSON compatta (senza spazi dopo virgole e due punti) — necessario perché Mosparo confronta questa stringa esatta per validare l'integrità dei dati.
                "fields": [{"name": "cig", "value": cig, "fieldPath": "input[text].cig"}],
                "ignoredFields": []
            }, separators=(',', ':'))

            payload_check = {
                "formData": form_data_stringa,
                "submitToken": submit_token,
                "proofOfWorkNumber": proof_number,
                "publicKey": MOSPARO_PUBLIC_KEY
            }
            risposta_check = sessione.post(url_check, data=payload_check,
                                           timeout=10)  # Nota data=payload_check invece di json=payload_check —
            # questa è la differenza che avevamo scoperto: il browser invia
            # questi dati come form URL-encoded (Content-Type: application/x-www-form-urlencoded),
            # non come JSON. requests con data= (un dizionario) li codifica automaticamente in quel formato.
            dati_check = risposta_check.json()

            if not dati_check.get("valid"):  # Se la risposta non contiene "valid": true, la validazione è fallita
                print(f"    [-] Validazione Mosparo fallita per CIG {cig}")
                continue  # si passa al tentativo successivo

            validation_token = dati_check[
                "validationToken"]  # altrimenti si estrae il validation_token, l'ultimo "permesso" necessario.

            # STEP 4: Scarica i dati del CIG
            # Ora che abbiamo entrambi i token Mosparo (submitToken e validationToken),
            # possiamo chiamare l'endpoint vero che restituisce i dati del CIG
            url_cig = "https://dettaglio-cig.anticorruzione.it/api/v1/operations/consultaCIG/1.0/exec"
            payload_cig = {
                "cig": cig,
                "_mosparo_submitToken": submit_token,
                "_mosparo_validationToken": validation_token
            }
            risposta_cig = sessione.post(url_cig, json=payload_cig,
                                         timeout=10)  # torniamo a json= perché questo endpoint si aspetta JSON (diverso dal form Mosparo).

            if risposta_cig.status_code == 200:  # Se la risposta è 200, prendiamo il JSON
                dati = risposta_cig.json()
                return dati[0] if isinstance(dati, list) and len(
                    dati) > 0 else dati  # isinstance(dati, list) and len(dati) > 0 controlla: è una lista e non è vuota? Se sì, restituiamo il primo elemento (dati[0]) — l'API restituisce i dati del CIG dentro una lista con un solo elemento, quindi "scartiamo" il livello lista esterno, se non è una lista (o è vuota), restituiamo dati così com'è
            else:  # Se invece lo status non è 200, stampiamo l'errore e continue al tentativo successivo.
                print(f"    [-] API CIG ha risposto con codice: {risposta_cig.status_code}")
                continue

        except Exception as e:
            print(
                f"    [-] Errore tentativo {tentativo} per CIG {cig}: {e}")  # Qualsiasi eccezione (timeout, errore di rete, JSON malformato, chiave mancante in dati_token["submitToken"] se la risposta non ha quella struttura) viene catturata qui e stampata, poi il ciclo for continua al tentativo successivo automaticamente

    print(
        f"    [-] Tutti i tentativi falliti per CIG {cig}")  # Se tutti i tentativi falliscono (nessun return è stato eseguito), si arriva dopo il for e si restituisce None — che il main interpreta come "impossibile recuperare i dati ANAC".
    return None


def _scarica_json_anac_diretto(cig, tentativi=15, serve_mosparo=None):
    """
    Scarica il JSON del CIG dal sito ANAC — versione SENZA verifica Mosparo.

    ANAC ha rimosso la verifica anti-bot Mosparo dall'endpoint consultaCIG:
    il POST con il solo {"cig": ...} restituisce direttamente i dati. Si
    mantiene lo STEP 0 (visita della pagina del CIG per i cookie di sessione),
    innocuo e potenzialmente ancora richiesto dal server.
    La versione precedente con Mosparo e' archiviata qui sopra, pronta al
    ripristino se la verifica venisse reintrodotta.

    Ritorna il dict del CIG (primo elemento se l'API risponde con una lista),
    None se tutti i tentativi falliscono — contratto identico alla versione
    Mosparo, nessun cambiamento per i chiamanti.

    serve_mosparo, se passato, e' una lista di un elemento che questa funzione
    imposta a True quando il server pretende i token della verifica anti-bot:
    e' il segnale con cui il dispatcher distingue "non ci sono riuscito" da
    "serve l'altra via". Chi chiama questa funzione da sola puo' ignorarlo.
    """
    # Guardia: un CIG non e' mai diverso da 10 caratteri alfanumerici. Un codice
    # monco (es. troncato a 9 in pagina) non esiste in ANAC: chiamare l'API
    # brucerebbe TUTTI i tentativi (15 x 8s) per nulla. Si rifiuta subito,
    # qualunque sia il chiamante (main, GUI, ...).
    if not cig or len(cig) != 10:
        print(f"    [-] CIG non valido ({len(cig) if cig else 0} caratteri): '{cig}' — salto la chiamata ANAC")
        return None

    for tentativo in range(1, tentativi + 1):
        if tentativo > 1:
            print(f"    [!] Tentativo {tentativo}/{tentativi}...")
            time.sleep(8)

        sessione = requests.Session()  # sessione nuova ad ogni tentativo: si parte puliti
        try:
            # STEP 0: cookie di sessione (visita della pagina del CIG)
            url_pagina = f"https://dettaglio-cig.anticorruzione.it/cig/{cig}"
            sessione.get(url_pagina, timeout=20)

            # STEP 1: chiamata diretta all'endpoint dei dati del CIG
            url_cig = "https://dettaglio-cig.anticorruzione.it/api/v1/operations/consultaCIG/1.0/exec"
            payload_cig = {"cig": cig}  # senza Mosparo bastano i dati del CIG
            risposta_cig = sessione.post(url_cig, json=payload_cig, timeout=10)

            if risposta_cig.status_code == 200:
                dati = risposta_cig.json()
                # l'API restituisce i dati dentro una lista con un solo elemento
                return dati[0] if isinstance(dati, list) and len(dati) > 0 else dati

            # 429 = Too Many Requests: ANAC sta limitando la FREQUENZA, non
            # chiede una verifica. Ritentare subito peggiora la situazione:
            # si rispetta l'header Retry-After se c'e', altrimenti si aspetta
            # un tempo crescente col numero di tentativi.
            if risposta_cig.status_code == 429:
                try:
                    attesa = int(risposta_cig.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    attesa = 0
                attesa = attesa or min(60, 8 * tentativo)
                print(f"    [-] ANAC limita le richieste (429): attendo {attesa}s")
                time.sleep(attesa)
                continue

            # Il server pretende di nuovo i token Mosparo: inutile insistere
            # per tutti i tentativi con una richiesta che non puo' passare.
            # Si segna il motivo nel flag del chiamante e si esce subito, cosi'
            # il dispatcher sa che deve ripiegare sulla via con verifica.
            if _richiede_mosparo(risposta_cig):
                print("    [!] ANAC richiede la verifica Mosparo: passo alla via con verifica")
                if serve_mosparo is not None:
                    serve_mosparo[0] = True
                return None

            print(f"    [-] API CIG ha risposto con codice: {risposta_cig.status_code}")
            continue

        except Exception as e:
            print(f"    [-] Errore tentativo {tentativo} per CIG {cig}: {e}")

    print(f"    [-] Tutti i tentativi falliti per CIG {cig}")
    return None
'''