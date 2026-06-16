import requests
from bs4 import BeautifulSoup
import urllib.parse  # Serve per gestire correttamente gli spazi e i caratteri speciali nei filtri
import json
import time
import hashlib

BASE_URL = "https://www.provincia.pistoia.it"


def genera_url_con_filtri(parola_chiave="", cig="", stato="All", contraente="All", tipologia="All"):
    """
    Questa funzione è il cuore generico: prende i parametri (filtri) che decidi tu
    e costruisce l'URL perfetto per il sito.
    """
    # Puliamo la parola chiave per renderla leggibile da un URL (es. trasforma gli spazi in %20)
    parola_chiave_pulita = urllib.parse.quote_plus(parola_chiave)


    #Genera l'url da usare per andare alla pagina dei bandi con i filtri applicati
    url_finale = (
        f"{BASE_URL}/gare?"
        f"title={parola_chiave_pulita}&"
        f"field_cig_value={cig}&"
        f"field_stato_gara_value={stato}&"
        f"field_scelta_del_contraente_target_id={contraente}&"
        f"field_tipologia_gara_target_id={tipologia}"
    )
    return url_finale


def estrai_lista_bandi(url_con_filtri, data_limite=None):
    #INIZIALIZZAZIONE
    link_bandi = []   #Lista dei link raccolti da restituire alla fine del metodo
    link_visti = set()  #Insieme per il controllo die duplicati, verificare "x" in link_visti è molto più rapido che "x" in link_bandi quando la lista cresce.
    pagina_corrente = 0 #il sito parte da pag0, poi si scorrono tutte le pagine contenenti bandi
    stop_per_data = False   #Flag per uscire dal ciclo quando si trova un bando con una data troppo vecchia

    #CICLO PRINCIPALE
    while True:
        url_pagina = f"{url_con_filtri}&page={pagina_corrente}"  #scorre le pagine contenenti i bandi partendo da pag0
        print(f"Richiedo pagina {pagina_corrente + 1}: {url_pagina}\n")

        risposta = requests.get(url_pagina)  #Verifica la disponibilità/funzionamento del server, se da errore interrompiamo subito
        if risposta.status_code != 200:
            print(f"Errore di connessione. Codice: {risposta.status_code}")
            break

        #Tasforma l'HTML in un oggetto navigabile e cerca la tabella dei risultati
        #se non c'è (nessun bando corrispondente ai filtri), esce
        soup = BeautifulSoup(risposta.text, 'html.parser')
        tabella = soup.find('table')
        if not tabella:
            print("Nessun bando trovato (tabella vuota).")
            break

        righe = tabella.find_all('tr') #prende tutte le righe della tabella, inclusa l'intestazione
        righe_dati = righe[1:]  #con questo prende tutti gli elementi dalla posizione 1 in poi (salta l'intestazione)

        #CICLO SULLE RIGHE
        for riga in righe_dati:
            celle = riga.find_all('td') #per ogni riga prende tutte le celle <td>

            # Salta righe vuote o senza abbastanza celle (se ce ne sono meno di 3)
            if len(celle) < 3:
                continue

            #Se è stato impostato un filtro data: cerca il tag <time> nella riga,
            #prende l'attributo datetime (es. "2024-08-29T12:00:00Z"), lo divide su "T" e prende solo la parte data
            # ("2024-08-29"). Confronta le stringhe data in formato YYYY-MM-DD — questo formato si confronta
            # correttamente anche come stringhe ("2024-01-01" < "2024-12-31" è True).
            # Se il bando è più vecchio del limite, imposta stop_per_data = True e fa break
            # — esce dal ciclo for (non dal while), perché i bandi sono ordinati dal più recente al più vecchio
            # , quindi tutti i successivi saranno ancora più vecchi.
            if data_limite:
                tag_tempo = riga.find('time')
                if tag_tempo and tag_tempo.get('datetime'):
                    data_bando = tag_tempo['datetime'].split('T')[0]
                    if data_bando < data_limite:
                        print(f"  [!] Bando del {data_bando} precedente alla data limite. Stop.")
                        stop_per_data = True
                        break

            link_oggetto = celle[2].find('a')  #terza cella, contiene il titolo/link del bando, cerca il tag <a> al suo interno
            if link_oggetto and link_oggetto.get('href'): #prende l'href
                href = link_oggetto['href']
                if href not in link_visti: #se non è gia statp visitato lo aggiunge sia al set che alla lista
                    link_visti.add(href)
                    link_bandi.append(href)

        #CONTROLLO PAGINA SUCCESSIVA
        pulsante_next = soup.find('li', class_='pager__item--next') #cerca l'elemento <li> del pulsante per la pag successiva
        ha_pagina_dopo = pulsante_next is not None and 'disabled' not in pulsante_next.get('class', [])
        #ha_pagina_dopo è True solo se il pulsante per andare a pag successiva non ha la classe disabled (disattivato)
        #pulsante_next.get('class', []) restituisce la lista delle classi CSS dell'elemento (o una lista vuota se non ha l'attributo class)

        if stop_per_data or not ha_pagina_dopo: #se abbiamo trovato un bando troppo vecchio o non ha pagina dopo usciamo dal while
            if not stop_per_data: #se non abbiamo trovato un bando troppo vecchio
                print(f"  [✓] Ultima pagina raggiunta (pagina {pagina_corrente + 1}).")
            break

        pagina_corrente += 1  #altrimenti incrementiamo il numero di pagina e passiamo alla pagina dopo
        time.sleep(1)

    print(f"\n[+] Totale link raccolti: {len(link_bandi)}") #stampa il totale di link raccolti
    return link_bandi #ritorna i link raccolti




'''Passiamo ora allos scraping delle pagine dei singoli bandi'''


#Nuovo estrai dettagli bando
'''
Vecchia versione, non gestisce CIG multipli in un bando
def estrai_dettagli_bando(url_bando):
    dati_bando = {
        "url_provincia": url_bando,
        "tipologia": "Non presente",
        "scelta_contraente": "Non presente",
        "enti": "Non presente",
        "cig": "Non trovato",  # <- aggiunto
        "link_anac": "Non trovato",
        "data_pubblicazione": "Non presente",
        "scadenza_manifestazione": "Non presente",
        "data_scadenza": "Non presente"
    }

    try:
        risposta = requests.get(url_bando, timeout=10)
        if risposta.status_code != 200:
            print(f"[-] Impossibile accedere: {url_bando}")
            return dati_bando

        soup = BeautifulSoup(risposta.text, 'html.parser')

        # 1. TIPOLOGIA GARA
        div_tipo = soup.find('div', class_='field--name-field-tipologia-gara')
        if div_tipo:
            dati_bando["tipologia"] = div_tipo.get_text(strip=True)

        # 2. SCELTA DEL CONTRAENTE
        div_contraente = soup.find('div', class_='field--name-field-scelta-del-contraente')
        if div_contraente:
            dati_bando["scelta_contraente"] = div_contraente.get_text(strip=True)

        # CIG E LINK ANAC
        link_cig = soup.find('a', href=lambda href: href and "dati.anticorruzione.it" in href)
        if link_cig:
            dati_bando["cig"] = link_cig.get_text(strip=True)
            dati_bando["link_anac"] = link_cig['href'].strip()

        # 4. ENTI
        div_enti = soup.find('div', class_='field--name-field-comune')
        if div_enti:
            dati_bando["enti"] = ", ".join(
                div.get_text(strip=True) for div in div_enti.find_all('div') if div.get_text(strip=True)
            )

        # DATA DI PUBBLICAZIONE
        div_pubb = soup.find('div', class_='field--name-field-data-di-pubblicazione')
        if div_pubb and div_pubb.find('time'):
            dati_bando["data_pubblicazione"] = div_pubb.find('time')['datetime'].split('T')[0]

        # SCADENZA MANIFESTAZIONE DI INTERESSE
        div_manif = soup.find('div', class_='field--name-field-data-scadenza-manifestazio')
        if div_manif and div_manif.find('time'):
            dati_bando["scadenza_manifestazione"] = div_manif.find('time')['datetime'].split('T')[0]

        # DATA DI SCADENZA GARA
        div_scad = soup.find('div', class_='field--name-field-data-di-scadenza')
        if div_scad and div_scad.find('time'):
            dati_bando["data_scadenza"] = div_scad.find('time')['datetime'].split('T')[0]

    except Exception as e:
        print(f"[-] Errore durante lo scraping del bando {url_bando}: {e}")

    return dati_bando
'''

def estrai_dettagli_bando(url_bando):

    #Inizializza il dizionario con valori di default
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
        risposta = requests.get(url_bando, timeout=10) #se il server non risonde entro 10 secondi lancia un'eccezione
        if risposta.status_code != 200:
            print(f"[-] Impossibile accedere: {url_bando}")
            return dati_bando #se in risposta arriva un codice diverso da 200, ritorna i dati standard

        # Tasforma l'HTML in un oggetto navigabile
        soup = BeautifulSoup(risposta.text, 'html.parser')


        #Per ogni dato che vogliamo estrarre, si usa soup.find() per cercare nell'HTML il <div> che ha la classe CSS
        #specifica corrispondente a quel campo (es. field--name-field-tipologia-gara),
        #e poi si estrae il testo (o l'attributo) contenuto al suo interno.

        # 1. TIPOLOGIA GARA
        div_tipo = soup.find('div', class_='field--name-field-tipologia-gara')
        if div_tipo: #se non trova niente ritorna None
            dati_bando["tipologia"] = div_tipo.get_text(strip=True)

        # 2. SCELTA DEL CONTRAENTE
        div_contraente = soup.find('div', class_='field--name-field-scelta-del-contraente')
        if div_contraente:
            dati_bando["scelta_contraente"] = div_contraente.get_text(strip=True)

        # 3. CIG (uno o più)
        div_cig = soup.find('div', class_='field--name-field-cig') #prima trova il contenitore di tutti i CIG
        if div_cig:
            for link_cig in div_cig.find_all('a', href=lambda href: href and "dati.anticorruzione.it" in href):
                #per ogni <a> trovato chiama la funzione lambda passandole il valore dell'attributo href
                #lamnda restituisce true solo se href esiste e contiene quella stringa. Solo i tag per cui lamnda restituisce
                #True vengono inclusi nel risultato
                cig_valore = link_cig.get_text(strip=True).upper()  #con .upper() si scrive tutto maiuscolo
                if cig_valore:
                    dati_bando["cig_list"].append(cig_valore) #per ogni link estrae il testo e lo aggiunge alla lista

        # 4. ENTI
        div_enti = soup.find('div', class_='field--name-field-comune')
        if div_enti:
            dati_bando["enti"] = ", ".join(
                div.get_text(strip=True) for div in div_enti.find_all('div') if div.get_text(strip=True)
            )

        # DATA DI PUBBLICAZIONE
        #cerca il div del campo data e dentro cerca il tag time
        div_pubb = soup.find('div', class_='field--name-field-data-di-pubblicazione')
        if div_pubb and div_pubb.find('time'): #verifica entrambe le condizioni: che il div esista e che contenga un tag time
            dati_bando["data_pubblicazione"] = div_pubb.find('time')['datetime'].split('T')[0] #accede all'attributo datetime del tag,
            # che ha formato "2024-08-29T12:00:00Z". .split('T')[0] divide la stringa sulla lettera "T"
            # e prende il primo pezzo: "2024-08-29".

        #FA LO STESSO PER LE ALTRE DUE DATE

        # SCADENZA MANIFESTAZIONE DI INTERESSE
        div_manif = soup.find('div', class_='field--name-field-data-scadenza-manifestazio')
        if div_manif and div_manif.find('time'):
            dati_bando["scadenza_manifestazione"] = div_manif.find('time')['datetime'].split('T')[0]

        # DATA DI SCADENZA GARA
        div_scad = soup.find('div', class_='field--name-field-data-di-scadenza')
        if div_scad and div_scad.find('time'):
            dati_bando["data_scadenza"] = div_scad.find('time')['datetime'].split('T')[0]

    except Exception as e:  #cattura qualsiasi tipo di errore
        print(f"[-] Errore durante lo scraping del bando {url_bando}: {e}")

    return dati_bando #ritorna i dati




def estrai_dati_json_anac(dati_json):  #Questo meotod riceve un dizionario Python già pronto (JSON convertito) e ne estrae i campi che ci interessano
    #INIZIALIZZAZIONE
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

    if not dati_json:  #se dati_json è None perchè scarica json ha fallito o è vuoto
        return risultato # si esce restituendo i dati di default

    blocco_bando = dati_json.get("bando", {}) #cerca la chiave bando nel dizionario principale, se non esiste restituisice un dizionario vuoto
    if blocco_bando:
        #Tre estrazioni dirette
        risultato["numero_gara"] = blocco_bando.get("NUMERO_GARA", "Non presente")
        risultato["oggetto_gara"] = blocco_bando.get("OGGETTO_GARA", "Non presente")
        risultato["tipo_scelta_contraente"] = blocco_bando.get("TIPO_SCELTA_CONTRAENTE", "Non presente")

        lista_cpv = blocco_bando.get("CPV") or [] #CPV è una lista di dizionari perchè un bando potrebbe avere piu CPV
        if lista_cpv:
            risultato["cod_cpv"] = lista_cpv[0].get("COD_CPV", "Non presente")
            risultato["descrizione_cpv"] = lista_cpv[0].get("DESCRIZIONE_CPV", "Non presente")

        lista_cup = blocco_bando.get("CUP") or []
        if lista_cup:
            risultato["cup"] = lista_cup[0].get("CUP", "Non presente")

    # "or []" evita il crash se l'API restituisce None invece di una lista vuota
    for part in dati_json.get("partecipanti") or []:  #itera su tutta la lista dei partecipanti
        if part.get("FLAG_AGGIUDICATARIO"): #prende l'aggiucatario come quello che ha il valore del flag aggiudicatario diverso da 0 o None
            risultato["aggiudicatario"] = part.get("DENOMINAZIONE", "Non presente")
            risultato["aggiudicatario_cf"] = part.get("CODICE_FISCALE", "Non presente")

    return risultato #ritorna i valori ottenuti

def scarica_json_anac(cig, tentativi=10): #vecchi tentativi 5
    for tentativo in range(1, tentativi + 1): #itera sui tentativi
        if tentativo > 1: #se non è il primo tentativo fa una pausa, al primo non avrebbe senso
            print(f"    [!] Tentativo {tentativo}/{tentativi}...")
            time.sleep(8)

        sessione = requests.Session() #ad ogni tentativo viene creata una nuova sessione
        #è essenziale perché Mosparo lega il token alla sessione/cookie ottenuti nello STEP 0.
        # Crearne una nuova ad ogni tentativo garantisce di partire "pulito", senza cookie scaduti o
        #invalidati dal tentativo precedente.
        try:
            # STEP 0: Cookie di sessione
            #Visita la pagina del CIG nel browser ANAC. Non ci interessa il contenuto della risposta — l'unico scopo è far sì che il server imposti i cookie di sessione nella sessione,
            # che verranno automaticamente inclusi in tutte le richieste successive fatte con la stessa sessione
            url_pagina = f"https://dettaglio-cig.anticorruzione.it/cig/{cig}"
            sessione.get(url_pagina, timeout=20) #vecchio 45

            # STEP 1: Submit token
            #Mosparo richiede un "submit token" prima di accettare qualsiasi dato dal form
            url_token = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/request-submit-token"
            payload_token = {
                "pageTitle": "dati-cig",
                "pageUrl": url_pagina,
                "htmlLanguage": "en",
                "publicKey": "jUHeENQtdJN-tmRO0FWpv1QnvTyfWpifwSHMpNOcSck" #è la chiave pubblica del form Mosparo presente sul sito — è statica/fissa per quel form specifico, l'abbiamo trovata ispezionando la richiesta nel browser.
            }
            risposta_token = sessione.post(url_token, json=payload_token, timeout=10) #json=payload_token invia il dizionario come corpo JSON della richiesta POST (requests lo converte automaticamente e imposta Content-Type: application/json).
            dati_token = risposta_token.json() #.json() converte la risposta (testo JSON) in un dizionario Python
            #Estraiamo 3 valori dalla risposta
            submit_token = dati_token["submitToken"] #il token che useremo nei passi successivi
            proof_of_work_result = dati_token["proofOfWorkResult"] #l'hash target che dobbiamo "indovinare" nel passo 2
            proof_of_work_max = dati_token["proofOfWorkMaxNumber"] # il limite massimo entro cui cercare il numero giusto

            # STEP 2: Calcolo proof of work
            #Questo è il meccanismo "anti-bot" che abbiamo decodificato dal file JavaScript di Mosparo:
            # il server fornisce un hash target (proof_of_work_result) e noi dobbiamo trovare un numero n tale che
            # SHA256(submitToken + n) produca esattamente quell'hash.
            proof_number = None
            for n in range(proof_of_work_max + 1): #prova n = 0, 1, 2, ..., proof_of_work_max
                stringa = f"{submit_token}{n}"  #Per ognuno, concatena submit_token e n come stringa, calcola l'hash SHA-256
                hash_risultato = hashlib.sha256(stringa.encode('utf-8')).hexdigest() #restituisce l'hash come stringa esadecimale)
                if hash_risultato == proof_of_work_result:      #confronta con il target
                    proof_number = n                            #Quando trova la corrispondenza, salva n in proof_number
                    break                                       #esce dal ciclo

            if proof_number is None: #Se nessun numero nel range produce l'hash giusto (caso anomalo, non dovrebbe succedere quasi mai), proof_number resta None
                print(f"    [-] Proof of work non trovato per CIG {cig}")
                continue       #salta al tentativo successivo del ciclo esterno — non ha senso proseguire senza questo numero.

            # STEP 3: Validazione form
            #Prepara i "dati del form" come li manderebbe il browser —
            # un oggetto che descrive il campo cig con il suo valore
            url_check = "https://dettaglio-cig.anticorruzione.it/mosparo/api/v1/frontend/check-form-data"
            form_data_stringa = json.dumps({     #converte il dizionario in una stringa JSON compatta (senza spazi dopo virgole e due punti) — necessario perché Mosparo confronta questa stringa esatta per validare l'integrità dei dati.
                "fields": [{"name": "cig", "value": cig, "fieldPath": "input[text].cig"}],
                "ignoredFields": []
            }, separators=(',', ':'))

            payload_check = {
                "formData": form_data_stringa,
                "submitToken": submit_token,
                "proofOfWorkNumber": proof_number,
                "publicKey": "jUHeENQtdJN-tmRO0FWpv1QnvTyfWpifwSHMpNOcSck"
            }
            risposta_check = sessione.post(url_check, data=payload_check, timeout=10) #Nota data=payload_check invece di json=payload_check —
                                                                                        # questa è la differenza che avevamo scoperto: il browser invia
                                                                                        #questi dati come form URL-encoded (Content-Type: application/x-www-form-urlencoded),
                                                                                        # non come JSON. requests con data= (un dizionario) li codifica automaticamente in quel formato.
            dati_check = risposta_check.json()

            if not dati_check.get("valid"): #Se la risposta non contiene "valid": true, la validazione è fallita
                print(f"    [-] Validazione Mosparo fallita per CIG {cig}")
                continue  #si passa al tentativo successivo

            validation_token = dati_check["validationToken"]  #altrimenti si estrae il validation_token, l'ultimo "permesso" necessario.

            # STEP 4: Scarica i dati del CIG
            #Ora che abbiamo entrambi i token Mosparo (submitToken e validationToken),
            # possiamo chiamare l'endpoint vero che restituisce i dati del CIG
            url_cig = "https://dettaglio-cig.anticorruzione.it/api/v1/operations/consultaCIG/1.0/exec"
            payload_cig = {
                "cig": cig,
                "_mosparo_submitToken": submit_token,
                "_mosparo_validationToken": validation_token
            }
            risposta_cig = sessione.post(url_cig, json=payload_cig, timeout=10) #torniamo a json= perché questo endpoint si aspetta JSON (diverso dal form Mosparo).

            if risposta_cig.status_code == 200:   #Se la risposta è 200, prendiamo il JSON
                dati = risposta_cig.json()
                return dati[0] if isinstance(dati, list) and len(dati) > 0 else dati  #isinstance(dati, list) and len(dati) > 0 controlla: è una lista e non è vuota? Se sì, restituiamo il primo elemento (dati[0]) — l'API restituisce i dati del CIG dentro una lista con un solo elemento, quindi "scartiamo" il livello lista esterno, se non è una lista (o è vuota), restituiamo dati così com'è
            else: #Se invece lo status non è 200, stampiamo l'errore e continue al tentativo successivo.
                print(f"    [-] API CIG ha risposto con codice: {risposta_cig.status_code}")
                continue

        except Exception as e:
            print(f"    [-] Errore tentativo {tentativo} per CIG {cig}: {e}") #Qualsiasi eccezione (timeout, errore di rete, JSON malformato, chiave mancante in dati_token["submitToken"] se la risposta non ha quella struttura) viene catturata qui e stampata, poi il ciclo for continua al tentativo successivo automaticamente

    print(f"    [-] Tutti i tentativi falliti per CIG {cig}") #Se tutti i 10 tentativi falliscono (nessun return è stato eseguito), si arriva dopo il for e si restituisce None — che il main interpreta come "impossibile recuperare i dati ANAC".
    return None




