from pick import pick  # <--- Libreria super leggera per le freccette
from scraper import genera_url_con_filtri, estrai_lista_bandi, BASE_URL, estrai_dati_json_anac, scarica_json_anac, estrai_dettagli_bando
from datetime import datetime
from save_data import salva_in_excel
import time


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
    # Creiamo la lista delle opzioni visibili (es: ["Qualsiasi", "Aperta", ...])
    opzioni_visibili = [chiave.title() for chiave in dizionario_mappa.keys()]

    titolo_menu = f"\nSeleziona {nome_filtro.upper()} (Usa le freccette ↑ ↓ e premi INVIO):"

    # pick() gestisce la grafica, le freccette e la barra di selezione da sola
    opzione_scelta, indice = pick(opzioni_visibili, titolo_menu, indicator="=>")

    # STAMPA FISSA: Lasciamo la scelta scritta a schermo prima di passare al prossimo filtro
    print(f"-> {nome_filtro.title()} selezionato: {opzione_scelta}")

    # Ritorniamo la chiave originale tutta minuscola per il nostro dizionario
    return opzione_scelta.lower()


# =====================================================================
# FUNZIONE DI RICHIESTA DATA COMPATIBILE ED INTERATTIVA
# =====================================================================
def richiedi_data_limite():
    """
    Chiede all'utente se vuole filtrare per data di pubblicazione.
    Ritorna la data formattata in 'YYYY-MM-DD' o None se saltata.
    Include un controllo di sicurezza sull'anno inserito.
    """
    risposta = input("\nVuoi filtrare i bandi in base alla data di pubblicazione? (s/n): ").strip().lower()
    if risposta != 's':
        print("-> Nessun filtro data applicato.")
        return None

    # Recuperiamo l'anno corrente in automatico dal computer
    anno_corrente = datetime.now().year

    print("\nInserisci la data limite (verranno estratti solo i bandi da questo giorno in poi):")
    while True:
        try:
            anno = int(input(f"  Inserisci l'ANNO (es. {anno_corrente}): ").strip())

            # --- CONTROLLO DI SICUREZZA SULL'ANNO ---
            if anno < 2010 or anno > anno_corrente:
                print(f"[-] Anno non valido! Deve essere compreso tra il 2010 e il {anno_corrente}.\n")
                continue  # Ricomincia il ciclo del while e richiede l'anno

            mese = int(input("  Inserisci il MESE (1-12): ").strip())
            giorno = int(input("  Inserisci il GIORNO (1-31): ").strip())

            # Controllo di validità della data reale (es. evita il 31 febbraio)
            data_valida = datetime(anno, mese, giorno)
            stringa_data = data_valida.strftime('%Y-%m-%d')
            print(f"-> Filtro data applicato: Bandi pubblicati dal {data_valida.strftime('%d/%m/%Y')} in poi.")
            return stringa_data
        except ValueError:
            print("[-] Data non valida o numeri errati. Riprova ad inserirla.\n")
# =====================================================================
# METODO DI CONTROLLO E AVVIO RICERCA
# =====================================================================

'''
Vecchia versione, non gestisce CIG multipli per i bandi
def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, nome_file=None):
    codice_stato = MAPPA_STATO[stato]
    codice_tipologia = MAPPA_TIPOLOGIA[tipologia]
    codice_contraente = MAPPA_CONTRAENTE[contraente]

    filtri_attivi = []
    if parola_chiave: filtri_attivi.append(f"Oggetto/Parola chiave: '{parola_chiave}'")
    if cig: filtri_attivi.append(f"CIG: '{cig}'")
    if stato != "qualsiasi": filtri_attivi.append(f"Stato: '{stato}' (Codice: {codice_stato})")
    if tipologia != "qualsiasi": filtri_attivi.append(f"Tipologia: '{tipologia}' (Codice: {codice_tipologia})")
    if contraente != "qualsiasi": filtri_attivi.append(f"Scelta Contraente: '{contraente}' (Codice: {codice_contraente})")
    if data_limite: filtri_attivi.append(f"Pubblicati dal: '{data_limite}'")

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

    elenco_link = estrai_lista_bandi(url_ricerca, data_limite=data_limite)

    print(f"\n[+] Trovati {len(elenco_link)} bandi corrispondenti ai filtri e alle date.")
    print("[+] Avvio estrazione dettagli dalle singole pagine...\n")

    lista_risultati = []  # Lista che accumula tutti i bandi

    for i, link in enumerate(elenco_link, 1):
        if i > 1:
            time.sleep(2)

        url_completo = f"{BASE_URL}{link}" if not link.startswith("http") else link
        print(f"[{i}] Analizzo: {url_completo}")

        dati_bando = estrai_dettagli_bando(url_completo)

        # Stampe invariate...
        print(f"    -> CIG: {dati_bando['cig']}")
        print(f"    -> Tipologia Gara: {dati_bando['tipologia']}")
        print(f"    -> Scelta Contraente: {dati_bando['scelta_contraente']}")
        print(f"    -> Ente/Comune: {dati_bando['enti']}")
        print(f"    -> Pubblicato il: {dati_bando['data_pubblicazione']}")
        print(f"    -> Scadenza Manif. Interesse: {dati_bando['scadenza_manifestazione']}")
        print(f"    -> Scadenza Gara: {dati_bando['data_scadenza']}")

        dati_anac = {}
        cig_bando = dati_bando.get("cig", "Non trovato")
        if cig_bando != "Non trovato":
            print(f"    [..] Recupero dati ANAC per CIG {cig_bando}...")
            json_anac = scarica_json_anac(cig_bando)
            if json_anac:
                dati_anac = estrai_dati_json_anac(json_anac)
                print(f"    -> [ANAC] Numero Gara: {dati_anac['numero_gara']}")
                print(f"    -> [ANAC] Oggetto Gara: {dati_anac['oggetto_gara']}")
                print(f"    -> [ANAC] CUP: {dati_anac['cup']}")
                print(f"    -> [ANAC] CPV: {dati_anac['cod_cpv']} - {dati_anac['descrizione_cpv']}")
                print(f"    -> [ANAC] Tipo Scelta Contraente: {dati_anac['tipo_scelta_contraente']}")
                print(
                    f"    -> [ANAC] Aggiudicatario: {dati_anac['aggiudicatario']} (CF: {dati_anac['aggiudicatario_cf']})")
            else:
                print("    -> [ANAC] Impossibile recuperare i dati.")

        # Aggiungiamo il bando alla lista risultati
        lista_risultati.append({
            "provincia": dati_bando,
            "anac": dati_anac
        })

        print("-" * 60)

        # Salvataggio finale (fuori dal loop, allineato con il for)
    if lista_risultati:
        salva_in_excel(lista_risultati, nome_file=nome_file)
'''

def avvia_ricerca_bandi(parola_chiave="", cig="", stato="qualsiasi", tipologia="qualsiasi", contraente="qualsiasi", data_limite=None, nome_file=None):
    #Prende i codici mappati con i dizionari in cima al file
    codice_stato = MAPPA_STATO[stato]
    codice_tipologia = MAPPA_TIPOLOGIA[tipologia]
    codice_contraente = MAPPA_CONTRAENTE[contraente]

    #Costruisce una lista di stringhe descrittive, solo per i filtri effettivamente impostati
    filtri_attivi = []
    if parola_chiave: filtri_attivi.append(f"Oggetto/Parola chiave: '{parola_chiave}'")
    if cig: filtri_attivi.append(f"CIG: '{cig}'")
    if stato != "qualsiasi": filtri_attivi.append(f"Stato: '{stato}' (Codice: {codice_stato})")
    if tipologia != "qualsiasi": filtri_attivi.append(f"Tipologia: '{tipologia}' (Codice: {codice_tipologia})")
    if contraente != "qualsiasi": filtri_attivi.append(f"Scelta Contraente: '{contraente}' (Codice: {codice_contraente})")
    if data_limite: filtri_attivi.append(f"Pubblicati dal: '{data_limite}'")

    print("\n[+] Avvio ricerca sul sito...")
    if filtri_attivi:
        print("  Filtri applicati:")
        for f in filtri_attivi: print(f"    -> {f}")
    else:
        print("  Nessun filtro specifico inserito (mostro tutti i bandi)")

    #chiama il metodo sopra che genera l'url
    url_ricerca = genera_url_con_filtri(
        parola_chiave=parola_chiave, cig=cig, stato=codice_stato,
        tipologia=codice_tipologia, contraente=codice_contraente
    )

    #prende tutti i link dei bandi presenti nelle pagine relative all'url generato precedentemente
    elenco_link = estrai_lista_bandi(url_ricerca, data_limite=data_limite)

    print(f"\n[+] Trovati {len(elenco_link)} bandi corrispondenti ai filtri e alle date.")
    print("[+] Avvio estrazione dettagli dalle singole pagine...\n")

    lista_risultati = []  #accumulerà un dizionario per ogni riga che finirà nell'Excel (un bando può generare più righe se ha più CIG)
    contatore_falliti = 0  #contatore utilizzato per verificare il numero di CIG dove fallisce l'acquisizione dei dati ANAC

    #CICLO PRINCIPALE SUI BANDI
    for i, link in enumerate(elenco_link, 1):   #itera sulla lista dei bandi numerandoli partendo da 1
        if i > 1: #dopo il primo fa una pausa di due secondi tra un bando e l'altro
            time.sleep(2)

        url_completo = f"{BASE_URL}{link}" if not link.startswith("http") else link #se link non inizia già con "http" (è un link relativo come /gara/2024/...), lo concatena con BASE_URL per ottenere l'URL completo.
                                                                                    # Se invece link fosse già un URL completo (caso raro, ma gestito per sicurezza), lo lascia com'è.
        print(f"[{i}] Analizzo: {url_completo}")

        #chiama il metodo che entra nella pagina e restituisce il dizionario con tipologia, scelta contraente, enti, date, e cig_list
        dati_bando = estrai_dettagli_bando(url_completo)

        print(f"    -> Tipologia Gara: {dati_bando['tipologia']}")
        print(f"    -> Scelta Contraente: {dati_bando['scelta_contraente']}")
        print(f"    -> Ente/Comune: {dati_bando['enti']}")
        print(f"    -> Pubblicato il: {dati_bando['data_pubblicazione']}")
        print(f"    -> Scadenza Manif. Interesse: {dati_bando['scadenza_manifestazione']}")
        print(f"    -> Scadenza Gara: {dati_bando['data_scadenza']}")

        lista_cig = dati_bando.get("cig_list", [])

        if not lista_cig:
            print("    -> CIG: Non trovato") #se non c'è CIG stampa questo con gli altri dati del bando
            lista_risultati.append({
                "provincia": dati_bando,
                "anac": {},
                "cig_corrente": "Non trovato"
            })
        else:
            #Caso di uno o più CIG
            print(f"    -> CIG trovati: {len(lista_cig)} -> {', '.join(lista_cig)}")  #stampa quanti e quali CIG sono stati trovati

            for cig_singolo in lista_cig: #per ogni CIG nella lista chiama il metodo scarica_json_anac, che restituisce il json o None
                print(f"\n    [CIG: {cig_singolo}]")
                print(f"    [..] Recupero dati ANAC per CIG {cig_singolo}...")
                json_anac = scarica_json_anac(cig_singolo)

                dati_anac = {} #creo un dizionario vuoto dove andremo ad inserire i dati anac
                if json_anac:  #se json_anac è vero (non vuoto) chiama estraiu_dati_json_anac per ottenere i dati specifici e li stampa
                    dati_anac = estrai_dati_json_anac(json_anac)
                    print(f"        -> [ANAC] Numero Gara: {dati_anac['numero_gara']}")
                    print(f"        -> [ANAC] Oggetto Gara: {dati_anac['oggetto_gara']}")
                    print(f"        -> [ANAC] CUP: {dati_anac['cup']}")
                    print(f"        -> [ANAC] CPV: {dati_anac['cod_cpv']} - {dati_anac['descrizione_cpv']}")
                    print(f"        -> [ANAC] Tipo Scelta Contraente: {dati_anac['tipo_scelta_contraente']}")
                    print(f"        -> [ANAC] Aggiudicatario: {dati_anac['aggiudicatario']} (CF: {dati_anac['aggiudicatario_cf']})")
                else:
                    print("        -> [ANAC] Impossibile recuperare i dati.") #altrimenti stampa un messaggio di fallimento
                    contatore_falliti += 1  # e incrementa il contatore dei fallimenti

                lista_risultati.append({   #Aggiunge una riga alla lista risultati per questo specifico CIG
                    "provincia": dati_bando,
                    "anac": dati_anac,
                    "cig_corrente": cig_singolo
                })  #dati_bando è lo stesso per tutti i CIG dello stesso bando (stessi enti, tipologia, date),
                    # ma anac e cig_corrente sono specifici di questo CIG

                time.sleep(2) #pausa di 2 secondi per non sovraccare i server ANAC

        print("-" * 60) #riga per separare le stampe

    #solo se c'è almeno un risultato, salva in Excel e stampa il riepilogo dei CIG falliti
    if lista_risultati:
        salva_in_excel(lista_risultati, nome_file=nome_file)

        print(f"\n[!] CIG senza dati ANAC: {contatore_falliti}")  #per conteggio CIG falliti


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

    # Menu a scorrimento con le freccette della tastiera
    scelta_stato = selezione_filtri("Stato Gara", MAPPA_STATO)
    scelta_tipologia = selezione_filtri("Tipologia Gara", MAPPA_TIPOLOGIA)
    scelta_contraente = selezione_filtri("Scelta del Contraente", MAPPA_CONTRAENTE)

    # NUOVO: Chiediamo la data (Opzionale)
    scelta_data_limite = richiedi_data_limite()

    # Chiedi il nome del file
    nome_file = input("\nCome vuoi chiamare il file Excel? (premi INVIO per nome automatico): ").strip()
    if nome_file:
        if not nome_file.endswith(".xlsx"):
            nome_file += ".xlsx"
    else:
        nome_file = None  # Verrà generato automaticamente con data e ora

    # Passiamo tutto (inclusa la data) all'avvio della ricerca
    avvia_ricerca_bandi(
        parola_chiave=scelta_oggetto,
        cig=scelta_cig,
        stato=scelta_stato,
        tipologia=scelta_tipologia,
        contraente=scelta_contraente,
        data_limite=scelta_data_limite,
        nome_file=nome_file

    )


