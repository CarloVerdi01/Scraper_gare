"""
Lettura e interpretazione dei PDF di esito allegati ai bandi.

E' il modulo piu' lungo del progetto, e il motivo e' che i PDF non hanno un
formato e l'unico modo per leggerli tutti e' riconoscere caso per caso com'e' fatto quello che
si ha davanti. Da qui deriva l'organizzazione del file.

Dati che si ricavano solo da qui
    Manifestanti, invitati con le loro P.IVA e codici fiscali, offerte
    ricevute, ammesse ed escluse, aggiudicatario, ribasso, importo. Alcune sono
    informazioni che ne' la pagina della Provincia ne' l'API ANAC espongono:
    esistono soltanto dentro i documenti.

I quattro formati riconosciuti
    standard        un solo blocco di sezioni ("Operatori che hanno
                    manifestato interesse", "Invitati", "Offerte"...), valido
                    sia per le gare a lotto unico sia per molte multi-lotto
    per_lotto       il documento si ripete per intero per ogni lotto
    per_lotto_sub   variante del precedente con sotto-sezioni annidate
    multi_lotto_std un unico PDF con sezioni auto-contenute "Lotto N - Titolo"

    rileva_formato_pdf() stabilisce quale sia, e da li' si dirama verso la
    famiglia di estrattori corrispondente. Dentro il formato standard esistono
    poi diverse varianti multi-lotto (CIG in testata, campi etichettati riga
    per riga, sezioni globali...), ciascuna con la sua funzione dedicata: sono
    i casi incontrati sui bandi reali.

Come e' organizzato il file
    Costanti globali          espressioni regolari e delimitatori condivisi
    Helper generici           pulizia dei nomi, ricomposizione delle righe
                              spezzate dall'andata a capo
    Estrattori STANDARD       dalla singola sezione fino al lotto completo
    Estrattori PER_LOTTO      e PER_LOTTO_SUB, le due varianti ripetitive
    Funzioni pubbliche        cio' che usano le interfacce: rilevamento del
                              formato, estrazione completa, e le utilita' su
                              P.IVA e CIG (invitato_con_piva, cig_compatibile,
                              seleziona_pdf_per_cig, costruisci_lista_cig)

Un limite da conoscere
    I PDF scansionati sono immagini senza testo: pdfplumber non ne ricava
    nulla e i campi restano a "Non presente". Servirebbe un OCR, con i rischi
    di lettura che comporta su codici come le P.IVA.
"""


import re
import io
import requests
import pdfplumber
from console import log  # stampa solo se console.VERBOSE e' acceso


# ── Costanti globali ──────────────────────────────────────────────────────────

# Riconosce un codice fiscale/partita IVA COMPLETO: etichetta (anche nella forma
# "P.IVA/C.F." con lo slash) seguita dal codice. Serve a capire se una riga porta già
# il proprio codice o se è troncata dal wrap. Usata da _unisci_continuazioni_a_lettera.
_RE_CODICE_COMPLETO = re.compile(
    r'(?:C\.?F\.?|P\.?\s?I(?:VA)?\.?)\s*(?:/\s*C\.?F\.?\.?)?\s*[.:\s-]*(?:IT-\s*)?[A-Z0-9]{8,}',
    re.IGNORECASE
)

# Riconosce l'inizio di una nuova voce numerata in tutte le forme usate dai bandi:
# "N. NOME", "N) NOME", "N.NOME" (attaccato), "N. 2NOME" (nome che inizia per cifra),
# "N. . NOME" (punto orfano) e "N NOME" (senza separatore, con spazio e lettera obbligatori).
_RE_NUOVA_VOCE_NUM = re.compile(r'\s*(?:\d{1,4}[.)]\s*\.?\s*[A-Za-z0-9]|\d{1,4}\s+[A-Za-z])')


# Delimitatori anagrafici generici (sempre validi per tutti)
_DELIMITATORI_BASE = (
    r',\s*con\s+sede'
    r'|,\s*CAP'
    r'|\n'
    r'|\s+con\s+sede'
    r'|\s+sede\s+legale'   # "NOME sede legale Via..." senza "con" 
    r'|,\s*[Ii]talia\b'
    r'|\s*[Ii]ndirizzo\b'
    r'|\s+\d{5}\b'
    r'|\s*\('
)

# Delimitatori stradali aggressivi (da usare SOLO per l'Aggiudicatario
# o per nomi che contengono un CAP, segno di indirizzo incorporato)
_DELIMITATORI_STRADALI = (
    r'|\s+[Vv]iale\s'
    r'|\s+[Vv]ia\s'
    r'|\s+[Pp]iazza\s'
    r'|\s+[Cc]orso\s'
    r'|\s+[Ll]argo\s'
    r'|\s+[Ll]ocalit[aà]\'?\s'
    r'|\s+[Ll]oc\.\s'
    r'|\s+[Ss]trada\s'      # via per esteso "Strada Querciolare" 
    r'|\s+C/da\s'          # contrada abbreviata 
    r'|\s+[Cc]ontrada\s'
)

_FINE_MANIFESTANTI = [
    r'Numero\s+(?:di\s+)?(?:operatori\s+|soggetti\s+|OO\.?\s*EE\.?\s+)?(?:economici\s+)?(?:pre\s+)?(?:invitati|selezionati|estratti\s+a\s+sorte)',  # OO.EE. es. bando mensa San Marcello; estratti a sorte es. bando Serra Carmignano; "soggetti invitati" es. esito-210
    r'Operatori\s+economici\s+con\s+manifestazione',
    r'Numero\s+(?:di\s+)?offerte\s+(?:ricevute|pervenute)',
    r'Data\s+(?:di\s+)?spedizione',
    r'Nome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario',
]

_FINE_INVITATI = [
    r'Numero\s+(?:di\s+)?offerte\s+(?:ricevute|pervenute)',
    r'Data\s+di\s+spedizione',
    r'Termine\s+per\s+la\s+presentazione',
    r'Nome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario',
]
_FINE_OFFERTE = [
    r'Numero\s+offerte\s+ammesse',
    r'Nome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario',
]

# Classe di caratteri per i nomi delle offerte
# Include ':', '+_,;' per nomi composti
# Classe di caratteri per i nomi delle offerte
# Include ':', '+_,;' per nomi composti
# Include '()' per gestire nomi come (SO.GE.R.T.)
_CLS_OFF = r'[A-Za-z0-9\s\'\.\-–&+_,;:\"()àèìòùÀÈÌÒÙ\/]'
# ── Helper generici ───────────────────────────────────────────────────────────

def estrai_sezione(testo, pattern_inizio, pattern_fine_list):
    """
    Estrae la porzione di testo che inizia subito dopo la prima occorrenza di
    pattern_inizio e termina prima del pattern_fine che compare PIÙ VICINO
    nel testo (posizione minima tra tutti i marcatori, non ordine della lista:
    con l'ordine della lista un marcatore lontano poteva vincere su uno vicino
    facendo sconfinare la sezione).
    Restituisce stringa vuota se pattern_inizio non corrisponde.
    """
    m = re.search(pattern_inizio, testo, re.IGNORECASE)
    if not m:
        return ""
    pos = m.end()
    fine_min = None
    for p in pattern_fine_list:
        mf = re.search(p, testo[pos:], re.IGNORECASE)
        if mf and (fine_min is None or mf.start() < fine_min):
            fine_min = mf.start()
    if fine_min is not None:
        return testo[pos: pos + fine_min]
    return testo[pos:]


def _deduplicaNome(nome):
    """Rimuove un eventuale nome duplicato (es. 'COOP.COOP.' → 'COOP.')."""
    n = len(nome)
    if n < 10:
        return nome
    for split_pos in range(max(5, n // 4), n // 2 + 2):
        first = nome[:split_pos]
        rest = nome[split_pos:]
        first_clean = first.rstrip('. ')
        if len(first_clean) >= 5 and rest.startswith(first_clean):
            return first.strip()
    return nome

def _pulisci_nome(nome, taglia_indirizzi=False):
    """Tronca il nome al primo delimitatore. Se taglia_indirizzi=True, usa anche i pattern stradali."""
    nome = re.sub(r'^\d+[°o]\s*classificato[:\s]+', '', nome, flags=re.IGNORECASE)
    nome = re.sub(r'^[-–]\s+', '', nome)
    # Toglie la precisazione societaria che alcune ragioni sociali portano in coda tra
    # virgolette ("TIPIESSE S.P.A. \"Società Unipersonale soggetta ad attività di direzione
    # e coordinamento HBS Srl\"" -> "TIPIESSE S.P.A.", es. CIG 93743885B3). Solo code lunghe:
    # le virgolette brevi sono parte della denominazione e vanno conservate
    # ("ISTITUTO PROFESSIONALE \"L. EINAUDI\"", es. CIG 9049707676).
    nome = re.sub(r'\s*["“«][^"”»]{25,}["”»]\s*$', '', nome).strip()
    # (?:[\s,;]+|(?<=\.)): toglie la coda "P.IVA..." / "Partita IVA..." quando è separata
    # dal nome da spazi, da una virgola ("SRL ,P.IVA:") o INCOLLATA
    # dopo un punto ("ZOE S.C.S.P.IVA: IT-..."). Copre anche
    # "Partita IVA" scritto per esteso senza punti. Il contesto
    # prima dell'etichetta è obbligatorio, così nomi che iniziano con "PIVA"/"SPIVAK" e
    # sigle come "P.A." restano intatti.
    nome = re.sub(r'(?:[\s,;]+|(?<=\.))(?:Partita\s+IVA|[Pp]\.?\s*[Ii]\.?[Vv]\.?[Aa]\.?)\b.*$', '', nome).strip()
    # Coda "C.F. E P.I. ..." (codice fiscale e partita IVA coincidenti, un solo numero:
    # "CCM FINOTELLO SRL C.F. E P.I. 02022820019".
    # La regola sopra taglia solo a partire da "P.IVA"; qui la coda inizia col "C.F.".
    # Il lookahead pretende che dopo "C.F." segua "E P.I."/"E" finale o un codice, così
    # nomi legittimi come "C.F. COSTRUZIONI SRL" o "ALFA C.F.M. SRL" restano intatti.
    nome = re.sub(
        r'(?:[\s,;]+|(?<=\.))C\.?F\.?(?=\s*(?:[Ee]\s*(?:P\.?\s?I|$)|[.:\s]*(?:IT-\s*)?[A-Z0-9]{8,}))[\s\S]*$',
        '', nome, flags=re.IGNORECASE
    ).strip()
    # etichetta incollata direttamente dopo una LETTERA ("SRLP.IVA: IT-..."):
    # qui il punto in "P.IVA" è obbligatorio,
    # per non troncare nomi che contengono "PIVA" come sequenza di lettere
    nome = re.sub(r'(?<=[A-Za-z])P\.IVA\b.*$', '', nome).strip()
    nome = re.sub(r'\s+\d{11}\s*$', '', nome).strip()
    nome = re.sub(r'\s*IMPRESA O SOCIETÀ\s*$', '', nome, flags=re.IGNORECASE)

    # Costruisce la regex dinamicamente in base al contesto
    delimitatori = _DELIMITATORI_BASE
    if taglia_indirizzi:
        delimitatori += _DELIMITATORI_STRADALI

    nome = re.split(delimitatori, nome, flags=re.IGNORECASE)[0].replace('\n', ' ').rstrip(',').strip()
    return _deduplicaNome(nome)

def _pulisci_offerta(nome):
    """
    Pulisce il nome di un offerente (singolo lotto):
    applica _pulisci_nome e rimuove il suffisso 'RTI costituendo'.
    """
    nome = _pulisci_nome(nome.strip())
    nome = re.sub(
        r'(?:\s+RTI)?\s+costituendo(?:\s+costituendo)?\s*$',
        '', nome, flags=re.IGNORECASE
    )
    # Etichetta anagrafica agganciata in coda al nome ("INTESA SANPAOLO S.P.A.
    # Codice fiscale", CIG 9413762228, 2 lotti): la riga offerta prosegue con "Codice fiscale
    # NNN, P.Iva NNN" che il pattern trascina nel nome. Si taglia l'etichetta.
    nome = re.sub(r'\s+(?:Codice\s+fiscale|C\.?F\.?|P\.?\s*Iva|Partita\s+IVA)\s*$',
                  '', nome, flags=re.IGNORECASE)
    return nome.strip()


def _pulisci_offerta_ml(nome):
    """
    Pulisce il nome di un offerente (multi-lotto):
    rimuove solo il suffisso 'RTI costituendo' (senza _pulisci_nome).
    """
    nome = nome.strip()
    nome = re.sub(
        r'(?:\s+RTI)?\s+costituendo(?:\s+costituendo)?\s*$',
        '', nome, flags=re.IGNORECASE
    )
    return nome.strip()


# ── Estrattori formato STANDARD ───────────────────────────────────────────────

def _unisci_membri_raggruppamento(testo_sez):
    """
    Unisce alla voce precedente le righe dei MEMBRI di un raggruppamento, quando la voce è
    aperta da una sigla (ATI/RTI/RTP/RTS/ATP) e i membri sono elencati su righe proprie:
        "ATI ROSI LEOPOLDO S.P.A. Via Giuseppe Giusti, 67 ... C.F. 00820700474
         CMB ENGINEERING SRL Viale Montegrappa, 276 ... C.F. 02385520974"
    (es. bando ponte Ombrone CIG 8453980CFB). Il PDF dichiara UNA offerta — l'ATI — ma senza
    l'unione la riga del membro viene letta come una seconda offerta.

    Nessun join esistente copre il caso: gli altri helper richiedono che la riga precedente
    sia troncata (senza codice), mentre qui ogni riga ha il proprio codice completo. Il
    segnale è la sigla che apre la voce: finché le righe successive non sono nuove voci
    numerate, appartengono allo stesso raggruppamento.

    Restano estratti sigla+capogruppo (il nome si chiude al primo delimitatore d'indirizzo),
    coerentemente con la convenzione degli altri raggruppamenti.
    """
    righe = testo_sez.split('\n')
    out = []
    for r in righe:
        if (out and r.strip() and out[-1].strip()
                and re.match(r'\s*(?:RT[PIS]|ATI|ATP)\b', out[-1], re.IGNORECASE)
                and not _RE_NUOVA_VOCE_NUM.match(r)
                and _RE_CODICE_COMPLETO.search(r)):
            out[-1] = out[-1].rstrip() + ' ' + r.strip()
        else:
            out.append(r)
    return '\n'.join(out)


def _unisci_parola_spezzata_dopo_trattino(testo_sez):
    """
    Ricongiunge la riga precedente e la successiva quando il wrap ha spezzato A METÀ PAROLA
    il nome di un membro di raggruppamento introdotto dal trattino:
        "ATI E.CO.RES. S.R.L. ... C.F. 04804621219 - GREEN
         WOOD SRL SP 27 KM 0.900 ... P.IVA -08173150726, C.F. 08173150726"
    (es. bando polo scolastico San Giusto, CIG 85853631AD: "GREENWOOD" spezzato in "GREEN"+"WOOD").
    Le due parti vanno attaccate SENZA spazio, altrimenti il nome risulta "GREEN WOOD".

    Nessun join esistente copre il caso: il join sul trattino di legatura richiede che la
    riga finisca col trattino, mentre qui finisce con una parola tronca; e
    _unisci_continuazioni_a_lettera non scatta perché la riga precedente ha già un codice.
    Senza l'unione la continuazione viene letta come una voce autonoma e la lista offerte
    esce con un frammento in più ("WOOD SRL SP 27 KM 0.900, SN -").

    La condizione è volutamente stretta — la riga precedente deve finire con " - PAROLA"
    (il trattino che introduce il membro), la successiva iniziare con maiuscole e portare
    un codice — perché unire senza spazio è distruttivo se applicato a righe spezzate tra
    parole intere (es. CIG 9456283398, "... DI COOPERATIVE\\nSOCIALI ...", che vanno unite CON
    lo spazio e sono già gestite altrove). Verificata su tutti i bandi della suite: tocca
    solo il caso per cui è nata.
    """
    righe = testo_sez.split('\n')
    out = []
    for r in righe:
        if (out and r.strip() and out[-1].strip()
                and not _RE_NUOVA_VOCE_NUM.match(r)
                and re.search(r'\s[-–]\s[A-Z]{2,}$', out[-1].rstrip())
                and re.match(r'[A-Z]{2,}', r)
                and _RE_CODICE_COMPLETO.search(r)):
            out[-1] = out[-1].rstrip() + r.strip()   # niente spazio: è una parola spezzata
        else:
            out.append(r)
    return '\n'.join(out)


def _unisci_continuazioni_a_lettera(testo_sez):
    """
    Unisce alla riga precedente una riga di continuazione che INIZIA CON UNA LETTERA (o con
    una parentesi aperta) e porta un codice C.F./P.I., quando la riga precedente è troncata
    (non ha un codice suo).

    Serve per il wrap che spezza la voce a metà indirizzo o a metà ragione sociale lasciando
    la seconda parte a iniziare con una parola: "4. Camillo Sirianni ... (CZ), 88049 Loc.\\n
    Scaglioni 30, C.F. 01932130790, e P.I. ..." (es. bando arredi Montale, CIG 9039019273),
    "10. CENTRO LEGNO AMBIENTE ... SOC. COOP.\\nA. F. P.IVA: ..." (es. CIG 9323641FF0-3) o
    "1. COOPERATIVA TERRITORIO AMBIENTE MONTANO ACQUACHETA RABBI\\n(C.T.A.) SCA P.IVA: ..."
    (es. bando Buggiano CIG 906684987C, dove la continuazione inizia con la parentesi di una
    sigla).

    La condizione "la riga precedente non ha un codice" è ciò che rende l'unione sicura:
    nelle liste dove ogni voce ha già il suo codice (es. CIG 821750861F, voci senza numero tipo
    "Alioth ... P.IVA/C.F. 02197770502") nessuna riga viene unita. La condizione "la riga
    porta un codice" evita di assorbire le code descrittive ("(Piccola Impresa)" di
    CIG 95278831C9). Elaborato riga per riga e non con re.sub, perché lì la scansione riparte a
    metà riga dopo ogni sostituzione e il gruppo che dovrebbe contenere la riga precedente
    arriva vuoto, aggirando il controllo.
    """
    righe = testo_sez.split('\n')
    out = []
    for r in righe:
        if (out and r.strip()
                and not _RE_NUOVA_VOCE_NUM.match(r)      # non è una nuova voce numerata
                and re.match(r'\s*[A-Za-z(]', r)          # inizia con lettera o parentesi
                and _RE_CODICE_COMPLETO.search(r)        # porta un codice
                and out[-1].strip()
                and not _RE_CODICE_COMPLETO.search(out[-1])):  # la precedente è troncata
            out[-1] = out[-1].rstrip() + ' ' + r.strip()
        else:
            out.append(r)
    return '\n'.join(out)


def _preprocessa_sezioni_std(testo):
    """
    Estrae e pre-processa le cinque sezioni principali del PDF in formato standard:
    manifestanti, invitati, offerte, ammesse, aggiudicatario.

    Restituisce:
        (testo_sez_manifestanti, testo_sez_invitati, testo_sez_offerte,
         testo_sez_ammesse, testo_aggiud_flat)

    Le sezioni non trovate sono stringhe vuote, mai None (vedi estrai_sezione).
    """
    # Normalizza l'etichetta "CF E PI" (codice fiscale e partita IVA coincidenti, un solo
    # numero) nella forma canonica "C.F. E P.I.": alcuni bandi la scrivono senza punti
    # (es: "CCM FINOTELLO SRL CF E PI 02022820019"),
    # forma che nessun pattern a valle riconosce, lasciando il nome sporco e la P.IVA vuota.
    # Applicata al testo intero PRIMA del ritaglio, così vale per manifestanti, invitati,
    # offerte e aggiudicatario in un colpo solo. Il lookahead richiede cifre subito dopo,
    # così nomi come "CF E PIETRO SNC" non vengono toccati.
    testo = re.sub(r'\bC\.?F\.?\s+E\s+P\.?\s?I\.?(?=[\s:.,-]*\d)', 'C.F. E P.I.', testo,
                   flags=re.IGNORECASE)
    # Stessa etichetta con i due codici in ordine INVERTITO ("P.IVA e CF 00932450471",
    # "P.IVA e CF05053140488" col codice attaccato, "P.IVA e C.F. 03866350485"): significa
    # anch'essa "codice fiscale e partita IVA coincidenti, un solo numero". Normalizzata
    # nella stessa forma canonica, così i pattern a valle la vedono già gestita.
    # Il lookahead richiede cifre subito dopo, quindi frasi come "P.IVA e CF sono
    # coincidenti" o nomi tipo "CFERRARI" non vengono toccati.
    # (es. bando decoro urbano Serravalle CIG 8150086FB7)
    testo = re.sub(r'\bP\.?\s?I\.?V?A?\.?\s+E\s+C\.?\s?F\.?(?=[\s:.,-]*\d)', 'C.F. E P.I.', testo,
                   flags=re.IGNORECASE)

    # Stessa etichetta coi due codici uniti dal TRATTINO invece che dalla "e"
    # ("... SEMPLIFICATA C.F.-P.IVA: 03827870613", es. bando plesso Montecatini CIG 90371750BC):
    # senza normalizzarla il pattern aggancia il solo "P.IVA:" e il "C.F.-" resta appiccicato
    # in coda al nome. Il lookahead richiede cifre subito dopo, così non tocca i casi in cui
    # il trattino precede le cifre di un codice ("P.IVA -02890290162" di CIG 95278831C9).
    testo = re.sub(r'\bC\.?F\.?\s*[-–]\s*P\.?\s?I\.?V?A?\.?(?=[\s:.,-]*\d)', 'C.F. E P.I.', testo,
                   flags=re.IGNORECASE)

    # Normalizza l'intestazione dell'aggiudicatario al PLURALE ("Nome e indirizzo degli
    # aggiudicatari:", es. bando brokeraggio Massa e Cozzile SmartCIG ZEECDCAC17) nella forma
    # singolare canonica: i delimitatori _FINE_*, il ritaglio della sezione e i pattern di
    # _estrai_aggiudicatario_std cercano tutti "dell'aggiudicatario", quindi col plurale
    # la sezione non veniva né delimitata né estratta. Normalizzando qui, sul testo intero
    # e prima di ogni ritaglio, tutti i punti a valle funzionano senza doverli toccare.
    testo = re.sub(r"(Nome\s+e[d]?\s+indirizzo\s+)degli\s+aggiudicatari\b",
                   r"\1dell'aggiudicatario", testo, flags=re.IGNORECASE)

    # Normalizza l'intestazione dell'aggiudicatario scritta TUTTA MAIUSCOLA ("NOME E
    # INDIRIZZO DELL'AGGIUDICATARIO:", es. bando manutenzione SR435 CIG 9323641FF0-3, che ha
    # tutte le intestazioni in maiuscolo). Il ritaglio della sezione e i delimitatori
    # _FINE_* cercano "Nome e indirizzo dell'aggiudicatario" in forma case-sensitive
    # (accettano solo "Nome"/"nome"), quindi col maiuscolo la sezione non veniva né
    # delimitata né estratta e l'aggiudicatario usciva "Non presente". Le altre sezioni
    # (invitati, offerte) usano già IGNORECASE e col maiuscolo funzionano.
    testo = re.sub(r"\bNOME\s+E[D]?\s+INDIRIZZO\s+DELL(['’])AGGIUDICATARIO",
                   r"Nome e indirizzo dell\1aggiudicatario", testo)

    # Rimuove il bullet tipografico usato come marcatore di elenco a inizio riga
    # ("• TECHNOLOGICA S.R.L. ... P.IVA: 03136540402", es. bando SP20 CIG 9049707676). I pattern
    # di estrazione sono ancorati a ^ e pretendono che la voce inizi con lettera o cifra:
    # col bullet non agganciano, il nome esce col simbolo e la P.IVA attaccata, e il campo
    # piva resta vuoto. Convertirlo in "- " non basta (anche il trattino blocca l'ancora):
    # va tolto. Solo a inizio riga, così i bullet decorativi a metà testo restano intatti,
    # e senza toccare le liste che usano il trattino come marcatore (es. CIG 8183742D87).
    testo = re.sub(r'^(\s*)[•·▪◦‣∙]\s*', r'\1', testo, flags=re.MULTILINE)

    # Classificazione dimensionale infilata TRA l'etichetta e le cifre ("P.IVA Microimprese
    # - 04804621219", es. bando polo scolastico San Giusto CIG 85853631AD): i pattern si aspettano
    # le cifre subito dopo l'etichetta, quindi saltano quel codice e agganciano il successivo
    # — nel caso dell'ATI, la P.IVA del secondo membro invece che della capogruppo.
    # Il lookahead richiede le cifre dopo, così la stessa parola messa DOPO il codice
    # ("P.I. 02802890612 Microimpresa", es. CIG 90371750BC) non viene toccata.
    testo = re.sub(r'(P\.?\s?I\.?V?A?\.?[\s:]*)(?:Micro|Piccol|Medi|Grand)\w*\s*(?=[-–]?\s*\d{8,})',
                   r'\1', testo, flags=re.IGNORECASE)

    # Etichetta "PIVA" senza punti ("... SOC.CONS. A R. L. PIVA: IT-01963870470", es. bando
    # palestra Datini CIG 8236949159, dove è l'unica voce su cinque a non avere "P.IVA:"): il
    # pattern delle offerte pretende il punto o il trattino dopo la P, quindi non la
    # riconosce e la voce si perde. Il lookahead richiede le cifre dopo (anche con "IT-"),
    # così non tocca né i testi in cui "PIVA" compare senza codice né il refuso già gestito
    # "PIVA E CF" di CIG 8571671EAA.
    testo = re.sub(r'\bPIVA\b(?=[\s:.,-]*(?:IT-)?\d)', 'P.IVA', testo)

    # Etichetta "P.IV" a cui manca la A ("MI.PA. COSTRUZIONI EDILI SRL P.IV/CF 07106311009",
    # es. bando liceo Brunelleschi CIG 812633250E):
    # i pattern accettano "IVA" per intero o "I" col punto, ma non "IV", quindi la voce si
    # perde sia tra gli invitati sia tra le offerte. Il contesto richiesto è stretto —
    # slash, sigla CF e cifre subito dopo — così non tocca testi in cui "P.IV" compaia
    # senza codice né l'ordine invertito "CF/P.IVA".
    testo = re.sub(r'\bP\.?IV(?=\s*/\s*C\.?F\.?[\s:.,-]*\d)', 'P.IVA', testo)

    # Spazio mancante tra il nome e l'indirizzo ("ROSI LEOPOLDO S.P.A.Via Giuseppe Giusti",
    # es. bando ponte Ombrone CIG 8453980CFB): i delimitatori del nome cercano " Via "/" Piazza "
    # con lo spazio davanti, quindi senza non scattano e l'indirizzo resta nel nome.
    # Richiede la maiuscola dopo, così non tocca gli indirizzi già separati né quelli
    # scritti in minuscolo o tutto maiuscolo dentro una riga.
    testo = re.sub(r'(?<=[a-zA-Z.])(Via|Viale|Piazza|Corso|Strada)\s+(?=[A-Z])', r' \1 ', testo)

    # Numero d'elenco con lo spazio dal lato sbagliato del separatore ("9 )Palandri e Belli
    # S.r.l.P.IVA: ...", es. bando parcheggio Pieve a Nievole CIG 9023452427, dove due voci
    # hanno "N )NOME" invece di "N) NOME"). Il lookahead che protegge le voci numerate
    # e i pattern di estrazione si aspettano il separatore attaccato al numero: senza questa
    # normalizzazione quelle righe vengono scambiate per continuazioni e fuse nella
    # precedente, facendo sparire la voce dall'elenco.
    testo = re.sub(r'^(\s*\d{1,4})\s+([.)])', r'\1\2', testo, flags=re.MULTILINE)

    # Fix etichetta P.IVA mandata a capo INSIEME al codice: il PDF spezza la voce lasciando
    # il nome sulla prima riga e "P.IVA/ C.F. 04876970486" tutta sulla seconda (es. bando
    # servizi Serravalle, CIG 821750861F, entry CO&SO dal nome molto lungo). Va ricongiunta alla
    # riga precedente, altrimenti: nei manifestanti/invitati la riga-etichetta diventa una
    # voce spuria ("P.IVA/ C.F.") e il nome vero resta senza codice; nelle offerte la voce
    # non viene catturata affatto e si perde. Applicato al testo intero prima del ritaglio,
    # così vale per tutte le sezioni. Non tocca le P.IVA già inline né il caso opposto
    # ("P.IVA:\n<cifre>", gestito dai fix di sezione).
    testo = re.sub(r'\n(?=\s*P\.?\s?IVA\b)', ' ', testo, flags=re.IGNORECASE)

    testo_sez_manifestanti = estrai_sezione(
        testo,
        # "Numero di operatori (economici) manifestanti" è la forma comune, ma alcuni bandi
        # usano "Manifestazioni di interesse pervenute: N" senza il prefisso "Numero..."
        # oppure "Numero operatori economici
        # che hanno manifestato interesse: n. N".
        # Prefisso opzionale, "pervenute" accanto a "ricevute".
        r'(?:Numero\s+(?:di\s+)?(?:operatori\s+)?(?:economici\s+)?)?'
        r'(?:manifestanti|che\s+hanno\s+manifestato\s+interesse'
        r'|manifestazioni\s+(?:di\s+)?interesse\s+(?:ricevute|pervenute))'
        r'[\s\S]{0,80}?\n',
        _FINE_MANIFESTANTI
    )
    testo_sez_invitati = estrai_sezione(
        testo,
        # "soggetti invitati": variante di etichetta al posto di "operatori economici invitati".
        r'(?:Numero\s+(?:(?:di\s+)?(?:operatori\s+(?:economici\s+)?|soggetti\s+|OO\.?\s*EE\.?\s+))?(?:invitati|(?:pre\s+)?selezionati|estratti\s+a\s+sorte)'
        r'|Operatori\s+economici\s+(?:con\s+manifestazione\s+di\s+interesse\s+(?:completa\s+e\s+corretta\s+)?)?invitati)'
        r'[\s\S]{0,80}?\n',
        _FINE_INVITATI
    )
    testo_sez_offerte = estrai_sezione(
        testo,
        # "pervenute": variante di etichetta.
        r'Numero\s+(?:di\s+)?offerte\s+(?:ricevute|presentate|pervenute)[\s\S]{0,80}?\n',
        _FINE_OFFERTE
    )
    # Sezione "offerte ammesse e valutate" come lista a sé: molti PDF elencano i nomi
    # solo qui (sotto "ricevute" c'è il solo conteggio).
    # Estratta separatamente e restituita a parte; NON si travasa più nelle ricevute.
    testo_sez_ammesse = estrai_sezione(
        testo,
        r'Numero\s+offerte\s+ammesse(?:\s+e\s+valutate)?[^\n]*\n',
        [r'Nome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario', r'Numero\s+offerte\s+escluse']
    )

    # Fix timestamp troncato a fine riga: "12:07:" → "12:07:00"
    # Impedisce che il match lazy di pattern 6 sconfini nella riga successiva
    testo_sez_manifestanti = re.sub(
        r'(\d{2}:\d{2}:)\n', r'\g<1>00\n', testo_sez_manifestanti
    )
    # Fix data+ora concatenate senza spazio: "15/10/202322:12:33" → "15/10/2023 22:12:33"
    testo_sez_manifestanti = re.sub(
        r'(\d{2}/\d{2}/\d{4})(\d{2}:\d{2}(?::\d{2})?)', r'\1 \2', testo_sez_manifestanti
    )
    # Fix ora orfana su riga separata: "DD/MM/YYYY\nHH:MM:SS" → "DD/MM/YYYY HH:MM:SS"
    # Impedisce che il "0" iniziale dell'ora venga letto come numero entry e il resto assorbito come nome
    testo_sez_manifestanti = re.sub(
        r'(\d{2}/\d{2}/\d{4})\s*\n\s*(\d{2}:\d{2}:\d{2})', r'\1 \2', testo_sez_manifestanti
    )
    # Fix nome+data concatenati senza spazio: "S.R.L.05/08/2023" → "S.R.L. 05/08/2023"
    # Impedisce che il lazy match di Pattern 5 scavalchi il newline e assorba la voce successiva
    # [-–]? scarta anche il trattino di legatura: "VASSALLO CALOGERO-04/10/2022"
    # → "VASSALLO CALOGERO 04/10/2022" (es. bando verde Quarrata, CIG 93928633C1)
    # L'anno è 2-4 cifre: alcuni bandi usano l'anno breve attaccato ("MORANI SRL21/06/21")
    # che va staccato prima della normalizzazione sotto.
    testo_sez_manifestanti = re.sub(
        r'([A-Za-z\.])[-–]?(\d{2}/\d{2}/\d{2,4}\b)', r'\1 \2', testo_sez_manifestanti
    )
    # Normalizza l'anno a 2 cifre nelle date "GG/MM/AA" -> "GG/MM/20AA": alcuni bandi usano
    # l'anno breve ("17/06/21 11:33") mentre tutti i pattern manifestanti si aspettano l'anno
    # a 4 cifre come terminatore del nome. Senza normalizzare, la data resta attaccata al nome.
    # Agisce solo su "GG/MM/AA" seguito da un orario (HH:MM), così non tocca le date già a 4
    # cifre né numeri non-data.
    testo_sez_manifestanti = re.sub(
        r'\b(\d{2}/\d{2}/)(\d{2})\b(?=\s*\d{1,2}:\d{2})', r'\g<1>20\g<2>', testo_sez_manifestanti
    )
    # Fix numero d'elenco isolato su riga a sé: alcuni PDF mettono il numero e il punto su
    # una riga tutta loro, col nome sulla riga seguente ("1.\nCafissi Alvaro..."). Senza
    # unirli, i pattern numerati (che vogliono "N. NOME" sulla stessa riga) non agganciano
    # il nome. Unisce "N.\n" alla riga seguente solo se questa inizia con una lettera: non
    # tocca liste già su riga singola. Speculare al fix omonimo in _estrai_offerte_std.
    testo_sez_manifestanti = re.sub(
        r'^(\s*\d+\.)\s*\n(?=[A-Za-z])', r'\1 ', testo_sez_manifestanti, flags=re.MULTILINE
    )
    # Fix voce spezzata su più righe nelle liste PUNTATE (voci introdotte da "- " invece che
    # da un numero, es. bando servizi SdS Pistoiese CIG 8183742D87): un RTI dal nome molto lungo
    # occupa 3 righe e le continuazioni venivano lette come voci autonome (4 manifestanti
    # invece di 2). In questo formato l'unico marcatore affidabile di nuova voce è il
    # trattino iniziale: si unisce alla precedente ogni riga che non ne ha. La guardia
    # (almeno 2 righe puntate) attiva il fix solo su questo formato, lasciando intatte le
    # liste numerate di tutti gli altri bandi.
    _righe_m = [r for r in testo_sez_manifestanti.split('\n') if r.strip()]
    if sum(1 for r in _righe_m if re.match(r'\s*[-–]\s+\S', r)) >= 2:
        testo_sez_manifestanti = re.sub(r'\n(?!\s*[-–]\s)(?=\S)', ' ', testo_sez_manifestanti)

    # Normalizza virgolette tipografiche: " " → " "
    testo_sez_manifestanti = testo_sez_manifestanti.replace('“', '"').replace('”', '"')
    # Fix concatenazioni di fine pagina: pdfplumber a volte non inserisce
    # newline tra l'ultima riga di una pagina e la prima della successiva.
    # → "HH:MM:SS36. NOME" / "HH:MM:SS0023 NOME" / "HH:MM:SS02 NOME" (manifestanti)
    testo_sez_manifestanti = re.sub(
        r'(\d{2}:\d{2}:\d{2})\s*(\d{2,4}(?=\s)|\d+\.)', r'\1\n\2', testo_sez_manifestanti
    )
    # → "manifestazione di interesse del\n22/01/2025" (page break tra "del" e la data):
    #   pattern 1 non riesce a matchare; unisce la data alla riga precedente
    testo_sez_manifestanti = re.sub(
        r'([Mm]anifestazione\s+(?:di\s+)?interesse\s+del)\s*\n\s*(\d{2}/\d{2}/\d{4})',
        r'\1 \2', testo_sez_manifestanti
    )
    # → "22LA CITTADELLA S.N.C." (numero entry incollato al nome senza spazio)
    testo_sez_manifestanti = re.sub(
        r'^(\s*)(\d{1,3})([A-Z])', r'\1\2 \3', testo_sez_manifestanti, flags=re.MULTILINE
    )
    # → "XXXXXXXXXXX38 NOME" / "0208704067729. M.S.C." (invitati): numero entry attaccato
    #   al C.F./P.IVA precedente, con o senza punto
    testo_sez_invitati = re.sub(
        r'(\d{11})\s*(\d{1,3}\.?\s+[A-Za-z])', r'\1\n\2', testo_sez_invitati
    )
    # → "FINDATA SRLS6. studio legale" (invitati): numero entry incollato alla fine
    #   del nome precedente (page break senza newline)
    testo_sez_invitati = re.sub(
        r'([A-Za-z])(\d{1,2}\.)\s*(?=[A-Za-z])', r'\1\n\2 ', testo_sez_invitati
    )
    # → "ARTEA SRL5:" (invitati): artefatto digit+colon a fine riga (numero di pagina)
    testo_sez_invitati = re.sub(
        r'\s*\d+:\s*$', '', testo_sez_invitati, flags=re.MULTILINE
    )
    # → "EDILING SRL 31/12/2021 15:07:37" (invitati): data+ora in coda alla prima entry
    testo_sez_invitati = re.sub(
        r'\s+\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s*$', '', testo_sez_invitati,
        flags=re.MULTILINE
    )
    # → "S.T.A. INGEGNERIA ... 1" (invitati): trailing digit artefatto (numero di pagina)
    testo_sez_invitati = re.sub(
        r'(?<=[A-Za-z\.])\s+\d{1,2}\s*$', '', testo_sez_invitati, flags=re.MULTILINE
    )
    # → "STUDIO CROCE SRLData di spedizione" (invitati): sezione successiva concatenata
    testo_sez_invitati = re.sub(
        r'([A-Za-z])(Data\s+di\s+)', r'\1\n\2', testo_sez_invitati
    )
    # → "...15:49:05;32. NOME" (manifestanti): entry concatenata dopo ; senza newline
    testo_sez_manifestanti = re.sub(
        r';(\d+\.)', r';\n\1', testo_sez_manifestanti
    )
    # Fix "NOME GG/MM/AAAA manifestazione del HH:MM:SS" → "NOME manifestazione del GG/MM/AAAA HH:MM:SS"
    # Evita che Pattern 1 assorba la data nel nome e Pattern 1b la conti come duplicato
    testo_sez_manifestanti = re.sub(
        r'(\d{2}/\d{2}/\d{4})\s+([Mm]anifestazione\s+del)\s+(\d{2}:\d{2}:\d{2})',
        r'\2 \1 \3', testo_sez_manifestanti
    )
    # Fix nome su due righe: "NOME_PARTE1\nPARTE2 GG/MM/AAAA manifestazione" → riga singola
    # Evita che Pattern 1c e 1e creino voci duplicate per la stessa azienda spezzata da un'interruzione di pagina
    testo_sez_manifestanti = re.sub(
        r"([A-Za-z'])\n([A-Za-z][^\n]+?\s+\d{2}/\d{2}/\d{4}\s+[Mm]anifestazione)",
        r'\1 \2', testo_sez_manifestanti
    )
    # Fix "NOME GG/MM/AAAA manifestazione di interesse del HH:MM:SS"
    # Copre la variante con "di interesse" non gestita, e tollera "delHH:" senza spazio
    testo_sez_manifestanti = re.sub(
        r'(\d{2}/\d{2}/\d{4})\s+([Mm]anifestazione\s+di\s+interesse\s+del)\s*(\d{2}:\d{2}:\d{2})',
        r'\2 \1 \3', testo_sez_manifestanti
    )
    # Join nome su due righe: "NOME_PARTE1\nPARTE2 manifestazione di interesse del DATE"
    # Copre la variante in cui la seconda riga inizia col resto del nome + "manifestazione di interesse del DATE"
    testo_sez_manifestanti = re.sub(
        r"([A-Za-z'-])\n\s*([A-Za-z][^\n]+?\s+[Mm]anifestazione\s+di\s+interesse\s+del\s+\d{2}/\d{2}/\d{4})",
        r'\1 \2', testo_sez_manifestanti
    )
    # Strip indirizzo stradale tra nome e "manifestazione": "NOME Via X N CITY manifestazione" → "NOME manifestazione"
    # Richiede numero civico (\d+) per evitare falsi positivi su nomi tipo "NUOVA VIA SRL" (senza numero)
    # [^\n]+? lazy per gestire vie multi-parola: "Via Don Luigi Sturzo 15 Pistoia"
    testo_sez_manifestanti = re.sub(
        r'\s+(?:Via|Viale|Piazza|Corso|Largo)\s+[^\n]+?\s+\d+\s+\S+(?=\s+[Mm]anifestazione)',
        ' ', testo_sez_manifestanti
    )
    # Safety net finale: normalizza "N. NOME" → "N NOME" a inizio riga
    # Eseguito DOPO tutti i fix che possono generare nuovi \n (es. fix del ";", fix fine pagina),
    # così intercetta anche i "10." che finiscono a inizio riga solo dopo quei fix.
    # (?:\s+|(?=[A-Za-z"])): gestisce anche il punto INCOLLATO al nome senza spazio
    # lookahead solo su lettere per non toccare eventuali numeri decimali a inizio riga.
    testo_sez_manifestanti = re.sub(
        r'^(\s*)(\d+)\.(?:\s+|(?=[A-Za-z"]))', r'\1\2 ', testo_sez_manifestanti, flags=re.MULTILINE
    )
    # Fix P.IVA su riga separata: "P.IVA:\nVALUE" → "P.IVA: VALUE"
    # Evita che la riga del valore P.IVA diventi un "numero entry" spurio per Pattern 4
    # (?:IT-\s*)? copre anche l'a-capo DENTRO la P.IVA, dopo il prefisso IT-
    # ("P.IVA: IT-\n09743130156", es. bando brokeraggio Massa e Cozzile, SmartCIG ZEECDCAC17):
    # senza questo, la riga di continuazione diventa una voce spuria e sfasa tutta la lista.
    testo_sez_manifestanti = re.sub(
        r'(P\.IVA:?\s*(?:IT-\s*)?)\n\s*(\d{8,11})',
        r'\1\2', testo_sez_manifestanti, flags=re.IGNORECASE
    )
    # Fix C.F. su riga separata: "C.F.:\nVALUE" → "C.F.: VALUE"
    # I due punti sono opzionali: il wrap può cadere anche dopo un "C.F." nudo
    # ("... Via Vincenzo Gioberti 26, C.F.\n91007700478, e P.I. ...", es. bando cani
    # randagi Monsummano, CIG 7904504B0B). Senza, la riga di continuazione diventa una voce
    # a sé e il nome estratto è un frammento del codice ("7700478, e P.I.").
    # Stessa forma già usata dal fix gemello nel blocco invitati.
    testo_sez_manifestanti = re.sub(
        r'(C\.F\.\s*:?)\s*\n\s*([A-Z0-9]{11,16})\b',
        r'\1 \2', testo_sez_manifestanti, flags=re.IGNORECASE
    )
    # Strip C.F. e relativo valore (non usato dall'estrattore; evita che il numero C.F. su riga
    # separata venga letto da Pattern 4 come numero-entry e assorba cross-line il nome successivo)
    testo_sez_manifestanti = re.sub(
        r'\s+C\.F\.:\s*\S+', '', testo_sez_manifestanti, flags=re.IGNORECASE
    )
    # Fix data trailing negli invitati senza P.IVA: "0001 NOME GG/MM/AAAA" → "0001 NOME"
    # Permette a Fallback 4 (nome a fine riga) di estrarre anche le entry con data in coda
    testo_sez_invitati = re.sub(
        r'^(\s*\d+[.)]?\s+[A-Za-z][^\n]*?)\s+\d{2}/\d{2}/\d{4}\s*$',
        r'\1', testo_sez_invitati, flags=re.MULTILINE
    )
    # Fix parola concatenata dopo apostrofo in invitati (page break senza spazio):
    # "SOCIETA'COOPERATIVA" → "SOCIETA' COOPERATIVA"
    testo_sez_invitati = re.sub(r"([A-Z]{3,}')([A-Z]{3,})", r'\1 \2', testo_sez_invitati)
    # Fix nome su due righe in invitati: "NOME_PARTE1\nPARTE2 P.IVA" → riga singola
    # Permette al regex P.IVA principale di catturare anche le entry spezzate da un'interruzione di pagina.
    # Il lookahead negativo evita il join quando la prima riga contiene già una P.IVA/P.I.:
    # in quel caso è un'entry completa
    testo_sez_invitati = re.sub(
        r"^(?!.*P\.\s?(?:IVA|I))(.*[A-Za-z'])\n([A-Za-z][^\n]+?\s*P\.IVA)",
        r'\1 \2', testo_sez_invitati, flags=re.MULTILINE
    )
    # Fix continuazione indirizzo/codici a capo negli invitati: l'entry sta su due righe,
    # nome e inizio indirizzo sulla prima ("INTESA SANPAOLO S.P.A., con sede legale in
    # Torino (TO),") e il resto sulla seconda ("10121 PIAZZA SAN CARLO 156, C.F. ..., e
    # P.I. ..."). Il fix "nome su due righe" sopra non basta: pretende che la seconda riga
    # inizi con una lettera, mentre qui inizia col CAP. Senza ricongiungerle, i pattern
    # agganciano la riga di continuazione e restituiscono un pezzo d'indirizzo come nome.
    # Unisce solo righe che contengono un codice C.F./P.I. e non sono una nuova voce.
    # Il lookahead protegge tutte le forme di voce numerata usate dai bandi: "N. NOME",
    # "N) NOME", "N NOME" senza punto (es. "1 ETT S.R.L. P.IVA..." di CIG 9060289302),
    # "N.NOME" col numero attaccato (es. "2.CSA ScpA P.IVA..." di CIG 95949535B2), "N. 2NOME"
    # con nome che inizia per cifra (es. "7. 2ZERO PROJECTS ..." di CIG 90445094F0) e
    # "N. . NOME" col punto orfano dopo il numero (es. "21. . VE.MA. Progetti ..." idem).
    # Col separatore (punto/parentesi) lo spazio è opzionale e si ammette un punto orfano;
    # senza separatore restano obbligatori sia lo spazio sia la lettera iniziale, altrimenti
    # un CAP di continuazione ("51100 Pistoia CF ...") o un codice nudo a capo
    # ("05260330872") verrebbero scambiati per nuove voci e i join legittimi salterebbero.
    # Unisce le continuazioni che iniziano con una lettera quando la riga precedente è
    # troncata dal wrap ("10. CENTRO LEGNO AMBIENTE ... SOC. COOP.\nA. F. P.IVA: ...": senza, il nome estratto è il frammento
    # finale ("A. F."). Stesso helper usato in _estrai_offerte_std (esito_gara-6); i join
    # a regex qui sotto coprono solo le continuazioni che iniziano con cifre o col codice.
    # Applicato SOLO alla sezione invitati, non al testo intero: lì ogni riga è un operatore
    # economico, mentre altrove un telefono, un fax o un protocollo a 11 cifre a fine riga
    # ("Comune di Pistoia Tel. 05731234567") verrebbe scambiato per una partita IVA.
    # Il lookahead che protegge le righe già etichettate richiede l'etichetta SEGUITA da
    # cifre: senza quel vincolo, con IGNORECASE, la sequenza "pi" dentro un nome comune
    # ("Euroimpianti", "Cortesi Impianti") lo farebbe scattare bloccando il fix proprio dove
    # serve; e senza IGNORECASE non vedrebbe le etichette minuscole già presenti,
    # duplicandole ("Azienda srl p.iva P.IVA: 0123...").
    testo_sez_invitati = re.sub(
        r'^(?![^\n]*(?:C\.?F\.?|P\.?\s?I\.?V?A?\.?)\s*[/:.,\s-]*\d)'
        r'([^\n]*[A-Za-z][^\n]*?)\s+(\d{11})\s*$',
        r'\1 P.IVA: \2', testo_sez_invitati, flags=re.MULTILINE | re.IGNORECASE
    )
    testo_sez_invitati = _unisci_continuazioni_a_lettera(testo_sez_invitati)
    testo_sez_invitati = re.sub(
        r'\n(?!\s*(?:\d{1,4}[.)]\s*\.?\s*[A-Za-z0-9]|\d{1,4}\s+[A-Za-z]))'
        r'(\s*\d{1,11}\b[^\n]*?(?:C\.F\.|P\.I\.)[^\n]*'
        # Dopo l'etichetta devono seguire davvero delle cifre: senza questo vincolo una
        # ragione sociale che INIZIA con "C.F." ("C.F.C. Consorzio fra costruttori
        # soc.coop. ...", es. bando barriere SP CIG 75695638FD) viene scambiata per una
        # continuazione e fusa nella voce precedente, sparendo dall'elenco.
        r'|\s*(?:C\.F\.|e\s+P\.I\.|P\.I\.)[\s:.,-]*(?:IT-\s*)?[A-Z0-9]{8,}[^\n]*)',
        r' \1', testo_sez_invitati, flags=re.IGNORECASE
    )
    # Fix P.IVA su riga separata in invitati — stesso fix del blocco manifestanti
    testo_sez_invitati = re.sub(
        r'(P\.IVA:?\s*(?:IT-\s*)?)\n\s*(\d{8,11})',
        r'\1\2', testo_sez_invitati, flags=re.IGNORECASE
    )
    # Fix C.F. su riga separata: "C.F.:\nVALUE" → "C.F.: VALUE"
    testo_sez_invitati = re.sub(
        r'(C\.F\.:\s*)\n(\s*\S+)',
        r'\1\2', testo_sez_invitati, flags=re.IGNORECASE
    )
    # Entry con SOLO C.F. e nessuna P.IVA ("2ZERO PROJECTS S.R.L.T.P. C.F.: 01686400530",
    # es. bando ponti CIG 90445094F0): promuove il C.F. numerico a P.IVA prima dello strip,
    # altrimenti la riga resta senza codice e l'entry si perde. Solo su righe senza P.IVA.
    # Il lookahead copre sia "P.IVA" per esteso sia "P.I." abbreviato: righe come
    # "... C.F. 00799960158, e P.I. 11991500015" hanno già la P.IVA vera e NON vanno
    # promosse, altrimenti il C.F. la soppianta.
    testo_sez_invitati = re.sub(
        r'^(?![^\n]*P\.\s?I(?:VA)?[.:\s])([^\n]*?)\s+C\.F\.\s*:?\s*(\d{11})\b',
        r'\1 P.IVA: \2', testo_sez_invitati, flags=re.MULTILINE | re.IGNORECASE
    )
    # Punto orfano dopo il numero d'elenco ("21. . VE.MA.", es. bando ponti CIG 90445094F0)
    testo_sez_invitati = re.sub(
        r'^(\s*\d+\.)\s*\.\s+', r'\1 ', testo_sez_invitati, flags=re.MULTILINE
    )
    # C.F. mandato a capo dal wrap del PDF ("...872C.F.\n05260330872\n32. Breng"):
    # riunisce l'etichetta col suo codice sulla riga precedente, così lo strip del
    # C.F. non lascia una riga-numero orfana che poi la deduplica scarta, facendo
    # sparire l'entry (es. bando ponti CIG 90445094F0, entry SG.INARCH)
    testo_sez_invitati = re.sub(
        r'(C\.F\.\s*:?)\s*\n\s*([A-Z0-9]{11,16})\b', r'\1 \2', testo_sez_invitati, flags=re.IGNORECASE
    )
    # Prefisso codice pratica "n. <ID> Nome" dopo il numero d'elenco
    # ("1. n. 377491 C.R.M. Escavazioni S.r.l.", es. bando Migliana Cantagallo):
    # rimuove il "n. NNNNNN" così il nome resta pulito.
    testo_sez_invitati = re.sub(
        r'^(\s*\d+[.)]\s*)n\.\s*\d{4,7}\s+', r'\1', testo_sez_invitati, flags=re.MULTILINE
    )
    # Campi INVERTITI nel documento: "P.IVA: <codice fiscale alfanumerico> C.F.: <cifre>"
    # (es. bando CIG 9067034129, entry GIOVANNI ORISTANIO): scambia i due valori così la
    # P.IVA numerica torna sotto l'etichetta giusta prima dello strip del C.F.
    testo_sez_invitati = re.sub(
        r'(P\.IVA:?\s*)([A-Za-z]{6}[A-Za-z0-9]{10})(\s+C\.F\.:?\s*)(\d{11})\b',
        r'\1\4\3\2', testo_sez_invitati, flags=re.IGNORECASE
    )
    # Strip C.F. dal testo invitati
    testo_sez_invitati = re.sub(
        r'\s+C\.F\.:\s*\S+', '', testo_sez_invitati, flags=re.IGNORECASE
    )

    # Sezione aggiudicatario con newline appiattiti (fix nome su più righe)
    m_aggiud_match = re.search(
        r'[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[\s\S]{0,800}',
        testo
    )
    testo_aggiud_flat = m_aggiud_match.group(0).replace('\n', ' ') if m_aggiud_match else ""

    return testo_sez_manifestanti, testo_sez_invitati, testo_sez_offerte, testo_sez_ammesse, testo_aggiud_flat


def _elenco_nomi_nudi(testo_sez):
    """
    Fallback per gli elenchi di operatori scritti come NOMI NUDI, uno per riga,
    senza numerazione ne' data ne' anagrafica obbligatoria (es. lavori risanamento facciata CIG 8076620DA8).
    Le cascate di pattern standard, che si ancorano al numero di riga o
    all'anagrafica in coda, qui catturano solo la prima voce.

    Si legge dalla riga DOPO l'intestazione fino alla prima riga che apre una
    nuova sezione; l'eventuale anagrafica in coda ("... P.IVA IT-0231...",
    "... CF/P.IVA 0044...") viene tagliata dal nome.
    Restituisce lista di stringhe pulite (vuota se non applicabile).
    """
    _stop = re.compile(
        r'^\s*(?:Numero|Data\s+di|Nome\s+e\s+indirizzo|Ribasso|Valore|Subappalto|'
        r'Organo|Tipo\s+di|Criterio|CPV|Descrizione|Amministrazione|Per\s+conto)',
        re.IGNORECASE
    )
    nomi = []
    _righe = (testo_sez or "").split("\n")
    # la sezione puo' arrivare CON o SENZA la riga di intestazione a seconda
    # del preprocessore: si salta solo se la prima riga e' un'intestazione.
    if _righe and re.match(r'^\s*(?:Numero|Elenco|Operatori)', _righe[0], re.IGNORECASE):
        _righe = _righe[1:]
    for riga in _righe:
        r = riga.strip()
        if not r or _stop.match(r):
            break
        r = re.split(r'\s+P\.?\s*IVA\b|\s+CF/P\.IVA\b|\s+C\.F\.', r)[0].strip()
        if r:
            nomi.append(_pulisci_nome(r))
    return nomi


def _estrai_manifestanti_std(testo_sez):
    """
    Estrae la lista di nomi grezzi dei manifestanti dalla sezione delimitata.
    Applica una cascata di pattern (1, 1b, 2-8, 1c, 1e).
    Restituisce lista di stringhe (non ancora passate a _pulisci_nome).
    """
    # 1. "0001 NOME manifestazione [di [interesse]] del"
    #    ^\s* tollera indentazioni PDF; \s* cattura anche "0003GIANANTONIO"
    #    "di interesse" interamente opzionale ("manifestazione del")
    #    \d{1,4}: gestisce 1 cifra (es. "1 CO&SO-CONSORZIO") oltre al classico 4 cifre
    manifestanti = re.findall(
        r'^\s*\d{1,4}\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s*[Mm]anifestazione\s+(?:di\s+)?(?:interesse\s+)?del',
        testo_sez, re.MULTILINE
    )
    # 1b. "0001 NOME GG/MM/AAAA manifestazione del HH:MM" — gira sempre, unisce al set
    m1b = re.findall(
        r'^\s*\d{1,4}\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+\d{2}/\d{2}/\d{4}\s+manifestazione\s+del\s+\d{2}:\d{2}',
        testo_sez, re.MULTILINE | re.IGNORECASE
    )
    if m1b:
        _visti1 = {n.strip().upper() for n in manifestanti}
        for n in m1b:
            if n.strip().upper() not in _visti1:
                manifestanti.append(n.strip())
                _visti1.add(n.strip().upper())
    if not manifestanti:
        # 2. "1. NOME manifestazione di interesse del"
        manifestanti = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+[Mm]anifestazione\s+(?:di\s+)?interesse\s+del',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    if not manifestanti:
        # 3. "0001 NOME [del] GG/MM/AAAA" — ancorato a inizio riga (^ + MULTILINE):
        # senza ancora, le cifre finali dell'ORARIO della riga precedente venivano
        # usate come numero d'elenco e il numero vero finiva nel nome
        # "del" opzionale e giorno anche a 1 cifra: nello stesso PDF convivono
        # "NOME 02/05/2024", "NOME del 06/05/2024" e "NOME 4/05/2024"
        # (?:\d{4}(?=[A-Za-z0-9])|\d{1,4}\s+) accetta il numero incollato al nome
        # senza spazio, anche quando il nome inizia con una CIFRA:
        # "0003AQUASPORT", "00523C SRL".
        # Il numero col PUNTO attaccato al nome ("3.CRISTOFORO")
        # è normalizzato a monte dal safety net di _preprocessa_sezioni_std.
        # (?!\n\s*\d): il nome può proseguire sulla riga successiva (nomi spezzati)
        # ma NON scavalcare in una riga che inizia con cifre —
        # altrimenti una entry senza data (solo orario) ingoia la entry seguente
        # Giorno \d{1,3} (non \d{1,2}): tollera il refuso "del 112/09/2024" del PDF
        # (giorno a 3 cifre, es. bando SP17-SP24 Lotto B, CIG B330FAF9D8, voce 0109
        # LAUDANTE COSTRUZIONI SRL) che altrimenti fa scartare l'intera riga e
        # perdere l'operatore. Un giorno reale a 3 cifre non esiste: il pattern
        # allargato può solo recuperare righe che prima fallivano.
        # \s* (non \s+) prima di "del": tollera il refuso "del incollato al nome"
        # senza spazio (es. "COBESCO SRLdel 04/09/2024", presente in CIG B2E0277731) che
        # altrimenti lascia "del" attaccato in coda al nome estratto ("COBESCO
        # SRLdel"). Il "del" minuscolo seguito dalla data non è mai parte del
        # nome, quindi separarlo è sempre corretto.
        manifestanti = re.findall(
            r'^\s*(?:\d{4}(?=[A-Za-z0-9])|\d{1,4}\s+)\s*([A-Za-z0-9"](?:(?!\n\s*\d)[A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/])*?)\s*(?:del\s+)?\d{1,3}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    if not manifestanti:
        # 4. "N. NOME P.IVA:" — lista numerata con P.IVA nel testo
        manifestanti = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s*P\.IVA\b',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
        manifestanti = [m.rstrip('. ').strip() for m in manifestanti]
    if not manifestanti:
        # 5. "N. NOME GG/MM/AAAA" — senza orario
        manifestanti = re.findall(
            r'^\s*\d+[.)]?\s+([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+\d{2}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    if not manifestanti:
        # 6. "0001 NOME GG/MM/AAAA HH:MM:SS"
        manifestanti = re.findall(
            r'^\s*\d{1,4}\s+([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    if not manifestanti:
        # 7. Numero attaccato senza spazio
        manifestanti = re.findall(
            r'^\s*\d{2,4}\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+\d{1,2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    if not manifestanti:
        # 8. "(ID: 0001) NOME"
        manifestanti = re.findall(
            r'^\s*\(ID:\s*\d+\)\s+([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s*$',
            testo_sez, re.MULTILINE
        )
    if not manifestanti:
        # 9. Nessun numero di prefisso: "NOME manifestazione [di interesse] del DATE"
        #    Usato come last resort dopo il join a riga singola e lo strip dell'indirizzo in preprocessing
        manifestanti = re.findall(
            r'^([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s+[Mm]anifestazione\s+(?:di\s+)?(?:interesse\s+)?del',
            testo_sez, re.MULTILINE
        )
        manifestanti = [m for m in manifestanti if 'offerta' not in m.lower()]
    # 1c. "0007 NOME" / "01 NOME" — nome senza data né timestamp (aggiuntivo, non fallback)
    #     \d{1,4}: gestisce sia il formato 4 cifre (es. 0007) che 1-3 cifre (es. 01 COS.BO SRL)
    m1c = re.findall(
        r'^\s*(?:\d{4}(?=[A-Za-z0-9])|\d{1,4}\s+)\s*([A-Za-z0-9"][A-Za-z0-9\s\'\.\-–&+_,;:"()àèìòùÀÈÌÒÙ\/]+?)\s*$',
        testo_sez, re.MULTILINE
    )
    if m1c:
        _visti1c = set()
        for n in manifestanti:
            _visti1c.add(n.strip().upper())
            # anche la SOLA PRIMA RIGA dei nomi multi-riga: il Pattern 3 può aver
            # catturato "PRIMA RIGA\nSECONDA RIGA" scavalcando l'a-capo, e qui la
            # prima riga fisica ricomparirebbe da sola creando un doppione dopo la
            # pulizia
            _visti1c.add(n.strip().upper().split('\n')[0].strip())
        for n in m1c:
            n_clean = n.strip()
            # orario orfano a fine riga senza data ("NOME 11:53:18"): lo rimuove dal nome
            n_clean = re.sub(r'\s+\d{1,2}:\d{2}(?::\d{2})?\s*$', '', n_clean)
            # \d{1,2}: riconosce anche date malformate con giorno a 1 cifra ("4/05/2024"),
            # altrimenti la riga verrebbe ri-aggiunta sporca
            # P\.IVA senza \b iniziale: riconosce anche l'etichetta INCOLLATA al nome
            # ("ALCANTARA SRLP.IVA: IT-...")
            if (re.search(r'\d{1,2}/\d{2}/\d{4}', n_clean)
                    or re.search(r'manifestazione', n_clean, re.IGNORECASE)
                    or re.search(r'P\.IVA\b', n_clean, re.IGNORECASE)):
                continue
            if n_clean.upper() not in _visti1c and len(n_clean) > 2:
                manifestanti.append(n_clean)
                _visti1c.add(n_clean.upper())
    # 1e. Nome su due righe con data prima di "manifestazione"
    m1e = re.findall(
        r'^\s*\d{1,4}\s*([^\n]+)\n([^0-9\n][^\n]*?)\s+\d{2}/\d{2}/\d{4}\s+[Mm]anifestazione',
        testo_sez, re.MULTILINE
    )
    if not manifestanti:
        # Fallback nomi puri: lista senza numerazione, senza data e senza P.IVA,
        # un nome per riga (es. "RSPP Firenze srl", "BEN srl"...).
        for riga in testo_sez.split('\n'):
            r = riga.strip()
            if (len(r) > 3
                    and not re.match(r'^\s*(?:Numero|Data|Termine|Nome\s+e|Operatori\s+economici\s+con)', r,
                                     re.IGNORECASE)
                    and not re.search(r'\d{2}/\d{2}/\d{4}', r)
                    and re.search(r'[A-Za-z]{3}', r)):
                manifestanti.append(r)
    return manifestanti


def _cf_da_riga(riga, piva="Non presente"):
    """
    Ricava il CODICE FISCALE dalla riga di un operatore.

    Il campo si valorizza SOLO se il documento dichiara davvero un codice
    fiscale: la P.IVA non e' il codice fiscale e scriverla qui affermerebbe
    qualcosa che il PDF non dice. Se il PDF riporta la sola P.IVA, il campo
    resta "Non presente".

    L'unica eccezione e' l'etichetta UNICA "CF/P.IVA 04697600486": li' e' il
    documento stesso a dichiarare che quel codice vale come entrambi.

    Casi riconosciuti:
      - "C.F. 01965240789 e P.I. 01418060859" -> due codici distinti;
      - PERSONE FISICHE, C.F. alfanumerico di 16 caratteri;
      - "CF/P.IVA 04697600486" -> codice unico, vale per entrambi i campi.
    """
    if not riga:
        return "Non presente"
    # C.F. di persona fisica: 16 caratteri alfanumerici, forma inconfondibile
    m = re.search(r'C\.?F\.?[\s.:/]*([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])\b',
                  riga, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # Etichetta unica "CF/P.IVA": il PDF dichiara un codice solo per entrambi.
    if re.search(r'C\.?F\.?\s*/\s*P', riga, re.IGNORECASE):
        return piva if piva and piva != "Non presente" else "Non presente"
    # C.F. numerico dichiarato a parte, con la sua etichetta.
    m = re.search(r'C\.?F\.?[\s.:,/]*(?:IT-\s*)?(\d{11})', riga, re.IGNORECASE)
    if m:
        return m.group(1)
    return "Non presente"


def _op(nome, piva="Non presente", riga=None):
    """
    Costruisce il dict di un operatore con entrambi gli identificativi.
    Usato dai rami cosi' che la struttura resti uniforme: {"nome","piva","cf"}.
    """
    _p = (piva or "Non presente").strip()
    return {"nome": nome, "piva": _p, "cf": _cf_da_riga(riga, _p)}


def _estrai_invitati_std(testo_sez):
    """
    Estrae la lista degli operatori invitati dalla sezione delimitata.
    Tenta prima con P.IVA, poi con fallback solo-nomi.
    Restituisce lista di dict {"nome": ..., "piva": ...} o lista vuota.
    Non gestisce e_come_sopra né il fallback num==num (restano nel chiamante).
    """
    if not testo_sez:
        return []

    # — Tentativo P.IVA —
    # ^\s* tolera indentazioni PDF; \s* prima di P.IVA gestisce spazio assente
    # Prefisso C.F. opzionale prima dell'etichetta: "C.F. e P.I.: NNN",
    # "CF/P.iva NNN" ordine invertito e "CF <codice fiscale> P.iva NNN" per le persone
    # fisiche
    # (?:\s*/\s*C\.?F\.?\.?)? accetta anche "P.IVA/C.F.", "P.IVA/ C.F.", "P.IVA/CF"
    # Tre tolleranze per i refusi del PDF (es. bando ponti Prato, CIG 8571671EAA, entry RINA):
    #   P[.\-]? — il punto dell'etichetta è opzionale ("PIVA E CF" invece di "P.IVA E CF");
    #             resta comunque richiesto "IVA"/"I." dopo la P, quindi nomi come "PISA"
    #             o "PI.MA." non vengono scambiati per etichette
    #   C\.?F\.?\s* — dopo "CF" può esserci una virgola invece di uno spazio
    #   [,\s]*  — virgola spuria al posto della prima cifra del codice ("CF ,3746550102")
    # Il nome NON può scavalcare l'a-capo (spazio/tab ma non \n): i nomi spezzati veri
    # sono già uniti dal preprocessing, mentre una entry SENZA etichetta P.IVA
    # inghiottiva quella successiva
    _PFX_CF = r'(?:C\.?F\.?\s*(?:[Ee]\s+|/\s*|:?\s*[A-Za-z0-9]{16}\s+))?'
    _CLS_INV = r'[A-Za-z0-9 \t\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]'
    operatori = re.findall(
        r'^\s*\d+[.)]?\s*([A-Za-z0-9]' + _CLS_INV + r'+?)\s*' + _PFX_CF + r'P[.\-]?\s?(?:IVA|I\.?)(?:\s*/\s*C\.?F\.?\.?)?[.:\s]*(?:[Ee]\s+C\.?F\.?\s*)?[,\s]*(?:IT-\s*)?(\d{8,11})',
        testo_sez, re.MULTILINE | re.IGNORECASE
    )
    if not operatori:
        # [-–]? : elenco puntato col TRATTINO invece della numerazione
        # ("- CITTA' FUTURA S.C. P.IVA: ..."). Senza questo il
        # trattino restava incollato al nome e il fallback non agganciava.
        operatori = re.findall(
            r'^\s*[-–]?\s*([A-Za-z0-9]' + _CLS_INV + r'+?)\s*' + _PFX_CF + r'P[.\-]?\s?(?:IVA|I\.?)(?:\s*/\s*C\.?F\.?\.?)?[.:\s]*(?:[Ee]\s+C\.?F\.?\s*)?[,\s]*(?:IT-\s*)?(\d{8,11})',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
    # Recupero etichetta OMESSA: "N. NOME: 11533421001" senza "P.IVA" nel testo.
    # Aggiunge solo le righe la cui P.IVA non è già stata catturata.
    if operatori:
        _pive_gia = {p for _, p in operatori}
        for _mo in re.finditer(r'^\s*\d+[.)]\s*([A-Za-z][^\n:]+?):\s*(\d{11})\s*$',
                               testo_sez, re.MULTILINE):
            if _mo.group(2) not in _pive_gia:
                operatori.append((_mo.group(1).strip(), _mo.group(2)))
                _pive_gia.add(_mo.group(2))
    # Pass supplementare: recupera P.IVA non catturate
    if operatori:
        _pive_inv = {p for _, p in operatori}
        for _m in re.finditer(r'P\.IVA[:\s]*(?:[Ee]\s+C\.?F\.?\s+)?(?:IT-\s*)?(\d{8,11})', testo_sez, re.IGNORECASE):
            _piva = _m.group(1)
            if _piva not in _pive_inv:
                _before = testo_sez[:_m.start()]
                _parts = re.split(r'(?:C\.F\.|P\.IVA)[:\s]*[A-Z0-9]+|\n', _before)
                _cand = _parts[-1].strip()
                _cand = re.sub(r'^\d+[.)]?\s+', '', _cand).strip()
                if _cand and re.match(r'[A-Za-z]', _cand) and len(_cand) > 2:
                    operatori.append((_cand, _piva))
                    _pive_inv.add(_piva)
    if operatori:
        visti = set()
        risultato = []
        for nome, piva in operatori:
            nome = nome.strip()
            # Se il nome contiene un CAP l'indirizzo è incorporato nella riga
            # ("NOME Via/Loc./C-da ... CAP Città (PR) CF/P.IVA NNN"):
            # taglia con i delimitatori stradali. Altrimenti resta grezzo come sempre.
            # Stesso trattamento se porta in coda una precisazione societaria tra virgolette
            # ("TIPIESSE S.P.A. \"Società Unipersonale soggetta ad attività di direzione e
            # coordinamento HBS Srl\"", es. CIG 93743885B3), che va rimossa dalla ragione sociale.
            if re.search(r'\d{5}', nome) or re.search(r'["“«][^"”»]{25,}["”»]\s*$', nome):
                nome = _pulisci_nome(nome, taglia_indirizzi=True)
            # Deduplica per coppia (nome, piva) e non per sola piva: due entry
            # diverse possono condividere la stessa P.IVA per refuso del documento
            chiave = (nome.upper(), piva.strip())
            if chiave not in visti:
                visti.add(chiave)
                # La riga d'origine serve a ricavare il C.F. quando il PDF lo
                # dichiara a parte dalla P.IVA (o e' una persona fisica).
                _riga = ""
                for _r in testo_sez.split('\n'):
                    if piva.strip() and piva.strip() in _r:
                        _riga = _r
                        break
                risultato.append(_op(nome, piva.strip(), _riga))
        return risultato

    # — Fallback solo-nomi —
    invitati_senza_piva = re.findall(
        r'^\s*\d{1,4}\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]+?)\s*[Mm]anifestazione\s+(?:di\s+)?interesse\s+del',
        testo_sez, re.MULTILINE
    )
    if not invitati_senza_piva:
        invitati_senza_piva = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]+?)\s+[Mm]anifestazione\s+(?:di\s+)?interesse\s+del',
            testo_sez, re.MULTILINE
        )
    if not invitati_senza_piva:
        # Formato "N. NOME P.IVA: IT-..."
        invitati_senza_piva = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]+?)\s*P\.IVA\b',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
        invitati_senza_piva = [m.rstrip('. ').strip() for m in invitati_senza_piva]
    if not invitati_senza_piva:
        # Classe estesa con +()/: raggruppamenti "A+B"
        # \s* (non \s+) gestisce numero attaccato al nome senza spazio
        invitati_senza_piva = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]+?)\s*$',
            testo_sez, re.MULTILINE
        )
    if not invitati_senza_piva:
        # "N.? NOME GG/MM/AAAA" — lista numerata con data
        invitati_senza_piva = re.findall(
            r'^\s*\d+[.)]?\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&+_"(),;:/àèìòùÀÈÌÒÙ]+?)\s+\d{2}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
        invitati_senza_piva = [m.strip() for m in invitati_senza_piva]
    if not invitati_senza_piva:
        # Nomi puri: nessun numero d'elenco, nessuna P.IVA, un nome per riga
        # Scarta intestazioni e righe con data.
        for riga in testo_sez.split('\n'):
            r = riga.strip()
            if (len(r) > 3
                    and not re.match(r'^\s*(?:Numero|Data|Termine|Nome\s+e|Operatori\s+economici\s+con)', r,
                                     re.IGNORECASE)
                    and not re.search(r'\d{2}/\d{2}/\d{4}', r)
                    and re.search(r'[A-Za-z]{3}', r)):
                invitati_senza_piva.append(r)
    if invitati_senza_piva:
        visti = set()
        risultato = []
        for nome in invitati_senza_piva:
            nome_pulito = nome.strip()
            if nome_pulito.upper() not in visti:
                visti.add(nome_pulito.upper())
                risultato.append({"nome": nome_pulito, "piva": "Non presente", "cf": "Non presente"})
        return risultato
    return []


def _estrai_offerte_std(testo_sez):
    """
    Estrae la lista dei nomi offerenti dalla sezione offerte (singolo lotto standard).
    Applica Fix Q al testo, poi una cascata di 8 pattern.
    Restituisce lista di stringhe già pulite con _pulisci_offerta.
    """
    # Fix Q: timestamp concatenato all'entry successiva (page-break senza newline)
    # "10:02:572. NOME" → "10:02:57\n2. NOME"
    # "12:46:310002 NOME" → "12:46:31\n0002 NOME"
    # "15:58:3002 NOME" → "15:58:30\n02 NOME" (entry 2-3 cifre)
    # "16:50:491 NOME"  → "16:50:49\n1 NOME"  (entry 1 cifra)
    testo_sez = re.sub(
        r'(\d{2}:\d{2}:\d{2})\s*(\d{4}\s+|\d{1,3}\s+(?=[A-Za-z])|\d+\.)', r'\1\n\2', testo_sez
    )
    # Fix refuso "P.1." -> "P.I.": in alcuni PDF l'etichetta P.IVA è resa
    # con la cifra 1 al posto della lettera I ("...e P.1. 01234567890"). Va normalizzata
    # PRIMA dei join e dei fallback, perché sia il join delle continuazioni (che nel
    # lookahead cerca "P.I.") sia i pattern P.IVA a valle non riconoscerebbero "P.1.".
    testo_sez = re.sub(r'\bP\.1\.', 'P.I.', testo_sez, flags=re.IGNORECASE)
    # Fix numerazione "N ." con spazio PRIMA del punto: alcune entry hanno il numero
    # d'elenco malformato "2 .DI IORIO SRL" / "5 .TIRRENA..." (spazio tra cifra e punto)
    # mentre le altre righe sono "1. ", "3. " regolari. Senza normalizzare, Pattern 1 e
    # i fallback (che si aspettano "N." o "N ") saltano queste righe e le offerte si
    # perdono. Riporta "N ." -> "N." solo a inizio riga, cifra seguita dal punto: non
    # tocca nomi con numeri interni.
    testo_sez = re.sub(r'^(\s*\d{1,4})\s+\.(?=\s|[A-Za-z])', r'\1.', testo_sez, flags=re.MULTILINE)
    # Fix wrap indirizzo dopo "N.": l'indirizzo di una voce va a capo lasciando appeso
    # il numero civico abbreviato "... Via Firenze N.\n30 amministrativa in FIRENZE...".
    # La riga di continuazione, iniziando con cifre, verrebbe scambiata dai fallback
    # numerici (spec. Fallback 9) per una nuova voce d'elenco, creando un'offerta
    # fantasma ("amministrativa in FIRENZE ..."). Ricongiunge la continuazione alla
    # riga precedente SOLO quando questa termina con "N." (numero civico troncato) e
    # la successiva inizia con una cifra: non tocca liste numerate regolari.
    testo_sez = re.sub(r'(\bN\.)\n(\d)', r'\1 \2', testo_sez)
    # Fix nome spezzato dal wrap con trattino di legatura a fine riga: il PDF manda a capo
    # dentro un nome composto e lascia il trattino appeso ("...COOPERATIVE SOCIALI-\nSOCIETÀ'
    # COOPERATIVA SOCIALE P.IVA: ..."). La seconda riga porta la P.IVA, quindi senza
    # ricongiungerle la voce non viene catturata da nessun pattern e l'offerta si perde.
    # Unisce SENZA spazio (il trattino fa parte del nome) e solo se la riga seguente non è
    # una nuova voce numerata. (es. bando servizi SdS Area Pratese CIG 8298461AA5)
    testo_sez = re.sub(r'(-)\n(?!\s*(?:\d{1,4}[.)]\s*\.?\s*[A-Za-z0-9]|\d{1,4}\s+[A-Za-z]))(?=\S)', r'\1', testo_sez)
    # Fix numero d'elenco isolato su riga a sé: alcuni PDF mettono il numero e il punto
    # su una riga tutta loro, col nome sulla riga successiva ("1.\nCristoforo ... con sede
    # ...\n2.\nAlice ..."). Senza unirli, i pattern numerati (che vogliono "N. NOME" sulla
    # stessa riga) non agganciano il nome e le offerte si perdono. Unisce "N.\n" alla riga
    # seguente solo se questa inizia con una lettera: non tocca liste già su riga singola.
    testo_sez = re.sub(r'^(\s*\d{1,3}\.)\s*\n(?=[A-Za-z])', r'\1 ', testo_sez, flags=re.MULTILINE)
    # Nello stesso formato, l'indirizzo/codici di una voce proseguono su una o più righe.
    # La continuazione può iniziare in vari modi:
    #   - "C.F. ..." / "e P.I. ..." / "P.I. ..."        (codici a capo)
    #   - "<piva>, e P.I. ..."
    #   - "46, C.F. ... e P.I. ..."                     (numero civico a capo, es. CIG 9003089014)
    #   - "86100 via conte rosso 32, C.F. ..."          (CAP+via+civico a capo, es. CIG 9067034129)
    # Ricongiunge alla riga precedente ogni riga di continuazione che contiene un codice
    # C.F./P.I. e o inizia con cifre (CAP/civico, eventualmente seguite da testo d'indirizzo)
    # o inizia direttamente col codice. Il negative lookahead esclude le nuove voci numerate
    # in tutte le forme usate dai bandi — "N. NOME", "N) NOME", "N NOME" senza punto
    # (es. "2 GLI ALTRI ... P.IVA: ..." di CIG 87408265CC) e "N.NOME" col numero attaccato
    # (es. "2.CSA ScpA ..." di CIG 95949535B2) — così non si fondono voci distinte. Lo spazio
    # dopo il numero è opzionale solo col separatore: senza, resta obbligatorio, altrimenti
    # un CAP di continuazione verrebbe scambiato per una nuova voce.
    # Ogni offerta resta su una riga unica e la cascata la tratta in modo uniforme, senza
    # che un fallback intermedio (spec. 6b) ne catturi solo alcune.
    # es. bandi CIG 9003089014, CIG 9067034129
    # Unisce le continuazioni che iniziano con una lettera quando la riga precedente è
    # troncata dal wrap:
    # i join a regex qui sotto coprono solo quelle che iniziano con cifre o col codice.
    testo_sez = _unisci_parola_spezzata_dopo_trattino(testo_sez)
    testo_sez = _unisci_membri_raggruppamento(testo_sez)
    testo_sez = _unisci_continuazioni_a_lettera(testo_sez)
    testo_sez = re.sub(
        r'\n(?!\s*(?:\d{1,4}[.)]\s*\.?\s*[A-Za-z0-9]|\d{1,4}\s+[A-Za-z]))'
        r'(\s*\d{1,11}\b[^\n]*?(?:C\.F\.|P\.I\.)[^\n]*'
        # Dopo l'etichetta devono seguire davvero delle cifre: senza questo vincolo una
        # ragione sociale che INIZIA con "C.F." ("C.F.C. Consorzio fra costruttori
        # soc.coop. ...", es. bando barriere SP CIG 75695638FD) viene scambiata per una
        # continuazione e fusa nella voce precedente, sparendo dall'elenco.
        r'|\s*(?:C\.F\.|e\s+P\.I\.|P\.I\.)[\s:.,-]*(?:IT-\s*)?[A-Z0-9]{8,}[^\n]*)',
        r' \1', testo_sez, flags=re.IGNORECASE
    )
    # Normalizza il terminatore "offerta del": maiuscolo "OFFERTA DEL",
    # refuso del PDF "oferta del" con una sola f,
    # "offrta del" senza la e (es. bando ponti CIG 90445094F0), e "OFFERTA DEL" INCOLLATO al
    # nome senza spazio ("COSTITUENDOOFFERTA DEL") — per questo niente \b iniziale.
    # Consente ai pattern case-sensitive "[Oo]fferta\s+del" di riconoscerlo sempre.
    testo_sez = re.sub(r'off?r?erta\s+del\b', 'offerta del', testo_sez, flags=re.IGNORECASE)
    testo_sez = re.sub(r'\boffrta\s+del\b', 'offerta del', testo_sez, flags=re.IGNORECASE)
    # "offerta del <parola> <data>": una parola si è infilata tra il terminatore e la
    # data ("offerta del costituendo 08/02/2022", es. bando ponti CIG 90445094F0). Riporta
    # la parola prima del terminatore così il nome la include e la data resta agganciata.
    testo_sez = re.sub(
        r'offerta\s+del\s+(costituendo)\s+(\d{2}/\d{2}/\d{4})',
        r'\1 offerta del \2', testo_sez, flags=re.IGNORECASE
    )
    # Terminatore "offerta del" OMESSO su una singola entry numerata, con la sola data
    # ("16. IMPRENDO_MURARO RTI costituendo 08/02/2022 18:39:49", es. bando ponti CIG 90445094F0):
    # lo inserisce prima della data, ma SOLO se altrove nella sezione "offerta del" esiste
    if re.search(r'offerta\s+del', testo_sez, re.IGNORECASE):
        testo_sez = re.sub(
            r'^(\s*\d+\.\s+(?:(?!offerta\s+del)[^\n])*?)\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}:)',
            r'\1 offerta del \2', testo_sez, flags=re.MULTILINE | re.IGNORECASE
        )
    # Join nome su due righe in offerte: "NOME_PARTE1\nPARTE2 offerta del" → riga singola
    # Evita che _pulisci_nome tronchi il nome al \n e che la seconda parte vada persa
    testo_sez = re.sub(
        r"([A-Za-z'])\n([A-Za-z][^\n]+?\s+offerta\s+del)",
        r'\1 \2', testo_sez, flags=re.IGNORECASE
    )
    # Strip righe "e P.I. VALUE" e "P.I. VALUE" a inizio riga
    # Queste righe sono continuazioni di codice fiscale, non nomi di offerenti;
    # se lasciate, Fallback 7 le cattura come nomi ("e P.I." / "P.I." dopo _pulisci_nome)
    testo_sez = re.sub(
        r'^[,\s]*e\s+P\.I\.?\s+.*$', '', testo_sez, flags=re.MULTILINE | re.IGNORECASE
    )
    testo_sez = re.sub(
        r'^P\.I\.?\s+\d+.*$', '', testo_sez, flags=re.MULTILINE | re.IGNORECASE
    )
    # Strip righe che iniziano con "C.F." seguito dal codice (wrap dell'indirizzo su
    # riga nuova: "C.F. 01538140623, e P.I. 01538140623", es. bando CIG 9067034129).
    # Senza strip, Fallback 7 le cattura come nomi scavalcando fino al "con sede"
    # dell'entry successiva, che sparisce. Il codice deve essere VERO: 11 cifre
    # (societario) oppure CF di persona fisica (6 lettere + 10 caratteri con almeno
    # una cifra DENTRO il blocco). Così i nomi d'azienda tipo "C.F.C. CONSORZIO..."
    # o "C.F. COSTRUZIONI SRL" (sole lettere) restano intatti.
    testo_sez = re.sub(
        r'^C\.F\.?\s*:?\s*(?:\d{11}|[A-Z]{6}(?=[A-Z0-9]{0,9}\d)[A-Z0-9]{10})\b.*$', '', testo_sez,
        flags=re.MULTILINE | re.IGNORECASE
    )
    # Pattern 1: "NNNN NOME offerta del" — (?:\.|\s) accetta numerazione mista con/senza
    # punto nella stessa lista ("1 BANCHELLI" + "2. ITALSCAVI")
    # senza agganciare righe che iniziano con una data; (?=[A-Z]) accetta il numero
    # incollato al nome senza spazio ("0042M.V.RESILIENTI");
    # (?:data)? scarta la data quando sta PRIMA di "offerta del"
    offerte = re.findall(
        r'^\s*\d{1,4}(?:\.|\s|(?=[A-Z]))\s*([A-Za-z0-9]' + _CLS_OFF + r'+?)\s*(?:\d{2}/\d{2}/\d{4}\s*)?[Oo]fferta\s+del',
        testo_sez, re.MULTILINE
    )
    if not offerte:
        # Fallback 2: "0001 NOME GG/MM/AAAA"
        offerte = re.findall(
            r'^\s*\d{2,4}\s+([A-Za-z0-9]' + _CLS_OFF + r'+?)\s+\d{1,2}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 3: "1. NOME offerta del" (\s* cattura anche "1.NOME" senza spazio)
        # (?:data)? scarta la data quando sta PRIMA di "offerta del" e l'ora dopo:
        # "1. NOME 25/07/2022 offerta del 16:54:56"
        offerte = re.findall(
            r'^\s*\d+\.\s*([A-Za-z0-9]' + _CLS_OFF + r'+?)\s*(?:\d{2}/\d{2}/\d{4}\s*)?[Oo]fferta\s+del',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 4: "1. NOME GG/MM/AAAA offerta del" — data PRIMA di "offerta del"
        offerte = re.findall(
            r'^\s*\d+\.\s*([A-Za-z0-9]' + _CLS_OFF + r'+?)\s+\d{2}/\d{2}/\d{4}\s*[Oo]fferta\s+del',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 5: numero attaccato senza spazio
        offerte = re.findall(
            r'^\s*\d{4}([A-Za-z]' + _CLS_OFF + r'+?)\s*[Oo]fferta\s+del',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 6: "N. NOME-GG/MM/AAAA" separatore trattino
        raw = re.findall(
            r'^\s*\d+\.\s*([\s\S]+?)-\s*\d{2}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
        if raw:
            offerte = [n.replace('\n', ' ').strip() for n in raw if n.strip()]
    if not offerte:
        # Fallback 6b: "N. NOME P.IVA[:] <cifre> ..." — lista numerata dove il nome
        # termina all'etichetta P.IVA (formato della sezione "ammesse e valutate").
        # Prima del Fallback 7 perché quest'ultimo, fermandosi
        # a "con sede", cattura solo le righe che ce l'hanno e sporca il nome col "P.IVA".
        # \s*[,;]?\s* ammette la virgola prima dell'etichetta ("SRL ,P.IVA:") e l'alternativa
        # "Partita IVA" copre l'etichetta per esteso senza punti.
        # [.:\s-]* ammette anche il trattino nudo tra etichetta e cifre ("P.IVA -02066400405",
        # es. offerta RTI del bando sanzioni Pescia, CIG 8791802885).
        # Il separatore dopo il numero è opzionale: alcune liste hanno voci senza punto
        # ("2 GUTTORIELLO COSTRUZIONI SRL, con sede legale in Teano...", es. bando plesso
        # Montecatini CIG 90371750BC, dove è l'unica delle tre a non averlo). Senza, quella voce
        # non veniva catturata e — trovando comunque le altre — il 6b bloccava i fallback
        # successivi, lasciando la lista incompleta. Se il separatore manca, lo spazio è
        # obbligatorio, così il numero non si fonde col nome.
        offerte = re.findall(
            r'^\s*\d+(?:[.)]\s*|\s+)([A-Za-z0-9][^\n]+?)\s*[,;]?\s*(?:P[.\-]\s?(?:IVA|I\.?)|Partita\s+IVA)[.:\s-]*(?:IT-\s*)?\d{8,11}',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
    if not offerte:
        # Fallback 7: "N. NOME[, con sede ...] GG/MM/AAAA" — numero iniziale opzionale
        # La classe include le virgolette: alcune ragioni sociali portano una precisazione
        # tra virgolette prima della data ("TIPIESSE S.P.A. \"Società Unipersonale soggetta
        # ad attività di direzione e coordinamento HBS Srl\" 08/09/2022", es. bando campo
        # sportivo CIG 93743885B3); senza, il nome non arriva alla data e la voce si perde.
        # La coda viene poi rimossa da _pulisci_nome.
        offerte = re.findall(
            r'^\s*(?:\d{1,3}\.?\s*)?([A-Za-z][A-Za-z0-9\s\'\"“”«»\.\-–&+_,;()àèìòùÀÈÌÒÙ]+?)'
            r'(?=\s*[,;]?\s*\d*\s*con\s*sede|\s+\d{2}/\d{2}/\d{4})',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
        offerte = [o.rstrip(', ').strip() for o in offerte]
    if not offerte:
        # Fallback 8: "N.? NOME GG/MM/AAAA"
        offerte = re.findall(
            r'^\s*\d+\.?\s*([A-Za-z0-9]' + _CLS_OFF + r'+?)\s+\d{2}/\d{2}/\d{4}',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 9: "NNNN NOME" / "N. NOME" a fine riga, senza data né "offerta del"
        offerte = re.findall(
            r'^\s*\d{1,4}\.?\s+([A-Za-z0-9]' + _CLS_OFF + r'+?)\s*$',
            testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 9b: offerta su due righe, "NOME\nNazione Italia - Provincia ... - Città ...
        # - Indirizzo ...". Nessun numero d'elenco, nessuna etichetta P.IVA, nessun "offerta
        # del": l'unica ancora è la riga d'indirizzo che inizia con "Nazione". Il nome è la
        # riga immediatamente precedente. (es. bando riscossione coattiva Massa e Cozzile
        # CIG 8757984507).
        offerte = re.findall(
            r'^(.+?)\n\s*Nazione\b', testo_sez, re.MULTILINE
        )
    if not offerte:
        # Fallback 9c: offerta di un raggruppamento scritta come un unico blocco
        # "RTP: <mandataria> (mandataria) - <membro> (mandante) - ..." eventualmente
        # spezzato su più righe, senza numero d'elenco né P.IVA né "offerta del".
        # È UNA sola offerta (il PDF dichiara n. 1): si estrae il capogruppo, coerentemente
        # con la convenzione RTI degli altri bandi (es. CIG 8791802885 "RTI MAGGIOLI SPA") e con
        # l'aggiudicatario, che per lo stesso testo dà "RTP: Rina Consulting Spa".
        # (es. bando ponti Prato CIG 8571671EAA)
        offerte = re.findall(
            r'^\s*((?:RT[PIS]|ATI)\s*:?\s*[^\n(]+?)\s*\((?:mandataria|capogruppo)\)',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
    if not offerte:
        # Fallback 9d: variante senza "(mandataria)" — "RTI costituendo: <capogruppo> CF NNN
        # - <membro> CF NNN - ..." su più righe (es. bando servizi SdS Pistoiese, CIG 8183742D87).
        # Il nome del capogruppo termina alla prima etichetta di codice. Anche qui è UNA
        # sola offerta e si estrae il capogruppo, come fa l'aggiudicatario sullo stesso testo.
        offerte = re.findall(
            r'^\s*(RT[PIS]\s+costituendo\s*:\s*[^\n]+?)\s+(?:CF|C\.F\.|P\.\s?IVA)\b',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
    if not offerte:
        # Fallback 9e: voce di raggruppamento SENZA alcun codice, data o "con sede"
        # ("RTI COSTITUENDO SA.CA. S.R.L. -LUNARDI AMBIENTE E TERRITTORIO DI LUNARDI
        # RICCARDO", es. bando accordo quadro strade CIG 8490352C1B: il PDF elenca i codici solo
        # più sotto, nelle righe Mandataria/Mandante dell'aggiudicatario). Tutti i pattern
        # precedenti mancano la voce e la lista esce vuota; il 9d non basta perché pretende
        # i due punti dopo "costituendo" e un'etichetta di codice a chiudere il nome.
        # Il nome termina al trattino che introduce il membro: si estrae sigla+capogruppo,
        # come per gli altri raggruppamenti. La guardia iniziale limita il fallback alle
        # righe SENZA codici — che è il caso per cui nasce: dove i codici ci sono, la voce
        # è già gestita dai pattern precedenti e qui il trattino separerebbe l'indirizzo,
        # non il membro (es. "ATI E.CO.RES. S.R.L. VIA BENEDETTO CROCE, 43 - 80021
        # AFRAGOLA...", CIG 85853631AD, dove senza guardia il nome si porterebbe dietro la via).
        offerte = re.findall(
            r'^\s*(?![^\n]*(?:C\.F\.|P\.\s?IVA|CF\b))'
            r'((?:RT[PIS]|ATI|ATP)(?:\s+costituendo)?\s*:?\s*[^\n-]+?)\s*[-–]\s*\S',
            testo_sez, re.MULTILINE | re.IGNORECASE
        )
    if not offerte:
        # Fallback 10: "NOME P.IVA IT-NNN, C.F. NNN" — lista senza numerazione, senza data
        # e senza "offerta del". Lavora riga per riga: ogni riga
        # con etichetta P.IVA+cifre è una voce; il nome è la riga ripulita da etichette e
        # codici. Gestisce anche "P.IVA/C.F." con slash e i raggruppamenti RTI con più
        # aziende sulla stessa riga: "RTI: A P.IVA/C.F.NNN - B P.IVA/CF NNN".
        # Il prefisso C.F. accetta 11-16 caratteri: sia il CF societario (11 cifre)
        # sia quello di persona fisica (16). L'etichetta P.IVA include anche
        # "Partita IVA" per esteso. Tra etichetta e cifre è ammesso
        # anche un trattino nudo ("P.IVA -03140011200", separatore residuo quando manca "IT-",
        # es. bando CIG 9060289302 fornitura trattori).
        _lab_off = (
            r'[,;]?\s*(?:C\.?F\.?\s*(?:[Ee]\s+|/\s*|:?\s*[A-Za-z0-9]{11,16}\s+))?'
            r'(?:Partita\s+IVA|P[.\-]\s?(?:IVA|I\.?))(?:\s*/\s*C\.?F\.?\.?)?'
            r'[.:\s-]*(?:IT-\s*)?\d{8,11}(?:\s*[,;]?\s*C\.?F\.?[.:\s-]*[A-Z0-9]+)?'
        )
        for riga in testo_sez.split('\n'):
            if re.search(_lab_off, riga, re.IGNORECASE):
                nome = re.sub(_lab_off, '', riga, flags=re.IGNORECASE)
                nome = re.sub(r'^\s*\d{1,4}[.)]?\s+', '', nome).strip(' ,;')
                # CAP nel nome = indirizzo incorporato: taglia con i delimitatori
                # stradali. I nomi senza CAP restano intatti.
                if re.search(r'\d{5}', nome):
                    nome = _pulisci_nome(nome, taglia_indirizzi=True)
                if nome:
                    offerte.append(nome)
            elif re.search(r'\(\s*RTI\b', riga, re.IGNORECASE) and re.search(r'\bCF\s*\d{8,11}', riga, re.IGNORECASE):
                # Riga RTI senza etichetta "P.IVA" ma con "CF NNN" dentro le parentesi
                # ("Cap&G consulting srl (RTI ... (CF 01756750624 - Isfel srl CF ...)"
                # : il nome è la parte prima della parentesi dell'RTI.
                nome = re.sub(r'\s*\(\s*RTI\b.*$', '', riga, flags=re.IGNORECASE).strip(' ,;')
                if nome:
                    offerte.append(nome)
    _RUMORE_OFFERTE = {'e p.i.', 'p.i.', 'p.iva', 'c.f.', 'e p.i'}
    pulite = [_pulisci_offerta(n) for n in offerte] if offerte else []
    return [o for o in pulite if o.lower() not in _RUMORE_OFFERTE and len(o) > 4]


def _estrai_aggiudicatario_std(testo_aggiud_flat):
    """
    Estrae coppie (nome_grezzo, piva) dell'aggiudicatario dalla sezione appiattita.
    Applica una cascata di 8 pattern.
    Restituisce lista di tuple (nome, piva) — il nome NON è ancora passato a _pulisci_nome.
    """
    # L'etichetta P.IVA è riconosciuta anche come "Partita IVA" per esteso (senza punti):
    # alcuni PDF scrivono "H.C. s.r.l. Partita IVA 02426680845 C.F. ..." invece di "P.IVA:".
    # Il nome termina in lookahead su "Partita IVA" (così non se la ingloba) e l'etichetta
    # finale la include, agganciando le 11 cifre della P.IVA.
    #
    # Pattern 1a — P.IVA-first: quando la riga contiene SIA il C.F. SIA la P.IVA
    # ("..., C.F. 00799960158, e P.I. 11991500015"),
    # va presa la P.IVA. Il pattern 1b sotto ha "C.F." come prima alternativa e, essendo il
    # C.F. scritto per primo nel testo, catturerebbe quello scambiandolo per partita IVA.
    # Questo tentativo cerca solo le etichette P.IVA/P.I./Partita IVA; se la riga ha
    # soltanto il C.F. non matcha e si ripiega su 1b, che resta invariato.
    _head_aggiud = (
        r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
        # Alcuni bandi introducono l'aggiudicatario come voce di elenco numerata
        # ("1) DI DUCA COSTRUZIONI SRLP.IVA: ...", es. CIG 9023452427): il numero va consumato,
        # altrimenti finisce nel nome ("1) DI DUCA COSTRUZIONI SRL").
        r"(?:\d{1,3}\s*[.)]\s*)?"
        r"(.+?)(?:,\s*con\s+sede|\s+con\s+sede|,\s*CAP|(?=\s+Partita\s+IVA\b)"
        # Nei raggruppamenti il PDF può elencare i membri con le etichette "Mandataria:" /
        # "Mandante:" dopo il nome dell'RTI ("RTI Costituendo SA.CA. S.R.L. -LUNARDI ...
        # Mandataria: SA.CA. SRL Unipersonale - C.F.: ...", es. CIG 8490352C1B): senza chiudere
        # lì, il nome corre fino al primo codice e ingloba la riga della mandataria.
        r"|(?=\s+Mandatari[ao]\b)|(?=\s+Mandante\b)"
        # Il nome può essere incollato all'etichetta senza spazio né delimitatori
        # ("DI DUCA COSTRUZIONI SRLP.IVA: IT-01995380605", es. CIG 9023452427): senza chiudere
        # anche sull'etichetta, il nome non si chiude mai e i pattern col codice non
        # agganciano, lasciando la piva vuota. Il prefisso "C.F./" va incluso nel
        # lookahead, altrimenti resta appiccicato in coda al nome quando l'etichetta è
        # composta ("Exprit s.r.l. CF/P.IVA 02174300489", es. CIG 9015549A62).
        r"|(?=\s*(?:C\.?F\.?\s*/\s*)?P\.?\s?I(?:VA|\.)[\s:.-]*(?:IT-)?\d)"
        r"|\s+[Vv]ia(?:le)?\s|\s+[Pp]iazza\s|\s+[Cc]orso\s"
        # Il delimitatore " - " serve a tagliare le code tipo "ALFA SRL - Media impresa",
        # ma nei raggruppamenti il trattino introduce la prima impresa ("ATI - OLIMPIA
        # COSTRUZIONI SRL via B. Dovizi...", es. bando pista atletica CIG 95278831C9;
        # "RTI: - Palandri e Belli S.r.l. via Michelangelo...", es. bando SP9 CIG 9308156553, dove
        # la sigla ha i due punti ed è pure su una riga a sé prima dell'appiattimento):
        # lì tagliare lascerebbe come nome la sola sigla ("ATI", "RTI:"). I lookbehind
        # escludono il taglio quando il nome finora è solo una sigla di raggruppamento —
        # con o senza i due punti — così si ottiene "ATI - OLIMPIA COSTRUZIONI SRL" e
        # "RTI: - Palandri e Belli S.r.l.", coerenti con la convenzione sigla+capogruppo
        # già usata per gli altri raggruppamenti (CIG 8571671EAA "RTP: Rina Consulting Spa",
        # CIG 8183742D87 "RTI costituendo: Co&So ...").
        r"|(?<!\bATI)(?<!\bRTI)(?<!\bRTP)(?<!\bRTS)(?<!\bATP)"
        r"(?<!\bATI:)(?<!\bRTI:)(?<!\bRTP:)(?<!\bRTS:)(?<!\bATP:)"
        r"\s+[-–]\s+|\s*\()"
    )
    matches = re.findall(
        _head_aggiud +
        # Il prefisso "IT-" davanti alle cifre compare in alcuni bandi ("P.IVA: IT-01995380605",
        # es. bando parcheggio Pieve a Nievole CIG 9023452427): senza ammetterlo nessun pattern
        # della cascata aggancia e scatta il fallback "solo nome", che restituisce il nome
        # sporco dell'intera riga e piva vuota.
        r"[\s\S]{0,150}?(?:Partita\s+IVA|P\.\s?I(?:VA|va)?\.?)[\s:]*[-–]?\s*(?:e\s+C\.?F\.?\s+)?(?:IT-\s*)?(\d{11})",
        testo_aggiud_flat, re.IGNORECASE
    )
    matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # Pattern 1b — come sopra ma accetta anche il solo C.F. come codice
        matches = re.findall(
            _head_aggiud +
            r"[\s\S]{0,150}?(?:C\.F\.|Partita\s+IVA|P\.I(?:VA|va)?\.?)[\s:]*[-–]?\s*(?:e\s+C\.?F\.?\s+)?(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
    matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # "NOME P.IVA[-: ]DIGITS"
        matches = re.findall(
            r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
            r"(.+?)\s+P\.?\s*I(?:va|VA)\.?[\s:–-]*(?:e\s+C\.?F\.?\s+)?(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # "NOME CF/P.iva DIGITS indirizzo" — etichetta con slash attaccata subito dopo
        # il nome, indirizzo DOPO la P.IVA
        matches = re.findall(
            r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
            r"(.+?)\s*C\.?F\.?\s*/\s*P[.\-]?\s?I(?:VA|va)?\.?[\s:]*(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # "NOME C.F./Codice fiscale NNNNN"
        matches = re.findall(
            r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
            r"([A-Za-z][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)\s+"
            r"(?:[Cc]odice\s+[Ff]iscale|C\.F\.)[\s:]*(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        matches = re.findall(
            r"[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
            r"(.+?)[\s\S]{0,80}?(?:C\.F\.|P\.I(?:VA|va)?\.?)[\s:]*(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # "NOME con sede ... C.F. e P.I. DIGITS"
        matches = re.findall(
            r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*"
            r"(.+?)(?:,?\s*con\s*sede)"
            r"[\s\S]{0,200}?C\.F\.(?:\s+e\s+P\.I\.?)?\s*(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        matches = re.findall(
            r"aggiudicatario[\s\S]{0,30}?:\s*(.+?)[\s\S]{0,150}?(?:C\.F\.|P\.I(?:VA)?\.?)[\s\S]{0,20}?(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        matches = re.findall(
            r"[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*(.+?)\s+P\.?\s*I(?:VA|va)?\.?\s+(\d{11})",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        matches = re.findall(
            r"[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*(.+?)\s*\((\d{11})\)",
            testo_aggiud_flat, re.IGNORECASE
        )
        matches = [(n, p) for n, p in matches if len(n.strip()) > 2]
    if not matches:
        # Aggiudicatario SENZA alcuna P.IVA/C.F. nel blocco: cattura il solo nome,
        # delimitato dall'inizio dell'indirizzo; la piva resta vuota (-> "Non presente").
        # Il trattino iniziale è opzionale: alcuni PDF introducono l'aggiudicatario come
        # voce di elenco ("- TUTINO GROUP S.R.L.", es. bando SP20 CIG 9049707676).
        # Se manca anche l'indirizzo — il PDF dà solo il nome — il taglio avviene sulla
        # prima etichetta successiva del documento, altrimenti il nome si porterebbe
        # dietro tutto il resto della sezione.
        m = re.search(
            r"[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[:\s]*\s*(?:\d{1,3}\s*[.)]\s*)?[-–]?\s*"
            r"([A-Za-z0-9].{2,120}?)\s*(?:,\s*con\s+sede|\s+con\s+sede|\s+[Ii]ndirizzo\b"
            r"|\s+[Vv]ia(?:le)?\s|\s+[Pp]iazza\s|\s+[Cc]orso\s|\s+[Ss]trada\s|\s+[Ll]oc\.\s"
            r"|\s+zona\s+industriale"
            r"|\s+Ribasso\b|\s+Valore\b|\s+Data\b|\s+Organo\b|\s+Subappalto\b|\s+Punteggio\b)",
            testo_aggiud_flat, re.IGNORECASE
        )
        if m and len(m.group(1).strip()) > 2:
            matches = [(m.group(1).strip(), "")]
    return matches


def _estrai_singolo_lotto_std(testo, testo_sez_offerte, testo_aggiud_flat, testo_sez_ammesse=""):
    """
    Estrae dati per il singolo lotto nel formato standard.
    Restituisce il dict lotto completo.
    """
    lotto = {
        "nome_lotto": None,
        "num_offerte_ricevute": "Non presente",
        "offerte_ricevute": [],
        "num_offerte_ammesse": "Non presente",
        "offerte_ammesse": [],
        "num_offerte_escluse": "Non presente",
        "aggiudicatario_pdf": "Non presente",
        "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
        "ribasso": "Non presente",
        "valore_offerta": "Non presente"
    }

    # Num offerte "ricevute"/"pervenute".
    match = re.search(r'Numero\s+(?:di\s+)?offerte\s+(?:ricevute|presentate|pervenute)[^\n]{0,50}?(\d+)', testo, re.IGNORECASE)
    if match:
        valore = match.group(1)
        if len(valore) <= 3:
            lotto["num_offerte_ricevute"] = valore

    # Lista offerte
    lotto["offerte_ricevute"] = _estrai_offerte_std(testo_sez_offerte)

    # Fallback ELENCO DI NOMI NUDI anche per le offerte.
    if (lotto["num_offerte_ricevute"] != "Non presente"
            and str(lotto["num_offerte_ricevute"]).isdigit()
            and int(lotto["num_offerte_ricevute"]) >= 5
            and len(lotto["offerte_ricevute"]) <= 2):
        _nudi_off = _elenco_nomi_nudi(testo_sez_offerte)
        if len(_nudi_off) > len(lotto["offerte_ricevute"]):
            lotto["offerte_ricevute"] = _nudi_off
    # Elenco col TRATTINO e senza anagrafica:
    # le cascate standard si ancorano alla numerazione o alla P.IVA e qui non
    # pescano nulla. La guardia sopra non copre il caso perche' richiede almeno
    # 5 offerte dichiarate; qui il riferimento e' il conteggio del PDF stesso e
    # si accetta solo se il numero di voci trovate lo rispetta ESATTAMENTE,
    # cosi' le righe di continuazione (le consorziate esecutrici, che vanno a
    # capo) non vengono scambiate per offerenti.
    if (not lotto["offerte_ricevute"]
            and str(lotto["num_offerte_ricevute"]).isdigit()
            and testo_sez_offerte):
        _tratti = [
            _pulisci_nome(_m.group(1))
            for _m in re.finditer(r'^\s*[-–]\s*([^\n]+?)\s*;?\s*$',
                                  testo_sez_offerte, re.MULTILINE)
        ]
        _tratti = [_n for _n in _tratti if _n]
        if len(_tratti) == int(lotto["num_offerte_ricevute"]):
            lotto["offerte_ricevute"] = _tratti

    # Se il PDF non riporta il conteggio (header "Numero offerte ricevute" senza
    # numero, seguito subito dalla lista),
    # lo ricava dalla lunghezza della lista estratta
    if lotto["num_offerte_ricevute"] == "Non presente" and lotto["offerte_ricevute"]:
        lotto["num_offerte_ricevute"] = str(len(lotto["offerte_ricevute"]))

    # Lista offerte AMMESSE (sezione "ammesse e valutate"): lista propria, stessa
    # cascata di pattern delle ricevute. Molti PDF elencano i nomi solo qui.
    if testo_sez_ammesse:
        lotto["offerte_ammesse"] = _estrai_offerte_std(testo_sez_ammesse)

    # Offerte ammesse/escluse — conteggio sulla stessa riga dell'intestazione:
    # il civico dell'aggiudicatario non diventa il conteggio (es. bando arredi CIG 9060289302)
    match = re.search(r'Numero offerte ammesse[^\n]{0,50}?(\d+)', testo, re.IGNORECASE)
    if match:
        lotto["num_offerte_ammesse"] = match.group(1)
    # Se manca il conteggio ma la lista ammesse è piena, lo ricava dalla lista
    if lotto["num_offerte_ammesse"] == "Non presente" and lotto["offerte_ammesse"]:
        lotto["num_offerte_ammesse"] = str(len(lotto["offerte_ammesse"]))
    # "Numero di concorrenti esclusi": variante di etichetta. Li'
    # il conteggio e' ripartito su due fasi ("documentazione amministrativa:0.
    # In fase di esame offerta tecnica: 1"): si prende l'ULTIMO numero della
    # riga, cioe' il totale degli esclusi a valle di entrambe le fasi.
    match = re.search(r'Numero offerte escluse[^\n]{0,50}?(\d+)', testo, re.IGNORECASE)
    if match:
        lotto["num_offerte_escluse"] = match.group(1)
    else:
        _m_esc = re.search(r'Numero\s+(?:di\s+)?concorrenti\s+esclusi([^\n]*(?:\n[^\n]*)?)',
                           testo, re.IGNORECASE)
        if _m_esc:
            _numeri = re.findall(r'(\d+)', _m_esc.group(1))
            if _numeri:
                lotto["num_offerte_escluse"] = _numeri[-1]

    # Aggiudicatario
    matches = _estrai_aggiudicatario_std(testo_aggiud_flat)
    if matches:
        nomi = []
        pive = []
        visti_piva = set()
        for nome_completo, piva in matches:
            piva = piva.strip()
            if piva not in visti_piva:
                visti_piva.add(piva)
                nomi.append(_pulisci_nome(nome_completo, taglia_indirizzi=True))
                pive.append(piva)
        lotto["aggiudicatario_pdf"] = ", ".join(nomi)
        # Il fallback senza codici restituisce piva vuota: in quel caso il campo
        # resta "Non presente" invece di diventare stringa vuota
        pive_valide = [p for p in pive if p]
        if pive_valide:
            lotto["aggiudicatario_piva"] = ", ".join(pive_valide)

    # Ribasso
    match = re.search(r'(?:Ribasso[\s\S]{0,30}?)([\d,\.]+)\s*%', testo, re.IGNORECASE)
    if match:
        lotto["ribasso"] = f"{match.group(1)}%"

    # Valore offerta
    match = re.search(
        # "Importo contrattuale (valore dell'appalto) € ...": variante di
        # etichetta di esito-210, dove manca del tutto "Valore dell'offerta".
        r"(?:Valore dell['']offerta[\s\S]{0,60}?|Importo di aggiudicazione[\s\S]{0,40}?"
        r"|Importo contrattuale[\s\S]{0,40}?)"
        r"(?:€|Euro)\s*([\d\.,]+)",
        testo, re.IGNORECASE
    )
    if match:
        lotto["valore_offerta"] = f"€ {match.group(1).rstrip(',').strip()}"

    return lotto


def _estrai_lotto_ml_std(testo, nome_lotto, altri_lotti, testo_aggiud_flat):
    """
    Estrae dati per un singolo lotto nel formato standard multi-lotto.
    Restituisce il dict lotto completo.
    """
    lotto = {
        "nome_lotto": f"LOTTO {nome_lotto}",
        "cig_lotto": "Non presente",
        "deserto": False,
        "num_offerte_ricevute": "Non presente",
        "offerte_ricevute": [],
        "num_offerte_ammesse": "Non presente",
        "offerte_ammesse": [],
        "num_offerte_escluse": "Non presente",
        "aggiudicatario_pdf": "Non presente",
        "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
        "ribasso": "Non presente",
        "valore_offerta": "Non presente"
    }

    # — CIG del lotto dal blocco di testata ("LOTTO A “Asfalti...” /
    # CPV: ... / CIG: A03589F8C6", es. Ciclovia del Sole, gara a 3 lotti): pattern
    # "temperato" che NON scavalca nel blocco del lotto successivo.
    m_cig = re.search(
        rf'LOTTO\s+{nome_lotto}\b(?:(?!\bLOTTO\s)[\s\S]){{0,250}}?CIG[.:\s]*([A-Z0-9]{{10}})\b',
        testo, re.IGNORECASE
    )
    if m_cig:
        lotto["cig_lotto"] = m_cig.group(1)
    else:
        # Testata INVERTITA (es. gara SP16/SP28 a 2 lotti): "CIG: LOTTO A A01EDD2441 /
        # LOTTO B A01EDDFEF8" — l'etichetta CIG precede i lotti e il codice
        # e' ADIACENTE al nome. Un token di 10 alfanumerici maiuscoli subito
        # dopo "LOTTO X" e' inequivocabilmente il suo CIG.
        # {10,11}: il CIG in testata puo' avere una lettera IN PIU' per refuso
        # ("LOTTO B A01EF539010", 11 = A01[E]F539010, lotto B della gara
        # CIG A01E792BE2). Si
        # cattura fedelmente; l'aggancio col CIG vero di pagina (A01F539010)
        # avviene per SOTTOSEQUENZA in cig_compatibile.
        m_cig = re.search(rf'(?:LOTTO|Lotto)\s+{nome_lotto}\s+([A-Z0-9]{{10,11}})\b', testo)
        if m_cig:
            lotto["cig_lotto"] = m_cig.group(1)
        else:
            # Terza variante di testata (es. Vernio, gara a 4 lotti): "CIG lotto 1:
            # A031086993" — etichetta CIG prima, lotto minuscolo, due punti.
            m_cig = re.search(rf'CIG\s+lotto\s+{nome_lotto}\s*[.:\s]\s*([A-Z0-9]{{10}})\b',
                              testo, re.IGNORECASE)
            if m_cig:
                lotto["cig_lotto"] = m_cig.group(1)
            else:
                # "CIG Lotto 1 Lamporecchio 941376222B": tra il numero
                # di lotto e il codice c'e' il nome del Comune. Si prende il primo
                # token di 10 alfanumerici dopo "CIG Lotto N ... " fino al prossimo
                # "CIG" o fine testata.
                m_cig = re.search(
                    rf'CIG\s+[Ll]otto\s+{nome_lotto}\b(?:(?!\bCIG\b)[\s\S]){{0,60}}?([A-Z0-9]{{10}})\b',
                    testo
                )
                if m_cig:
                    lotto["cig_lotto"] = m_cig.group(1)

    # Isola il testo del lotto
    pattern_inizio = rf'[Ll]otto\s+{nome_lotto}\b'
    matches_inizio = list(re.finditer(pattern_inizio, testo, re.IGNORECASE))
    if matches_inizio:
        pos_inizio = matches_inizio[-1].start()
        if altri_lotti:
            pattern_fine = r'[Ll]otto\s+(?:' + '|'.join(altri_lotti) + r')\b'
            match_fine = re.search(pattern_fine, testo[pos_inizio + 10:], re.IGNORECASE)
            testo_lotto = (
                testo[pos_inizio: pos_inizio + 10 + match_fine.start()]
                if match_fine else testo[pos_inizio:]
            )
        else:
            testo_lotto = testo[pos_inizio:]
    else:
        testo_lotto = testo

    # Num offerte ricevute
    match = re.search(
        rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,30}}?n[°o]\s*(\d+)', testo, re.IGNORECASE
    )
    if not match:
        offerte_count = re.findall(
            r'\d{4}\s+[A-Za-z0-9][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?\s+[Oo]fferta del',
            testo_lotto
        )
        if offerte_count:
            lotto["num_offerte_ricevute"] = str(len(offerte_count))
    else:
        lotto["num_offerte_ricevute"] = match.group(1)

    # Fix Q + lista offerte
    testo_lotto = re.sub(
        r'(\d{2}:\d{2}:\d{2})\s*(\d{4}\s+|\d+\.)', r'\1\n\2', testo_lotto
    )
    offerte = re.findall(
        r'^\d{4}\s+([A-Za-z0-9]' + _CLS_OFF + r'+?)\s+[Oo]fferta del',
        testo_lotto, re.MULTILINE
    )
    if not offerte:
        offerte = re.findall(
            r'^([A-Za-z0-9]' + _CLS_OFF + r'+?)\s+[Oo]fferta\s+del',
            testo_lotto, re.MULTILINE
        )
    if offerte:
        lotto["offerte_ricevute"] = [_pulisci_offerta_ml(n) for n in offerte]

    # — Lotto DESERTO nel ramo lettere (es. gara CIG A01E792BE2: "LOTTO B Deserto"
    # nelle sezioni aggiudicatario e ribasso, ammesse "n° 0"): si marca qui
    # e a fine funzione i campi di aggiudicazione vengono forzati, perche'
    # senza guardia i fallback larghi copiavano ribasso e valore del lotto
    # precedente.
    if re.search(rf'LOTTO\s+{nome_lotto}\s+Deserto\b', testo, re.IGNORECASE):
        lotto["deserto"] = True

    # — Manifestanti in sotto-blocchi "Lotto N" NUDI (es. Pieve a Nievole
    # CIG B433A8B884, 5 lotti): sezione manifestanti con sotto-intestazioni "Lotto N" e
    # righe "0001 NOME manifestazione di interesse del ...". Le chiavi del
    # lotto vengono create SOLO se il sotto-blocco esiste (gli altri PDF del
    # ramo ml restano identici).
    _sez_manif = re.search(
        r'Numero\s+di\s+operatori\s+(?:economici\s+)?manifestanti[\s\S]*?'
        r'(?=Data\s+di\s+spedizione|Numero\s+operatori\s+economici\s+invitati|\Z)',
        testo, re.IGNORECASE
    )
    if _sez_manif:
        _m_subm = re.search(
            rf'(?:^|\n)\s*Lotto\s+{nome_lotto}\s*\n([\s\S]*?)(?=\n\s*Lotto\s+[A-Z0-9]+\s*\n|\Z)',
            _sez_manif.group(0)
        )
        if _m_subm:
            _righe_m = re.findall(
                r'^\s*\d{1,4}\s*(.+?)\s+manifestazione\s+di\s+interesse\s+del\b',
                _m_subm.group(1), re.MULTILINE | re.IGNORECASE
            )
            if _righe_m:
                lotto["num_manifestanti"] = str(len(_righe_m))
                lotto["manifestanti"] = [{"nome": _pulisci_nome(n), "piva": "Non presente", "cf": "Non presente"}
                                         for n in _righe_m]

    # — Offerte TAGGATE per lotto (es. CIG A03589F8C6, 3 lotti: "1. FENIX ... 11/12/2023
    # 10:15:10 – Lotto A, Lotto B e Lotto C"): se le righe della sezione
    # offerte dichiarano i lotti, lista e conteggio del lotto si ricavano dai
    # tag e VINCONO sui pattern generici qui sopra.
    _sez_off = re.search(r'Numero\s+offerte\s+ricevute[\s\S]*?(?=Numero\s+offerte\s+ammesse|$)',
                         testo, re.IGNORECASE)
    if _sez_off:
        _righe_tag = re.findall(
            r'^\s*\d+\.\s+(.+?)\s+\d{1,2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s*[\u2013\u2014-]\s*([^\n]+)',
            _sez_off.group(0), re.MULTILINE
        )
        _mie = [n for n, tags in _righe_tag
                if re.search(rf'\bLotto\s+{nome_lotto}\b', tags, re.IGNORECASE)]
        if _mie:
            lotto["offerte_ricevute"] = [_pulisci_nome(n) for n in _mie]
            lotto["num_offerte_ricevute"] = str(len(_mie))

    # — Offerte in SOTTO-BLOCCHI "LOTTO X n° N offerte" con righe SENZA
    # "offerta del" (es. CIG A01EDD2441, 2 lotti: "0001 BEMAR SRL 05/01/2024 10:46:56"):
    # si ritaglia il sotto-blocco del lotto fino al lotto successivo e si
    # estraggono le righe numerate nome+data+ora.
    if not lotto["offerte_ricevute"] and _sez_off:
        _m_sub = re.search(
            rf'LOTTO\s+{nome_lotto}\s*n[°o]\s*\d+\s*offerte([\s\S]*?)(?=LOTTO\s+[A-Z0-9]+\s*n[°o]|\Z)',
            _sez_off.group(0), re.IGNORECASE
        )
        if _m_sub:
            # \s* (non \s+) dopo il numero di riga: nel layer testuale di alcuni
            # PDF il numero e' INCOLLATO al nome ("0001AURA S.R.L.", CIG A01EDAE68B, 2 lotti)
            # anche se a video lo spazio si vede.
            # Nome "temperato" (?:(?!\n\s*\d{4}\s)[\s\S])+? invece di (.+?):
            # le righe dei RAGGRUPPAMENTI vanno a capo ("0010 A.T.I.: AL.MA...
            # (mandataria) + F.LLI ZACCARIELLO S.R.L.\n(mandante) RTI
            # costituendo data ora", CIG A02046BA2E, 2 lotti) e il punto mono-riga perdeva
            # la voce; il nome puo' proseguire sulla riga dopo ma NON invadere
            # una nuova riga numerata a 4 cifre.
            _righe = re.findall(
                r'^\s*\d{4}\s*((?:(?!\n\s*\d{4}\s)[\s\S])+?)\s+\d{1,2}/\d{2}/\d{4}\s*\d{2}:\d{2}:\d{2}',
                _m_sub.group(1), re.MULTILINE
            )
            if _righe:
                # _pulisci_offerta_ml (non _pulisci_nome): conserva la composizione
                # dei RAGGRUPPAMENTI ("A.T.I.: AL.MA. ... (mandataria) + F.LLI
                # ZACCARIELLO S.R.L. (mandante)", CIG A02046BA2E, 2 lotti) togliendo solo il
                # suffisso "RTI costituendo"; _pulisci_nome troncava alla mandataria.
                lotto["offerte_ricevute"] = [_pulisci_offerta_ml(re.sub(r'\s+', ' ', n)) for n in _righe]
                if lotto["num_offerte_ricevute"] == "Non presente":
                    lotto["num_offerte_ricevute"] = str(len(_righe))

    # — Offerte in sotto-blocchi "Lotto N:" con righe NON numerate
    # "NOME offerta del data ora" (es. Vernio CIG A031086993, 4 lotti). Il confine col
    # lotto successivo puo' essere INCOLLATO alla riga precedente
    # ("...12:59:50Lotto 2:"), quindi niente ancora di inizio riga; \s*
    # prima di "offerta" tollera "S.R.L.offerta" (lotto 2).
    if not lotto["offerte_ricevute"] and _sez_off:
        _m_sub2 = re.search(
            rf'Lotto\s+{nome_lotto}\s*:\s*([\s\S]*?)(?=Lotto\s+[A-Z0-9]+\s*:|\Z)',
            _sez_off.group(0), re.IGNORECASE
        )
        if _m_sub2:
            _righe2 = re.findall(
                r'^\s*(.+?)\s*[Oo]fferta\s+del\b',
                _m_sub2.group(1), re.MULTILINE
            )
            _righe2 = [n for n in _righe2 if n and not re.match(r'^Lotto\b', n, re.IGNORECASE)]
            if _righe2:
                lotto["offerte_ricevute"] = [_pulisci_nome(n) for n in _righe2]
                if lotto["num_offerte_ricevute"] == "Non presente":
                    lotto["num_offerte_ricevute"] = str(len(_righe2))

    # — Offerte in sotto-blocchi "Lotto N" NUDI con righe numerate
    # "0001 NOME offerta del ..." (quarta variante, CIG B433A8B884, 5 lotti).
    if not lotto["offerte_ricevute"] and _sez_off:
        _m_sub3 = re.search(
            rf'(?:^|\n)\s*Lotto\s+{nome_lotto}\s*\n([\s\S]*?)(?=\n\s*Lotto\s+[A-Z0-9]+\s*\n|\Z)',
            _sez_off.group(0)
        )
        if _m_sub3:
            _righe3 = re.findall(
                r'^\s*\d{1,4}\s*(.+?)\s*[Oo]fferta\s+del\b',
                _m_sub3.group(1), re.MULTILINE
            )
            if _righe3:
                lotto["offerte_ricevute"] = [_pulisci_nome(n) for n in _righe3]
                if lotto["num_offerte_ricevute"] == "Non presente":
                    lotto["num_offerte_ricevute"] = str(len(_righe3))

    # Offerte ammesse
    match = re.search(
        r'offerte ammesse[\s\S]{0,200}?' + rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,30}}?n[°o]\s*(\d+)',
        testo, re.IGNORECASE
    )
    if match:
        lotto["num_offerte_ammesse"] = match.group(1)
    else:
        match = re.search(
            r'[Nn]umero\s+offerte\s+ammesse[\s\S]{0,50}?(\d+)', testo_lotto, re.IGNORECASE
        )
        if match:
            lotto["num_offerte_ammesse"] = match.group(1)

    # Offerte escluse
    match = re.search(
        r'offerte escluse[\s\S]{0,200}?' + rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,30}}?n[°o]\s*(\d+)',
        testo, re.IGNORECASE
    )
    if match:
        lotto["num_offerte_escluse"] = match.group(1)

    # Aggiudicatario
    match = re.search(
        rf'[Ll]otto\s+{nome_lotto}\s+'
        rf'([A-Za-z][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)'
        rf'(?:\s*,?\s*[Ss]ede\s+legale|\s*,?\s*[Cc]on\s+sede|\s*\()'  # ,? — "CvC Srl," con virgola prima di "con sede" (CIG A01EDD2441, 2 lotti)
        # "CF 0975..." (CADEL, CIG A03589F8C6, 3 lotti): senza l'alternativa C.F./CF il motore
        # scavalcava nel blocco del lotto successivo rubandone la P.IVA
        rf'[\s\S]{{0,200}}?(?:[Cc]odice\s+fiscale|C\.?F\.?(?:\s+e\s+P\.?I\.?)?)[.:\s]*(\d{{11}})'  # "C.F. e P.I. 0207..." (CIG A01EDD2441, 2 lotti)
        rf'(?:\s*e\s*P\.?\s*I\.?(?:VA)?\.?[.:\s]*(\d{{11}}))?',  # "C.F. X e P.I. Y" separati (CIG B433A8B884, 5 lotti): la P.IVA vera e' la seconda
        testo_aggiud_flat, re.IGNORECASE
    )
    if match:
        # taglia_indirizzi=False: il nome e' GIA' delimitato dal raccordo
        # "sede legale/con sede/(" del pattern; il taglio euristico amputava
        # i nomi che contengono parole-indirizzo ("Polisportiva calcio Via
        # Nova ASD" -> "Polisportiva calcio", CIG B433A8B884, 5 lotti).
        lotto["aggiudicatario_pdf"] = _pulisci_nome(match.group(1), taglia_indirizzi=False)
        lotto["aggiudicatario_piva"] = (match.group(3) or match.group(2)).strip()
    else:
        match = re.search(
            rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,30}}?'
            rf'[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[:\s]*'
            rf'([A-Za-z][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)'
            rf'(?:,\s*con\s+sede|,\s*CAP|\s+con\s+sede|\s*\()'
            rf'[\s\S]{{0,300}}?(?:C\.F\.|P\.I(?:VA)?\.?)\s*e?\s*P?\.?I?\.?\s*(\d{{11}})',
            testo_aggiud_flat, re.IGNORECASE
        )
        if match:
            lotto["aggiudicatario_pdf"] = _pulisci_nome(match.group(1), taglia_indirizzi=True)
            lotto["aggiudicatario_piva"] = match.group(2).strip()
        else:
            match = re.search(
                r'[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[:\s]*'
                r'([A-Za-z][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)'
                r'(?:,\s*con\s+sede|,\s*CAP|\s+con\s+sede|\s*\()'
                r'[\s\S]{0,300}?(?:C\.F\.|P\.I(?:VA)?\.?)\s*e?\s*P?\.?I?\.?\s*(\d{11})',
                testo_lotto, re.IGNORECASE
            )
            if match:
                lotto["aggiudicatario_pdf"] = _pulisci_nome(match.group(1), taglia_indirizzi=True)
                lotto["aggiudicatario_piva"] = match.group(2).strip()
            elif re.search(rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,30}}?[Dd]eserto', testo):
                lotto["aggiudicatario_pdf"] = "Deserto"

    # Ribasso
    match = re.search(
        rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,50}}?[Rr]ibasso[:\s]+?([\d,\.]+)\s*%'
        rf'|[Ll]otto\s+{nome_lotto}[\s\S]{{0,50}}?([\d,\.]+)\s*%',
        testo, re.IGNORECASE
    )
    if not match:
        match = re.search(r'[Rr]ibasso[\s\S]{0,30}?([\d,\.]+)\s*%', testo_lotto, re.IGNORECASE)
        if match:
            lotto["ribasso"] = f"{match.group(1)}%"
    else:
        val = match.group(1) or match.group(2)
        lotto["ribasso"] = f"{val}%"

    # Valore offerta: PRIMA l'adiacenza stretta "Lotto X € importo" (riparto per
    # lotto, es. CIG A03589F8C6, 3 lotti: "Lotto B € 376.978,98"); il pattern largo {0,100} da
    # solo agganciava l'euro del lotto PRECEDENTE via riga dei ribassi.
    match = re.search(
        rf'[Ll]otto\s+{nome_lotto}\s*(?:€|Euro)\s*([\d\.,]+)',
        testo, re.IGNORECASE
    )
    if not match:
        match = re.search(
            rf'[Ll]otto\s+{nome_lotto}[\s\S]{{0,100}}?(?:€|Euro)\s*([\d\.,]+)',
            testo, re.IGNORECASE
        )
    if not match:
        match = re.search(r'(?:Valore|€|Euro)\s*([\d\.,]+)', testo_lotto, re.IGNORECASE)
        if match:
            lotto["valore_offerta"] = f"€ {match.group(1)}"
    else:
        lotto["valore_offerta"] = f"€ {match.group(1)}"

    if lotto["deserto"]:
        lotto["aggiudicatario_pdf"] = "Deserto"
        lotto["aggiudicatario_piva"] = "Non presente"
        lotto["ribasso"] = "Non presente"
        lotto["valore_offerta"] = "Non presente"

    return lotto


def _estrai_multi_lotto_testata_puntata(testo, dati_pdf):
    """
    Multi-lotto in formato standard con CIG in testata a elenco puntato
    "\u2022 Lotto 1 CIG 9404314D68 / \u2022 Lotto 2 CIG 940432567E" (lotto
    prima, poi "CIG", poi codice) e nel corpo N blocchi di aggiudicazione in
    SEQUENZA, non etichettati per lotto (SdS Area Pratese, CIG 9404314D68, 2 lotti). I blocchi
    si agganciano ai lotti per POSIZIONE (1° blocco = Lotto 1, ...).

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    coppie = re.findall(r'[Ll]otto\s+(\d+)\s+CIG\s+([A-Z0-9]{10})\b', testo)
    if len(coppie) < 2:
        return False

    # blocchi del corpo: si affetta su "Numero offerte ricevute"/"offerte
    # ricevute:" e si tengono quelli con un blocco aggiudicatario.
    parti = re.split(r'(?=Numero\s+offerte\s+ricevute|offerte\s+ricevute\s*:)', testo, flags=re.IGNORECASE)
    blocchi = [p for p in parti if re.search(r'aggiudicatario', p, re.IGNORECASE)]
    if len(blocchi) < len(coppie):
        return False

    for idx, (nl, cig) in enumerate(coppie):
        b = blocchi[idx]
        comune = _estrai_singolo_lotto_std(b, b, b.replace('\n', ' '), b)
        # offerta: prima riga dopo l'header "offerte ricevute: n° N", ripulita
        # col taglio-indirizzi (l'ATI prosegue su piu' righe con l'elenco dei
        # consorziati; qui si tiene il capofila, coerente con l'aggiudicatario).
        _offs = []
        _mo = re.search(r'offerte\s+ricevute\s*:?\s*n?[\u00b0o]?\s*\d*\s*\n(.+?)(?=\n(?:Numero|Nome))',
                        b, re.IGNORECASE | re.DOTALL)
        if _mo:
            _n = _pulisci_nome(_mo.group(1), taglia_indirizzi=True)
            if _n:
                _offs.append(_n)
        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig,
            "deserto": False,
            "num_offerte_ricevute": comune.get("num_offerte_ricevute", "Non presente"),
            "offerte_ricevute": _offs if _offs else list(comune.get("offerte_ricevute", [])),
            "num_offerte_ammesse": comune.get("num_offerte_ammesse", "Non presente"),
            "offerte_ammesse": [],   # solo conteggio (travaso rinviato)
            "num_offerte_escluse": comune.get("num_offerte_escluse", "Non presente"),
            "aggiudicatario_pdf": comune.get("aggiudicatario_pdf", "Non presente"),
            "aggiudicatario_piva": comune.get("aggiudicatario_piva", "Non presente"),
            "ribasso": comune.get("ribasso", "Non presente"),
            "valore_offerta": comune.get("valore_offerta", "Non presente"),
        })
    return True


def _estrai_multi_lotto_righe_etichettate(testo, dati_pdf):
    """
    Multi-lotto in formato standard dove OGNI CAMPO ha la propria riga con
    l'etichetta ripetuta e il numero di lotto inline:
        "Numero manifestazioni interesse ricevute Lotto n.1: 1"
        "Numero offerte ricevute Lotto 1: n. 1"
        "Nome e indirizzo dell'aggiudicatario Lotto 1: NOME ... CF NNN"
        "Ribasso offerto Lotto 1: 0,69%"
        "Valore dell'offerta Lotto 1: euro 40.570,01"
    I manifestanti nominativi seguono la riga di intestazione del proprio lotto.
    Ponte Buggianese impianti sportivi 2019, Esito_F-2.

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    def _campo(etichetta, pattern_val):
        """Raccoglie i valori di un campo etichettato, per numero di lotto."""
        return dict(
            (m.group(1), m.group(2).strip())
            for m in re.finditer(
                rf'{etichetta}[^\n]*?Lotto\s*n?\.?\s*(\d+)\s*:\s*{pattern_val}',
                testo, re.IGNORECASE)
        )

    _agg = _campo(r"Nome\s+e\s+indirizzo\s+dell.aggiudicatario", r'(.+)')
    _rib = _campo(r'Ribasso\s+offerto', r'([\d,\.]+)\s*%')
    if len(_agg) < 2 or len(_rib) < 2:
        return False

    _man_n = _campo(r'Numero\s+manifestazioni\s+interesse\s+ricevute', r'(\d+)')
    _off_n = _campo(r'Numero\s+offerte\s+ricevute', r'n?\.?\s*(\d+)')
    _val = _campo(r"Valore\s+dell.offerta", r'\u20ac\s*([\d\.,]+)')

    # manifestanti nominativi: righe che seguono l'intestazione di ogni lotto
    _man_nomi = {}
    for m in re.finditer(
            r'Numero\s+manifestazioni\s+interesse\s+ricevute\s+Lotto\s*n?\.?\s*(\d+)\s*:\s*\d+\n([\s\S]*?)(?=\nNumero|\Z)',
            testo, re.IGNORECASE):
        _man_nomi[m.group(1)] = [r.strip() for r in m.group(2).split("\n") if r.strip()]

    for nl in sorted(set(list(_agg) + list(_rib)), key=int):
        _a = _agg.get(nl, "")
        _cf = re.search(r'\bCF\s*(\d{11})\b', _a)
        _nomi = _man_nomi.get(nl, [])
        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": "Non presente",     # i CIG stanno solo in pagina
            "deserto": False,
            "num_manifestanti": _man_n.get(nl, "Non presente"),
            "manifestanti": [{"nome": _pulisci_nome(n), "piva": "Non presente", "cf": "Non presente"} for n in _nomi],
            "num_offerte_ricevute": _off_n.get(nl, "Non presente"),
            "offerte_ricevute": [],          # il PDF dichiara solo il numero
            "num_offerte_ammesse": "Non presente",
            "offerte_ammesse": [],
            "num_offerte_escluse": "Non presente",
            "aggiudicatario_pdf": _pulisci_nome(_a, taglia_indirizzi=True) if _a else "Non presente",
            "aggiudicatario_piva": _cf.group(1) if _cf else "Non presente",
            "ribasso": f"{_rib[nl]}%" if nl in _rib else "Non presente",
            "valore_offerta": f"\u20ac {_val[nl]}" if nl in _val else "Non presente",
        })
    return True


def _estrai_multi_lotto_sezioni_globali(testo, dati_pdf):
    """
    Multi-lotto in formato standard con SEZIONI GLOBALI (manifestanti,
    invitati, offerte, aggiudicatari, ribassi, valori) ciascuna divisa
    internamente per "Lotto N:". Ogni lotto raccoglie i propri dati da tutte
    le sezioni; i lotti DESERTI ("Lotto 5: n. 0 Lotto deserto") e quelli
    senza aggiudicatario (offerta non ammessa, Lotto 7) sono gestiti.
    Nessun CIG nel PDF: i cig_lotto restano "Non presente" e arrivano dalla
    pagina.

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    def _sezione(inizio, fine):
        """Ritaglia il testo compreso fra due espressioni, vuoto se assente."""
        m = re.search(rf'{inizio}[\s\S]*?(?={fine})', testo, re.IGNORECASE)
        return m.group(0) if m else ""

    def _per_lotto(sez):
        """spezza una sezione sui marcatori "Lotto N:" -> {numero: corpo}"""
        out, parti = {}, re.split(r'Lotto\s+(\d+)\s*:', sez)
        for i in range(1, len(parti) - 1, 2):
            out[parti[i]] = parti[i + 1]
        return out

    _manif = _per_lotto(_sezione(r'Numero\s+manifestazioni\s+interesse\s+ricevute', r'Data\s+di\s+spedizione'))
    _off = _per_lotto(_sezione(r'Numero\s+offerte\s+ricevute', r'Numero\s+offerte\s+NON\s+ammesse|Numero\s+offerte\s+ammesse'))
    _agg = _per_lotto(_sezione(r"Nome\s+e\s+indirizzo\s+dell.aggiudicatario", r'Ribasso\s+di\s+aggiudicazione'))
    if len(_manif) < 2 or len(_off) < 2 or not _agg:
        return False

    _inv = _per_lotto(_sezione(r'Numero\s+operatori\s+economici\s+invitati', r'Numero\s+offerte\s+ricevute'))
    _rib = _per_lotto(_sezione(r'Ribasso\s+di\s+aggiudicazione', r"Valore\s+dell.offerta"))
    _val = _per_lotto(_sezione(r"Valore\s+dell.offerta", r'Subappalto|Data\s+di\s+decisione'))
    _amm = _per_lotto(_sezione(r'Numero\s+offerte\s+ammesse', r"Nome\s+e\s+indirizzo"))

    # nomi con anagrafica in coda: "NOME CF/P.IVA 0044..." / "NOME P.IVA ... CF ..."
    # Il gruppo "coda" cattura i codici che seguono l'etichetta, cosi' la P.IVA
    # puo' essere associata al nome invece di essere scartata.
    # Tollera il refuso "/P,IVA" con la virgola.
    _pat_nome = (r'^\s*(?P<nome>.+?)\s+'
                 r'(?P<etichetta>CF\s*/\s*P[.,]?\s*IVA|P[.,]?\s*IVA\s*/\s*CF|P[.,]?\s*IVA|CF)\b'
                 r'(?P<coda>.*)$')

    def _piva(coda):
        """
        Dalla coda anagrafica ricava la P.IVA. Se la riga distingue i due codici
        ("MARR SPA P.IVA 026... CF 018...") vince quello etichettato P.IVA;
        con l'etichetta unica "CF/P.IVA" il codice e' uno solo. I codici
        troncati del PDF (es. "004406002", 9 cifre) si riportano fedelmente.
        """
        m = re.search(r'P[.,]?\s*IVA[^\d]{0,5}(\d{9,11})', coda or "")
        if m:
            return m.group(1)
        m = re.search(r'(\d{9,11})', coda or "")
        return m.group(1) if m else "Non presente"

    def _num(corpo):
        """Estrae il numero da diciture come "n. 3"; "Non presente" se manca."""
        m = re.search(r'n\.?\s*(\d+)', corpo or "")
        return m.group(1) if m else "Non presente"

    for nl in sorted(_manif, key=int):
        _c_off = _off.get(nl, "")
        _deserto = bool(re.search(r'[Dd]eserto', _c_off))

        _nomi_m = re.findall(_pat_nome, _manif.get(nl, ""), re.MULTILINE)
        _nomi_o = [] if _deserto else re.findall(_pat_nome, _c_off, re.MULTILINE)
        # marcatore "c.s." (come sopra) nella riga invitati del lotto: gli
        # invitati coincidono con i manifestanti DI QUEL LOTTO.
        # Senza questo la lista invitati restava vuota.
        _cs = bool(re.search(r'\bc\.?\s?s\.?(?:\s|$|\.)', _inv.get(nl, "") or ""))

        _a = re.sub(r'\s+', ' ', _agg.get(nl, "")).strip()
        _nome_a = re.split(r'\s+con\s+sede', _a)[0].strip() if _a else ""
        _mcf = re.search(r'P\.?\s*I\.?\s*(\d{11})', _a) if _a else None

        _r = re.search(r'([\d,\.]+)\s*%', _rib.get(nl, "") or "")
        _v = re.search(r'([\d\.,]+)\s*\u20ac', _val.get(nl, "") or "")

        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": "Non presente",     # il PDF non dichiara CIG
            "deserto": _deserto,
            "num_manifestanti": _num(_manif.get(nl, "")) if _nomi_m else "Non presente",
            "manifestanti": [_op(_pulisci_nome(n), _piva(c), e + c) for n, e, c in _nomi_m],
            "num_invitati": _num(_inv.get(nl, "")),
            # "n. N c.s." => stessi operatori dei manifestanti del lotto
            "invitati": ([_op(_pulisci_nome(n), _piva(c), e + c) for n, e, c in _nomi_m]
                         if _cs else []),
            "num_offerte_ricevute": "0" if _deserto else _num(_c_off),
            "offerte_ricevute": [_pulisci_nome(n) for n, _e, _c in _nomi_o],
            "num_offerte_ammesse": _num(_amm.get(nl, "")) if nl in _amm else "Non presente",
            "offerte_ammesse": [],
            "num_offerte_escluse": "Non presente",
            "aggiudicatario_pdf": "Deserto" if _deserto else (_nome_a or "Non presente"),
            "aggiudicatario_piva": _mcf.group(1) if _mcf else "Non presente",
            "ribasso": f"{_r.group(1)}%" if _r else "Non presente",
            "valore_offerta": f"\u20ac {_v.group(1)}" if _v else "Non presente",
        })
    return True


def _estrai_multi_lotto_aggiudicatari_elenco(testo, dati_pdf):
    """
    Multi-lotto in formato standard con:
      - CIG in testata a righe "Lotto N CIG codice";
      - offerte in sezioni "Lotto N. Titolo" (righe "N NOME P.IVA: ...");
      - un unico blocco "Nome e indirizzo degli aggiudicatari:" che elenca
        "Lotto N NOME Codice fiscale NNN con sede legale ...", eventualmente
        con un lotto CONDIVISO ("Lotto 3 e 4 ASSURFINANCE ...");
      - ribasso e valore su righe "Lotto N X %" / "Lotto N euro Y".
    Ponte Buggianese servizi assicurativi, CIG 8396591E14, 4 lotti.

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    cig_map = dict(re.findall(r'Lotto\s+(\d+)\s+CIG\s+([A-Z0-9]{10})\b', testo, re.IGNORECASE))
    if len(cig_map) < 2:
        return False
    _m_blocco = re.search(r"aggiudicatari?\s*:\s*([\s\S]*?)(?=Ribasso\s+di\s+aggiudicazione|\Z)",
                          testo, re.IGNORECASE)
    if not _m_blocco:
        return False

    # aggiudicatario + codice fiscale per lotto ("Lotto 3 e 4 NOME Codice fiscale NNN")
    aggiud = {}
    for m in re.finditer(r'Lotto\s+(\d+(?:\s+e\s+\d+)*)\s+(.+?)\s+Codice\s+fiscale\s+(\d{11})',
                         _m_blocco.group(1), re.IGNORECASE | re.DOTALL):
        _nome = _pulisci_nome(re.sub(r'\s+', ' ', m.group(2)).strip(), taglia_indirizzi=True)
        for _nl in re.findall(r'\d+', m.group(1)):
            aggiud[_nl] = (_nome, m.group(3))

    # offerte per lotto: sezioni "Lotto N. Titolo" dentro la sezione offerte
    offerte = {}
    _m_off = re.search(r'Numero\s+offerte\s+ricevute[\s\S]*?(?=Numero\s+offerte\s+ammesse|\Z)',
                       testo, re.IGNORECASE)
    if _m_off:
        _sez = _m_off.group(0)
        _inte = list(re.finditer(r'(?m)^Lotto\s+(\d+)\.\s*[^\n]*', _sez))
        for i, h in enumerate(_inte):
            _corpo = _sez[h.end(): _inte[i + 1].start() if i + 1 < len(_inte) else len(_sez)]
            _nomi = re.findall(r'^\s*\d+\s*(.+?)\s+P\.?\s*IVA', _corpo, re.MULTILINE)
            offerte[h.group(1)] = [_pulisci_nome(n) for n in _nomi]

    ribassi = dict(re.findall(r'Lotto\s+(\d+)\s+([\d,\.]+)\s*%', testo))
    valori = dict(re.findall(r'Lotto\s+(\d+)\s+\u20ac\s*([\d\.,]+)', testo))

    for nl in sorted(cig_map, key=int):
        _nome, _cf = aggiud.get(nl, ("Non presente", "Non presente"))
        _off = offerte.get(nl, [])
        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig_map[nl],
            "deserto": False,
            "num_offerte_ricevute": str(len(_off)) if _off else "Non presente",
            "offerte_ricevute": _off,
            "num_offerte_ammesse": "Non presente",
            "offerte_ammesse": [],
            "num_offerte_escluse": "Non presente",
            "aggiudicatario_pdf": _nome,
            "aggiudicatario_piva": _cf,
            "ribasso": f"{ribassi[nl]}%" if nl in ribassi else "Non presente",
            "valore_offerta": f"\u20ac {valori[nl]}" if nl in valori else "Non presente",
        })
    return True


def _estrai_multi_lotto_sezioni_maiuscole(testo, dati_pdf):
    """
    Multi-lotto in formato standard con sezioni AUTOCONTENUTE introdotte da
    "LOTTO N" su riga a se' (maiuscolo, senza titolo inline), ognuna col
    proprio blocco invitati/offerte/aggiudicatario/valore, e CIG dichiarati
    in TESTATA in coda alla descrizione di ciascun lotto
    ("- LOTTO 1 - ... NUMERO GARA 7845252 CIG 8397632925;").
    SDS Pistoiese, CIG 8397632925, 2 lotti.

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    # intestazioni di sezione: "LOTTO N" da solo sulla riga
    intest = list(re.finditer(r'(?m)^\s*LOTTO\s+(\d+)\s*$', testo))
    if len(intest) < 2:
        return False

    # CIG dalla TESTATA: si spezza la parte iniziale (prima di "Tipo di
    # procedura") sui marcatori "- LOTTO N" e si prende il CIG di ogni blocco.
    testata = testo[:testo.find("Tipo di procedura")] if "Tipo di procedura" in testo else testo[:2000]
    cig_map = {}
    pezzi = re.split(r'(?m)^\s*[-\u2013]\s*LOTTO\s+(\d+)', testata)
    for i in range(1, len(pezzi) - 1, 2):
        m = re.search(r'CIG\s*[:\s]*([A-Z0-9]{10})\b', pezzi[i + 1])
        if m:
            cig_map[pezzi[i]] = m.group(1)

    for i, h in enumerate(intest):
        nl = h.group(1)
        fine = intest[i + 1].start() if i + 1 < len(intest) else len(testo)
        sez = testo[h.end():fine]
        comune = _estrai_singolo_lotto_std(sez, sez, sez.replace('\n', ' '), sez)

        # Aggiudicatario: puo' essere un RAGGRUPPAMENTO su piu' righe con
        # l'elenco dei mandanti/consorziati ("R.T.I. costituendo: CAPOFILA CF
        # ... (mandataria), ..."). Si tiene il CAPOFILA (fino a "(mandataria)"
        # o al primo CF) e il suo codice fiscale, coerente con gli altri
        # formati che espongono un solo aggiudicatario per lotto.
        _agg = comune.get("aggiudicatario_pdf", "Non presente")
        _piva = comune.get("aggiudicatario_piva", "Non presente")
        _ma = re.search(
            r"aggiudicatario\s*:\s*(.+?)\s*(?:\(mandataria\)|CF\s*\d{11}|,\s*con\s+sede)",
            sez, re.IGNORECASE | re.DOTALL
        )
        if _ma:
            _n = _pulisci_nome(re.sub(r'\s+', ' ', _ma.group(1)), taglia_indirizzi=True)
            if _n:
                _agg = _n
        # P.IVA/CF: il PRIMO codice fiscale che segue "aggiudicatario:" e' quello
        # del capofila. Ha la precedenza sul valore generico di
        # _estrai_singolo_lotto_std, che nella sezione pesca l'ultimo CF
        # incontrato (nel LOTTO 1 era quello di Mestieri, un invitato).
        _mc = re.search(r"aggiudicatario\s*:[\s\S]{0,300}?\bCF\s*(\d{11})\b", sez, re.IGNORECASE)
        if _mc:
            _piva = _mc.group(1)

        # Invitati PER LOTTO: ogni sezione dichiara i propri ("Numero operatori
        # economici invitati: n. 3" nel LOTTO 1, "n. 1" nel LOTTO 2) con l'elenco
        # numerato "N. NOME P.IVA: ...". Il nome puo' andare a capo, quindi si
        # cattura fino a "P.IVA" attraversando le righe.
        _n_inv = re.search(r'operatori\s+economici\s+invitati\s*:?\s*n?\.?\s*(\d+)', sez, re.IGNORECASE)
        _nomi_inv = re.findall(r'^\s*\d+\.\s*((?:(?!\n\s*\d+\.)[\s\S])+?)\s*P\.?\s*IVA', sez, re.MULTILINE | re.IGNORECASE)
        _invitati = [{"nome": _pulisci_nome(re.sub(r'\s+', ' ', n)), "piva": "Non presente", "cf": "Non presente"}
                     for n in _nomi_inv]
        for _voce, _piva_inv in zip(_invitati, re.findall(r'P\.?\s*IVA\s*:?\s*(?:IT-)?(\d{11})', sez, re.IGNORECASE)):
            _voce["piva"] = _piva_inv

        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig_map.get(nl, "Non presente"),
            "deserto": False,
            "num_invitati": _n_inv.group(1) if _n_inv else "Non presente",
            "invitati": _invitati,
            "num_offerte_ricevute": comune.get("num_offerte_ricevute", "Non presente"),
            # il PDF dichiara solo il NUMERO di offerte, non i nomi: la lista
            # generica raccoglierebbe l'elenco degli invitati della sezione.
            "offerte_ricevute": [],
            "num_offerte_ammesse": comune.get("num_offerte_ammesse", "Non presente"),
            # come per le ricevute: il PDF dichiara il numero, non i nomi (la
            # lista generica raccoglierebbe gli invitati della sezione)
            "offerte_ammesse": [],
            "num_offerte_escluse": comune.get("num_offerte_escluse", "Non presente"),
            "aggiudicatario_pdf": _agg,
            "aggiudicatario_piva": _piva,
            "ribasso": comune.get("ribasso", "Non presente"),
            "valore_offerta": comune.get("valore_offerta", "Non presente"),
        })
    return True


def _estrai_multi_lotto_cig_inline(testo, dati_pdf):
    """
    Multi-lotto in formato standard con:
      - testata "CIG LOTTO 1 84145767C7 CIG LOTTO 2 8414580B13" (etichetta CIG
        ripetuta per ogni lotto, sulla stessa riga o su righe vicine);
      - ribasso e valore dichiarati PER LOTTO ma IN LINEA, su un'unica riga
        ("Ribasso di aggiudicazione: LOTTO 1 20,15748 % - LOTTO 2 20,40009 %",
        "Valore dell'offerta: LOTTO 1 € 10.540,00 - LOTTO 2 € 11.658,61");
      - aggiudicatario, offerte e ammesse CONDIVISI tra i lotti (il PDF dice
        esplicitamente "per entrambi i lotti").
    Chiesina Uzzanese/Uzzano, CIG 84145767C7, 2 lotti.

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    coppie = re.findall(r'CIG\s+LOTTO\s+(\d+)\s+([A-Z0-9]{10})\b', testo, re.IGNORECASE)
    if len(coppie) < 2:
        return False

    def _inline_per_lotto(etichetta, pattern_val):
        """Mappa numero_lotto -> valore da una riga "Etichetta: LOTTO 1 X - LOTTO 2 Y"."""
        m = re.search(rf'{etichetta}[^\n]*', testo, re.IGNORECASE)
        if not m:
            return {}
        return dict(re.findall(rf'LOTTO\s+(\d+)\s*({pattern_val})', m.group(0), re.IGNORECASE))

    ribassi = _inline_per_lotto(r'Ribasso[^\n:]*:', r'[\d,\.]+\s*%')
    valori = _inline_per_lotto(r"Valore\s+dell[\'\u2019]offerta[^\n:]*:", r'\u20ac\s*[\d\.,]+')

    # blocco condiviso (offerte/ammesse/aggiudicatario): stessa via del ramo
    # standard mono-lotto, cosi' nomi e P.IVA passano dai pulitori consueti.
    _mn, _inv, sez_off, sez_amm, aggiud_flat = _preprocessa_sezioni_std(testo)
    comune = _estrai_singolo_lotto_std(testo, sez_off, aggiud_flat, sez_amm)

    # "Nome e indirizzo dell'aggiudicatario PER ENTRAMBI I LOTTI: ALIOTH ..."
    # (CIG 84145767C7, 2 lotti): l'inciso che precede i due punti finisce nel nome.
    _agg = comune.get("aggiudicatario_pdf", "Non presente")
    _agg = re.sub(r'^\s*per\s+entrambi\s+i\s+lotti\s*:\s*', '', _agg, flags=re.IGNORECASE)

    for nl, cig in coppie:
        _rib = ribassi.get(nl)
        _val = valori.get(nl)
        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig,
            "deserto": False,
            "num_offerte_ricevute": comune.get("num_offerte_ricevute", "Non presente"),
            "offerte_ricevute": list(comune.get("offerte_ricevute", [])),
            "num_offerte_ammesse": comune.get("num_offerte_ammesse", "Non presente"),
            "offerte_ammesse": [],
            "num_offerte_escluse": comune.get("num_offerte_escluse", "Non presente"),
            "aggiudicatario_pdf": _agg,
            "aggiudicatario_piva": comune.get("aggiudicatario_piva", "Non presente"),
            "ribasso": re.sub(r'\s+', '', _rib) if _rib else comune.get("ribasso", "Non presente"),
            "valore_offerta": re.sub(r'\u20ac\s*', '\u20ac ', _val) if _val else comune.get("valore_offerta", "Non presente"),
        })
    return True


def _estrai_multi_lotto_sezioni_titolo(testo, dati_pdf):
    """
    Multi-lotto in formato standard con sezioni AUTOCONTENUTE per lotto
    "Lotto N \u2013 Titolo" (ognuna col proprio blocco offerte/aggiudicatario/
    ribasso/valore) e CIG dichiarati in TESTATA come "CIG: 9363570667 Lotto 1
    \u2013 Campi sportivi / 93635841F6 Lotto 2 \u2013 Palazzetto" (codice PRIMA,
    lotto DOPO; l'etichetta CIG spesso solo sul primo).

    Ritorna True se riconosce e popola i lotti, False altrimenti.
    """
    # mappa numero_lotto -> CIG dalla testata (codice di 10 alfanum seguito da
    # "Lotto N \u2013"); l'etichetta CIG puo' esserci solo sul primo.
    cig_map = dict((nl, cig) for cig, nl in
                   re.findall(r'([A-Z0-9]{10})\s+Lotto\s+(\d+)\s*[\u2013-]', testo))
    if len(cig_map) < 2:
        return False

    # sezioni del CORPO: "Lotto N \u2013 Titolo" fino alla successiva; si tengono
    # solo quelle che contengono un blocco di aggiudicazione
    intest = list(re.finditer(r'(?im)^\s*Lotto\s+(\d+)\s*[\u2013-][^\n]*', testo))
    trovati = 0
    for i, h in enumerate(intest):
        nl = h.group(1)
        inizio = h.end()
        fine = intest[i + 1].start() if i + 1 < len(intest) else len(testo)
        sez = testo[inizio:fine]
        if not re.search(r'offerte\s+ricevute|aggiudicatario', sez, re.IGNORECASE):
            continue  # intestazione della testata-elenco, non una sezione vera

        comune = _estrai_singolo_lotto_std(sez, sez, sez.replace('\n', ' '), sez)
        # offerte per lotto: righe "NOME indirizzo... P.IVA..." dopo l'header
        # "offerte ricevute: N" (una per riga), ripulite del nome con taglio
        # indirizzi. L'estrazione generica di _estrai_singolo_lotto_std qui
        # produce rumore (titolo/etichette), percio' si estrae direttamente.
        _offs = []
        _mo = re.search(r'offerte\s+ricevute\s*:?\s*\d*\s*\n([\s\S]*?)(?=Numero\s+offerte\s+ammesse|Nome\s+e\s+indirizzo|Ribasso|\Z)',
                        sez, re.IGNORECASE)
        if _mo:
            for _riga in _mo.group(1).split('\n'):
                _riga = _riga.strip()
                if not _riga or re.match(r'(?:Numero|Nome|Ribasso|Valore|Data|Subappalto)', _riga, re.IGNORECASE):
                    continue
                _n = _pulisci_nome(_riga, taglia_indirizzi=True)
                if _n and not _n.lower().startswith(('dilettantistica', 'associazione', 'sportiva')):
                    _offs.append(_n)
        lotto = {
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig_map.get(nl, "Non presente"),
            "deserto": False,
            "num_offerte_ricevute": comune.get("num_offerte_ricevute", "Non presente"),
            "offerte_ricevute": _offs if _offs else list(comune.get("offerte_ricevute", [])),
            "num_offerte_ammesse": comune.get("num_offerte_ammesse", "Non presente"),
            "offerte_ammesse": [],   # solo conteggio (travaso lista rinviato, come negli altri formati)
            "num_offerte_escluse": comune.get("num_offerte_escluse", "Non presente"),
            "aggiudicatario_pdf": comune.get("aggiudicatario_pdf", "Non presente"),
            "aggiudicatario_piva": comune.get("aggiudicatario_piva", "Non presente"),
            "ribasso": comune.get("ribasso", "Non presente"),
            "valore_offerta": comune.get("valore_offerta", "Non presente"),
        }
        dati_pdf["lotti"].append(lotto)
        trovati += 1

    return trovati >= 2


def _estrai_multi_lotto_testata_cig(testo, dati_pdf):
    """
    Multi-lotto in formato standard con lotti dichiarati SOLO in testata come
    "CIG: - lotto 1 Larciano 94103491A9 / - lotto 2 Lamporecchio 94103599E7"
    (CIG 94103491A9, 2 lotti) o "CIG Lotto 1 ... CIG Lotto 2 ..." (CIG 941376222B, 2 lotti), offerte per
    lotto in sotto-blocchi "LOTTO N:" e AGGIUDICAZIONE CONDIVISA (un unico
    aggiudicatario/ribasso/valore/ammesse per tutti i lotti).

    Ritorna True se ha riconosciuto e popolato i lotti, False altrimenti
    (cosi' il chiamante prosegue con la logica standard normale).
    """
    coppie = re.findall(
        r'(?:CIG[:\s-]*|[-\u2013]\s*)[Ll]otto\s+([A-Z0-9]+)\s+[A-Z][a-z]+\s+([A-Z0-9]{10})\b',
        testo
    )
    if len(coppie) < 2:
        return False

    # sezioni preprocessate come nel ramo standard: cosi' _estrai_singolo_lotto_std
    # riceve esattamente cio' che si aspetta.
    _mn, _inv, testo_sez_offerte, testo_sez_ammesse, testo_aggiud_flat = _preprocessa_sezioni_std(testo)
    comune = _estrai_singolo_lotto_std(testo, testo_sez_offerte, testo_aggiud_flat, testo_sez_ammesse)

    # corpo per i sotto-blocchi "LOTTO N:" (offerte per lotto, gara CIG 94103491A9 a 2 lotti)
    sez = re.search(
        r'offerte\s+ricevute\s*:?\s*\n([\s\S]*?)(?=Numero\s+offerte\s+ammesse|Nome\s+e\s+indirizzo|$)',
        testo, re.IGNORECASE
    )
    corpo = sez.group(1) if sez else ""

    for nl, cig in coppie:
        offs = []
        m = re.search(rf'LOTTO\s+{nl}\s*:\s*\n([\s\S]*?)(?=LOTTO\s+[A-Z0-9]+\s*:|\Z)',
                      corpo, re.IGNORECASE)
        if m:
            # righe "N. NOME [data] offerta del [ora]": data/ora possono trovarsi
            # PRIMA di "offerta del" (ordine anomalo, lotto 2 della gara CIG 94103491A9), quindi
            # si taglia il nome al primo gruppo data o a "offerta".
            for riga in re.findall(r'^\s*\d+\.\s*(.+?)\s+offerta\s+del', m.group(1), re.MULTILINE | re.IGNORECASE):
                riga = re.sub(r'\s+\d{1,2}/\d{2}/\d{4}.*$', '', riga)  # via data trascinata
                offs.append(_pulisci_offerta(riga))
        if not offs:
            # nessun sotto-blocco "LOTTO N:" per questo lotto: offerte condivise
            # (CIG 941376222B, 2 lotti, un unico blocco per entrambi i lotti)
            offs = list(comune.get("offerte_ricevute", []))

        lotto = {
            "nome_lotto": f"LOTTO {nl}",
            "cig_lotto": cig,
            "deserto": False,
            "num_offerte_ricevute": str(len(offs)) if offs else comune.get("num_offerte_ricevute", "Non presente"),
            "offerte_ricevute": offs if offs else list(comune.get("offerte_ricevute", [])),
            "num_offerte_ammesse": comune.get("num_offerte_ammesse", "Non presente"),
            "offerte_ammesse": list(comune.get("offerte_ammesse", [])),
            "num_offerte_escluse": comune.get("num_offerte_escluse", "Non presente"),
            "aggiudicatario_pdf": comune.get("aggiudicatario_pdf", "Non presente"),
            "aggiudicatario_piva": comune.get("aggiudicatario_piva", "Non presente"),
            "ribasso": comune.get("ribasso", "Non presente"),
            "valore_offerta": comune.get("valore_offerta", "Non presente"),
        }
        dati_pdf["lotti"].append(lotto)
    return True


def _estrai_multi_lotto_testata_lotto_cig(testo, dati_pdf):
    """
    Ramo dedicato C (CIG 9204491A41, 4 lotti, campi sportivi Carmignano).

    Testata con LOTTO PRIMA e CIG DOPO, una riga per lotto:
        Lotto 1 CIG 9204491A41
        Lotto 2 CIG 9204514D3B
    (il ramo B _estrai_multi_lotto_testata_cig vuole l'ordine inverso,
    "CIG lotto N Comune codice", quindi qui non scatta).

    Nel corpo ogni campo ha la sua sezione, divisa per lotto in tre modi
    diversi che il PDF mescola:
        "LOTTO N: <offerente>"                 offerte ricevute (maiuscolo, due punti)
        "- Lotto N" + riga successiva          aggiudicatario (nome su riga a se')
        "- Lotto N ribasso X %"                ribasso in linea
        "- Lotto N Importo totale offerto Euro X"  valore in linea

    I lotti DESERTI compaiono solo nella sezione offerte ("LOTTO 2: n. 0
    offerte - lotto deserto;") e in nessun'altra: senza questo ramo il
    rilevamento generico li perdeva del tutto, perche' cerca "lotto N" seguito
    da ribasso/Euro/aggiudicatario/due punti e la riga del deserto ha i due
    punti seguiti da "n. 0". Il lotto spariva e il suo CIG di pagina risultava
    "non riscontrato nei PDF".
    """
    _pat_testata = re.compile(r'^\s*Lotto\s+(\d+)\s+CIG\s+([A-Z0-9]{8,11})\s*$',
                              re.MULTILINE | re.IGNORECASE)
    _pat_off = re.compile(r'^LOTTO\s+(\d+)\s*:\s*(.+?)\s*$', re.MULTILINE)
    _pat_rib = re.compile(r'-\s*Lotto\s+(\d+)\s+ribasso\s+([\d,\.]+)\s*%', re.IGNORECASE)
    _pat_val = re.compile(r'-?\s*Lotto\s+(\d+)\s+Importo\s+totale\s+offerto\s+Euro\s+([\d\.,]+)',
                          re.IGNORECASE)
    _pat_agg = re.compile(r'^-?\s*Lotto\s+(\d+)\s*$\n(.+?)(?=\n-?\s*Lotto\s+\d+\s*$|\nRibasso offerto)',
                          re.MULTILINE | re.DOTALL)

    _cig = _pat_testata.findall(testo)
    if len(_cig) < 2:
        return False
    # Marcatori del corpo: servono a distinguere questo formato da CIG 8396591E14, 4 lotti,
    # che ha la stessa testata ma sezioni completamente diverse (ed e' gia'
    # gestito dal suo ramo, piu' in alto nella cascata).
    if not (_pat_off.search(testo) and _pat_rib.search(testo) and _pat_val.search(testo)):
        return False

    _cig = dict(_cig)
    _off = dict(_pat_off.findall(testo))
    _rib = dict(_pat_rib.findall(testo))
    _val = dict(_pat_val.findall(testo))
    _agg = dict(_pat_agg.findall(testo))

    for _n in sorted(_cig, key=int):
        _testo_off = _off.get(_n, "")
        _deserto = bool(re.search(r'deserto|n\.\s*0\s+offert', _testo_off, re.IGNORECASE))
        # L'offerente e' il nome nudo dopo i due punti; nei lotti deserti la
        # riga contiene la dicitura del deserto, non un nome.
        _offerte = [] if (_deserto or not _testo_off) else [_pulisci_nome(_testo_off)]

        _blocco = _agg.get(_n, "").strip()
        _nome_agg, _piva_agg = "Non presente", "Non presente"
        if _blocco and not _deserto:
            # Riuso dell'estrattore standard (cascata di pattern nome+P.IVA),
            # alimentato col blocco appiattito del solo lotto.
            # L'estrattore standard si aspetta la sezione con la sua etichetta
            # iniziale: qui il blocco del lotto ne e' privo (l'etichetta compare
            # una sola volta, sopra l'elenco dei lotti), quindi si antepone.
            _coppie = _estrai_aggiudicatario_std(
                "Nome e indirizzo dell'aggiudicatario: " + _blocco.replace('\n', ' '))
            if _coppie:
                _nome_agg = _pulisci_nome(_coppie[0][0]) or "Non presente"
                _piva_agg = _coppie[0][1] or "Non presente"
            if _piva_agg == "Non presente":
                _m_piva = re.search(r'(?:C\.?F\.?|P\.?\s*IVA)[:\s]*(\d{11})', _blocco, re.IGNORECASE)
                if _m_piva:
                    _piva_agg = _m_piva.group(1)

        dati_pdf["lotti"].append({
            "nome_lotto": f"LOTTO {_n}",
            "cig_lotto": _cig[_n],
            "deserto": _deserto,
            "num_offerte_ricevute": "0" if _deserto else (str(len(_offerte)) if _offerte else "Non presente"),
            "offerte_ricevute": _offerte,
            # "Numero offerte ammesse e valutate: C.S." rimanda alle ricevute
            "num_offerte_ammesse": "0" if _deserto else (str(len(_offerte)) if _offerte else "Non presente"),
            "offerte_ammesse": list(_offerte),
            "num_offerte_escluse": "Non presente",
            "aggiudicatario_pdf": "Deserto" if _deserto else _nome_agg,
            "aggiudicatario_piva": _piva_agg,
            "ribasso": f"{_rib[_n]}%" if _n in _rib else "Non presente",
            "valore_offerta": f"€ {_val[_n]}" if _n in _val else "Non presente",
        })
    return True


def _estrai_formato_standard(testo, dati_pdf, lotto_corrente, indice_lotto):
    """
    Gestisce l'estrazione completa per il formato 'standard'.
    Popola dati_pdf.

    Quando la sezione degli invitati manca o resta vuota, tre regole di
    travaso possono ricostruirla a partire dai manifestanti. Tutte e tre si
    agganciano a una dichiarazione esplicita del verbale, mai alla sola
    assenza della sezione:

      1. "c.s." / "come sopra" accanto al numero degli invitati: il documento
         dichiara che coincidono con i manifestanti. Gestita anche la forma
         "c.s. eccetto X", che ne esclude alcuni.
      2. numero invitati uguale al numero manifestanti, con lista assente:
         l'uguaglianza dei conteggi vale come attestazione.
      3. presenza della data di spedizione delle lettere d'invito: in quei
         bandi gli invitati sono tutti i manifestanti (prassi confermata
         dalla stazione appaltante).

    Un verbale che non dichiara nulla di tutto cio' conserva la lista vuota:
    l'assenza resta dichiarata e non viene colmata per deduzione.
    """
    testo_sez_manifestanti, testo_sez_invitati, testo_sez_offerte, testo_sez_ammesse, testo_aggiud_flat = \
        _preprocessa_sezioni_std(testo)

    # — Num manifestanti —
    # Stesse diciture riconosciute dall'estrazione della sezione: "manifestanti",
    # "che hanno manifestato interesse" e
    # "manifestazioni di interesse ricevute/pervenute".
    match = re.search(
        r'(?:Numero\s+(?:di\s+)?(?:operatori\s+)?(?:economici\s+)?)?'
        r'(?:manifestanti|che\s+hanno\s+manifestato\s+interesse'
        r'|manifestazioni\s+(?:di\s+)?interesse\s+(?:ricevute|pervenute))'
        r'[^\n]{0,50}?(\d+)',  # stessa riga: senza numero in intestazione non pesca cifre dalla lista (es. bando arredi CIG 9060289302)
        testo, re.IGNORECASE
    )
    if match:
        dati_pdf["num_operatori_manifestanti"] = str(int(match.group(1)))

    # — Lista manifestanti —
    manifestanti = _estrai_manifestanti_std(testo_sez_manifestanti)
    if manifestanti:
        # NIENTE deduplica per nome qui: la protezione dai doppi match dei pattern
        # avviene già nei merge additivi (1b/1c/1e) dentro _estrai_manifestanti_std.
        # A questo livello i duplicati sono manifestazioni AUTENTICHE ripetute nel
        # PDF (stessa azienda che manifesta due volte) e vanno conservate
        manifestanti_unici = []
        for nome in manifestanti:
            nome_pulito = _pulisci_nome(nome.strip())
            if len(nome_pulito) > 2:
                manifestanti_unici.append({"nome": nome_pulito, "piva": "Non presente", "cf": "Non presente"})
        # Cerca P.IVA associata a ciascun manifestante nella sezione (quando presente)
        for _md in manifestanti_unici:
            _idx = testo_sez_manifestanti.lower().find(_md["nome"].lower())
            if _idx >= 0:
                # La P.IVA di una voce sta sempre sulla SUA riga: si cerca da dove inizia il
                # nome fino al fine riga. Una finestra a lunghezza fissa non andrebbe bene in
                # entrambi i sensi: troppo corta, manca la P.IVA quando il nome è seguito da
                # un indirizzo lungo (es. bando cani randagi Monsummano, CIG 7904504B0B, dove
                # il codice cade appena oltre i 100 caratteri); troppo lunga, sconfina nella
                # voce successiva e ne pesca la P.IVA. Il fine riga è il confine naturale.
                _fine_riga = testo_sez_manifestanti.find('\n', _idx)
                _vicino = (testo_sez_manifestanti[_idx:] if _fine_riga < 0
                           else testo_sez_manifestanti[_idx:_fine_riga])
                # Etichetta P.IVA o P.I. (anche preceduta da "C.F. E", quando codice fiscale
                # e partita IVA coincidono: "C.F. E P.I. 02022820019").
                # (?:\s*/\s*C\.?F\.?\.?)? copre "P.IVA/C.F. 02197770502" e "P.IVA/ C.F. ..."
                # con lo slash (es. bando servizi Serravalle, CIG 821750861F), come già fa il
                # pattern degli invitati.
                _pm = re.search(
                    r'P\.\s?I(?:VA)?\.?(?:\s*/\s*C\.?F\.?\.?)?[.:\s]+(?:IT-\s*)?(\d{11})',
                    _vicino, re.IGNORECASE
                )
                if _pm:
                    _md["piva"] = _pm.group(1)
        dati_pdf["operatori_manifestanti"] = manifestanti_unici
        if dati_pdf["num_operatori_manifestanti"] == "Non presente":
            dati_pdf["num_operatori_manifestanti"] = str(len(manifestanti_unici))

    # — Fallback ELENCO DI NOMI NUDI (uno per riga, senza numerazione ne' data):
    # scatta solo se il conteggio DICHIARATO e' molto maggiore di quanto estratto
    # dalle cascate.
    _n_dich = dati_pdf.get("num_operatori_manifestanti", "Non presente")
    if (_n_dich != "Non presente" and str(_n_dich).isdigit()
            and int(_n_dich) >= 5
            and len(dati_pdf.get("operatori_manifestanti", [])) <= 2):
        _nudi = _elenco_nomi_nudi(testo_sez_manifestanti)
        if len(_nudi) > len(dati_pdf.get("operatori_manifestanti", [])):
            dati_pdf["operatori_manifestanti"] = [
                {"nome": n, "piva": "Non presente", "cf": "Non presente"} for n in _nudi
            ]

    # — Num invitati —
    match = re.search(
        r'(?:Numero\s+(?:di\s+)?(?:operatori\s+|soggetti\s+|OO\.?\s*EE\.?\s+)?(?:economici\s+)?(?:invitati|(?:pre\s+)?selezionati|estratti\s+a\s+sorte)'
        r'|Operatori\s+economici\s+(?:con\s+manifestazione\s+di\s+interesse\s+(?:completa\s+e\s+corretta\s+)?)?invitati)'
        r'[^\n]{0,50}?(\d+)\s*(c\.?\s?s\.?\b|come\s+sopra)?',  # stessa riga (es. bando arredi CIG 9060289302)
        # Il marcatore "gli invitati sono gli stessi dei manifestanti" compare sia abbreviato
        # ("n. 10 c.s.") sia per esteso ("n.1 come sopra", es. bando impianto sportivo Pescia
        # CIG 8146826D7B; "4 Come sopra", es. CIG 87408265CC): senza la forma estesa il travaso dai
        # manifestanti non scattava e la lista invitati restava vuota.
        testo, re.IGNORECASE
    )
    if match:
        dati_pdf["num_operatori_invitati"] = match.group(1)
        e_come_sopra = bool(match.group(2))
    else:
        e_come_sopra = False

    # — Lista invitati —
    dati_pdf["operatori_invitati"] = _estrai_invitati_std(testo_sez_invitati)

    # La formula "Come sopra" su riga a se' (Esito_F-2: "Numero operatori
    # economici invitati: 3\nCome sopra") non e' un nome: va scartata, cosi'
    # il meccanismo e_come_sopra qui sotto puo' copiare i manifestanti.
    dati_pdf["operatori_invitati"] = [
        _v for _v in dati_pdf["operatori_invitati"]
        if not re.fullmatch(r'\s*come\s+sopra\s*[.;]?\s*',
                            _v["nome"] if isinstance(_v, dict) else str(_v), re.IGNORECASE)
    ]

    # Se "c.s." (come sopra): copia manifestanti come invitati
    if e_come_sopra and not dati_pdf["operatori_invitati"] and dati_pdf["operatori_manifestanti"]:
        _m_eccetto = re.search(r'c\.?s\.?\s+eccetto\s+([^\n;]+)', testo, re.IGNORECASE)
        _esclusi = set()
        if _m_eccetto:
            _nomi_esclusi = re.split(r'\s+e\s+|,\s*', _m_eccetto.group(1).strip().rstrip(';.'))
            for _ne in _nomi_esclusi:
                _ne = _pulisci_nome(_ne.strip())
                if _ne:
                    _esclusi.add(_ne.upper())
        dati_pdf["operatori_invitati"] = [
            {"nome": m["nome"], "piva": m.get("piva", "Non presente"), "cf": m.get("cf", "Non presente")}
            for m in dati_pdf["operatori_manifestanti"]
            if m["nome"].upper().rstrip('., ') not in _esclusi
        ]
    # Se num_invitati == num_manifestanti e lista invitati vuota → copia manifestanti
    if (not dati_pdf["operatori_invitati"]
            and dati_pdf["operatori_manifestanti"]
            and dati_pdf["num_operatori_invitati"] != "Non presente"
            and dati_pdf["num_operatori_manifestanti"] != "Non presente"
            and dati_pdf["num_operatori_invitati"] == dati_pdf["num_operatori_manifestanti"]):
        dati_pdf["operatori_invitati"] = [
            {"nome": m["nome"], "piva": m.get("piva", "Non presente"), "cf": m.get("cf", "Non presente")}
            for m in dati_pdf["operatori_manifestanti"]
        ]
    # Nessuna sezione invitati, ma il verbale dichiara la SPEDIZIONE delle
    # lettere d'invito: in quei bandi gli invitati sono tutti i manifestanti
    # (prassi confermata dalla stazione appaltante). Il travaso si aggancia a
    # una dichiarazione esplicita del documento, non alla sola assenza della
    # sezione: un verbale che tace sull'invito (es. CIG B60BE1C6E8, che al suo posto
    # riporta "Data di pubblicazione procedura") NON viene toccato e conserva
    # l'assenza dichiarata.
    # Etichette osservate: "Data di spedizione della Lettera d'invito:" (la
    # forma piu' diffusa) e "Data spedizione invito:" (CIG B4E0C0A4C3); "di" e
    # "della Lettera" sono percio' facoltativi nel pattern.
    if not dati_pdf["operatori_invitati"] and dati_pdf["operatori_manifestanti"]:
        if dichiara_invio_invito(testo):
            dati_pdf["operatori_invitati"] = [
                {"nome": m["nome"], "piva": m.get("piva", "Non presente"),
                 "cf": m.get("cf", "Non presente")}
                for m in dati_pdf["operatori_manifestanti"]
            ]
    # Intestazione invitati SENZA numero (la lista parte subito sotto, es. bando
    # arredi CIG 9060289302): deriva il conteggio dalla lunghezza della lista estratta
    if dati_pdf["num_operatori_invitati"] == "Non presente" and dati_pdf["operatori_invitati"]:
        dati_pdf["num_operatori_invitati"] = str(len(dati_pdf["operatori_invitati"]))

    # — Rilevamento lotti —
    # Ramo dedicato A00000: ogni campo su riga propria con etichetta ripetuta
    # e numero di lotto inline ("Ribasso offerto Lotto 1: ...").
    if _estrai_multi_lotto_righe_etichettate(testo, dati_pdf):
        return

    # Ramo dedicato A0000: SEZIONI GLOBALI divise internamente per "Lotto N:"
    # (manifestanti/invitati/offerte/aggiudicatari/ribassi/valori), lotti
    # deserti inclusi, nessun CIG nel PDF.
    if _estrai_multi_lotto_sezioni_globali(testo, dati_pdf):
        return

    # Ramo dedicato A000: elenco aggiudicatari "Lotto N NOME Codice fiscale
    # NNN" (con lotti condivisi "Lotto 3 e 4") + offerte in sezioni
    # "Lotto N. Titolo" + ribassi/valori per lotto su righe (CIG 8396591E14, 4 lotti).
    if _estrai_multi_lotto_aggiudicatari_elenco(testo, dati_pdf):
        return

    # Ramo dedicato A00: sezioni "LOTTO N" (riga a se') autocontenute, CIG in
    # coda alla descrizione di ciascun lotto in testata (CIG 8397632925, 2 lotti).
    if _estrai_multi_lotto_sezioni_maiuscole(testo, dati_pdf):
        return

    # Ramo dedicato A0: testata "CIG LOTTO N codice" ripetuta + ribasso/valore
    # per lotto IN LINEA su una riga sola, resto condiviso (CIG 84145767C7, 2 lotti).
    if _estrai_multi_lotto_cig_inline(testo, dati_pdf):
        return

    # Ramo dedicato A: sezioni autocontenute "Lotto N \u2013 Titolo" con CIG in
    # testata (codice prima, lotto dopo), aggiudicazione PER LOTTO.
    if _estrai_multi_lotto_sezioni_titolo(testo, dati_pdf):
        return

    # Ramo dedicato A2: testata a elenco puntato "Lotto N CIG codice" e blocchi
    # sequenziali per lotto agganciati per posizione (CIG 9404314D68, 2 lotti).
    if _estrai_multi_lotto_testata_puntata(testo, dati_pdf):
        return

    # Ramo dedicato B: lotti in TESTATA con CIG ("- lotto 1 Comune CODICE") e
    # aggiudicazione condivisa (CIG 941376222B, 2 lotti, CIG 94103491A9, 2 lotti). Se lo riconosce, popola
    # i lotti e termina; altrimenti prosegue col rilevamento standard.
    if _estrai_multi_lotto_testata_cig(testo, dati_pdf):
        return

    # Ramo dedicato C: testata "Lotto N CIG codice" (lotto prima, CIG dopo) con
    # sezioni per lotto miste, lotti deserti dichiarati SOLO nelle offerte
    # (CIG 9204491A41, 4 lotti).
    if _estrai_multi_lotto_testata_lotto_cig(testo, dati_pdf):
        return

    nomi_lotti_trovati = re.findall(
        r'LOTTO\s+([A-Z0-9]+)\s+(?:n[°o]\s*\d+|[Aa]ggiudicatario|[Dd]eserto|[Rr]ibasso)',
        testo
    )
    if not nomi_lotti_trovati:
        nomi_lotti_trovati = re.findall(
            r'[Ll]otto\s+([A-Z0-9]+)\s*(?:ribasso|€|Euro|[Aa]ggiudicatario|[Dd]eserto|:)',
            testo
        )
    if not nomi_lotti_trovati:
        # Lotti dichiarati SOLO in testata come "CIG Lotto 1 Lamporecchio
        # 941376222B CIG Lotto 2 Larciano 94137708C0" (tesorerie a 2 lotti): due o piu'
        # CIG per lotto senza sezioni per-lotto nel corpo (aggiudicazione
        # condivisa). Si rilevano dai numeri di lotto della testata CIG.
        nomi_lotti_trovati = re.findall(r'CIG\s+[Ll]otto\s+([A-Z0-9]+)\b', testo)
    nomi_lotti_unici = list(dict.fromkeys([n.upper() for n in nomi_lotti_trovati]))

    # — Multi-lotto —
    if len(nomi_lotti_unici) >= 2:
        if indice_lotto is not None and len(nomi_lotti_unici) > indice_lotto:
            lotto_da_estrarre = nomi_lotti_unici[indice_lotto]
        elif lotto_corrente and lotto_corrente in nomi_lotti_unici:
            lotto_da_estrarre = lotto_corrente
        else:
            lotto_da_estrarre = None
        lotti_da_processare = [lotto_da_estrarre] if lotto_da_estrarre else nomi_lotti_unici

        for nome_lotto in lotti_da_processare:
            if nome_lotto not in nomi_lotti_unici:
                dati_pdf["lotti"].append({
                    "nome_lotto": f"LOTTO {nome_lotto}",
                    "num_offerte_ricevute": "Non presente",
                    "offerte_ricevute": [],
                    "num_offerte_ammesse": "Non presente",
                    "offerte_ammesse": [],
                    "num_offerte_escluse": "Non presente",
                    "aggiudicatario_pdf": "Non presente",
                    "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
                    "ribasso": "Non presente",
                    "valore_offerta": "Non presente"
                })
                continue
            altri_lotti = [l for l in nomi_lotti_unici if l != nome_lotto]
            dati_pdf["lotti"].append(
                _estrai_lotto_ml_std(testo, nome_lotto, altri_lotti, testo_aggiud_flat)
            )

        # — AGGIUDICAZIONE CONDIVISA tra i lotti (CIG 941376222B, 2 lotti: "Lotto 1 e Lotto 2",
        # un unico blocco offerte/aggiudicatario/ribasso/valore per entrambi).
        # I lotti rimasti scoperti ereditano il blocco unico, estratto con
        # l'helper del singolo lotto. Le offerte "N.1\nNOME Codice fiscale ..."
        # e la P.IVA anomala vengono sistemate da _estrai_singolo_lotto_std.
        scoperti = [l for l in dati_pdf["lotti"] if l["aggiudicatario_pdf"] == "Non presente"]
        if scoperti:
            comune = _estrai_singolo_lotto_std(testo, testo_sez_offerte, testo_aggiud_flat, testo_sez_ammesse)
            for l in scoperti:
                for campo in ("num_offerte_ricevute", "offerte_ricevute",
                              "num_offerte_ammesse", "offerte_ammesse",
                              "aggiudicatario_pdf", "aggiudicatario_piva",
                              "ribasso", "valore_offerta"):
                    if campo in comune:
                        l[campo] = comune[campo]
    # — Singolo lotto —
    else:
        dati_pdf["lotti"].append(
            _estrai_singolo_lotto_std(testo, testo_sez_offerte, testo_aggiud_flat, testo_sez_ammesse)
        )

# ── Estrattori formato PER_LOTTO ──────────────────────────────────────────────

def _estrai_lotto_per_lotto(testo, nome_lotto, altri_lotti):
    """
    Estrae dati per un singolo lotto nel formato 'per_lotto'.
    Restituisce il dict lotto completo.
    """
    lotto = {
        "nome_lotto": f"LOTTO {nome_lotto}",
        "cig_lotto": "Non presente",
        "deserto": False,
        "num_manifestanti": "Non presente",
        "manifestanti": [],
        "num_invitati": "Non presente",
        "invitati": [],
        "num_offerte_ricevute": "Non presente",
        "offerte_ricevute": [],
        "num_offerte_ammesse": "Non presente",
        "offerte_ammesse": [],
        "num_offerte_escluse": "Non presente",
        "aggiudicatario_pdf": "Non presente",
        "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
        "ribasso": "Non presente",
        "valore_offerta": "Non presente"
    }

    # — Manifestanti —
    pattern_inizio = rf'LOTTO\s*{nome_lotto}[:\s]*manifestanti'
    match_inizio = re.search(pattern_inizio, testo, re.IGNORECASE)
    if match_inizio:
        pos_inizio = match_inizio.start()
        if altri_lotti:
            pattern_fine = r'LOTTO\s*(?:' + '|'.join(altri_lotti) + r')[:\s]*manifestanti'
            match_fine = re.search(pattern_fine, testo[pos_inizio + 10:], re.IGNORECASE)
            if match_fine:
                testo_manifestanti_lotto = testo[pos_inizio: pos_inizio + 10 + match_fine.start()]
            else:
                match_fine_sezione = re.search(
                    r'[Nn]umero\s+operatori\s+economici\s+invitati',
                    testo[pos_inizio + 10:]
                )
                testo_manifestanti_lotto = (
                    testo[pos_inizio: pos_inizio + 10 + match_fine_sezione.start()]
                    if match_fine_sezione else testo[pos_inizio:]
                )
        else:
            testo_manifestanti_lotto = testo[pos_inizio:]
    else:
        testo_manifestanti_lotto = testo

    match = re.search(
        rf'LOTTO\s*{nome_lotto}[:\s]*manifestanti\s+(\d+)', testo, re.IGNORECASE
    )
    if match:
        lotto["num_manifestanti"] = match.group(1)

    testo_pulito = re.sub(
        r'^LOTTO\s+\w+[:\s]*manifestanti\s*\d+', '', testo_manifestanti_lotto,
        count=1, flags=re.IGNORECASE | re.MULTILINE
    )
    manifestanti = re.findall(
        r'^\d{1,4}\s*([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&"()/àèìòùÀÈÌÒÙ]+?)\s*[Mm]anifestazione\s+(?:di\s+)?(?:interesse\s+)?del',
        testo_pulito, re.MULTILINE
    )
    if not manifestanti:
        # (?:\d{1,4}[.)]\s*)? — le righe "1. NOME data ora" (Abetone CIG A023816D71, 5 lotti)
        # hanno il numero col punto: va scartato, non incluso nel nome.
        # \s* tra data e ora: tollera l'incollatura "10/11/202310:57:08"
        # (voce LA PIASTRA, lotto 2) che altrimenti perde la riga.
        manifestanti = re.findall(
            r'^(?:\d{1,4}[.)]\s*)?([A-Za-z0-9][A-Za-z0-9 \'\.\-–&àèìòùÀÈÌÒÙ]+?)\s+\d{1,2}/\d{2}/\d{4}\s*\d{2}:\d{2}:\d{2}',
            testo_pulito, re.MULTILINE
        )
    if manifestanti:
        visti = set()
        for nome in manifestanti:
            nome_pulito = nome.strip()
            if (nome_pulito.upper() not in visti
                    and not re.match(r'^LOTTO', nome_pulito, re.IGNORECASE)
                    and 'manifestanti' not in nome_pulito.lower()
                    and 'invitati' not in nome_pulito.lower()
                    and not re.search(r'\d+$', nome_pulito)):
                visti.add(nome_pulito.upper())
                # dict {"nome","piva"} come in tutti gli altri formati (l'embrione
                # per_lotto usava stringhe: incoerenza latente, _stampa_operatore
                # e l'ereditarieta' invitati=manifestanti si aspettano dict)
                lotto["manifestanti"].append({"nome": nome_pulito, "piva": "Non presente", "cf": "Non presente"})

    # — Invitati —
    pattern_inizio_inv = rf'LOTTO\s*{nome_lotto}[:\s\.]*invitat'
    match_inizio_inv = re.search(pattern_inizio_inv, testo, re.IGNORECASE)
    if match_inizio_inv:
        pos_inizio_inv = match_inizio_inv.start()
        if altri_lotti:
            pattern_fine_inv = r'LOTTO\s*(?:' + '|'.join(altri_lotti) + r')[:\s\.]*invitat'
            match_fine_inv = re.search(pattern_fine_inv, testo[pos_inizio_inv + 10:], re.IGNORECASE)
            if match_fine_inv:
                testo_invitati_lotto = testo[pos_inizio_inv: pos_inizio_inv + 10 + match_fine_inv.start()]
            else:
                match_fine_sezione_inv = re.search(
                    r'[Dd]ata\s+di\s+spedizione', testo[pos_inizio_inv + 10:]
                )
                testo_invitati_lotto = (
                    testo[pos_inizio_inv: pos_inizio_inv + 10 + match_fine_sezione_inv.start()]
                    if match_fine_sezione_inv else testo[pos_inizio_inv:]
                )
        else:
            testo_invitati_lotto = testo[pos_inizio_inv:]
    else:
        testo_invitati_lotto = ""

    match = re.search(
        rf'[Ll]otto\s+{nome_lotto}[:\s\.]+invitat\w*\s+(\d+)', testo, re.IGNORECASE
    )
    if match:
        lotto["num_invitati"] = match.group(1)

    if testo_invitati_lotto:
        invitati = re.findall(
            r'^([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)\s*$',
            testo_invitati_lotto, re.MULTILINE
        )
        if invitati:
            visti = set()
            for nome in invitati:
                nome_pulito = nome.strip()
                if (nome_pulito.upper() not in visti
                        and len(nome_pulito) > 3
                        and not re.match(r'^LOTTO', nome_pulito, re.IGNORECASE)
                        and not re.match(r'^[Nn]umero', nome_pulito)
                        and not re.match(r'^[Dd]ata', nome_pulito)
                        and 'manifestanti' not in nome_pulito.lower()
                        and 'invitati' not in nome_pulito.lower()
                        and not re.search(r'\d+$', nome_pulito)):
                    visti.add(nome_pulito.upper())
                    lotto["invitati"].append({"nome": nome_pulito, "piva": "Non presente", "cf": "Non presente"})

    # — Offerte —
    pattern_inizio_off = rf'LOTTO\s*{nome_lotto}[:\s]*offerte'
    match_inizio_off = re.search(pattern_inizio_off, testo, re.IGNORECASE)
    if match_inizio_off:
        pos_inizio_off = match_inizio_off.start()
        if altri_lotti:
            pattern_fine_off = r'[Ll]otto\s*(?:' + '|'.join(altri_lotti) + r')[:\s]*offerte'
            match_fine_off = re.search(pattern_fine_off, testo[pos_inizio_off + 10:], re.IGNORECASE)
            testo_offerte_lotto = (
                testo[pos_inizio_off: pos_inizio_off + 10 + match_fine_off.start()]
                if match_fine_off else testo[pos_inizio_off:]
            )
        else:
            testo_offerte_lotto = testo[pos_inizio_off:]
    else:
        testo_offerte_lotto = ""

    match = re.search(
        rf'LOTTO\s*{nome_lotto}[:\s]*offerte\s+(\d+)', testo, re.IGNORECASE
    )
    if match:
        lotto["num_offerte_ricevute"] = match.group(1)

    if testo_offerte_lotto:
        # prefisso "N." scartato; \s* prima di "offerta": tollera l'incollatura
        # "F.P.E S.R.L.offerta del" (lotto 5 della gara CIG A023816D71) che perdeva la riga.
        offerte = re.findall(
            r'^(?:\d{1,4}[.)]\s*)?([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)\s*[Oo]fferta\s+del',
            testo_offerte_lotto, re.MULTILINE
        )
        if offerte:
            lotto["offerte_ricevute"] = [nome.strip() for nome in offerte]

    # — Offerte CONDIVISE "LOTTO 1 e LOTTO 2" (CIG 9435123DCC): un unico blocco
    # offerte vale per piu' lotti. Se il lotto non ha trovato offerte proprie
    # ma esiste un blocco intestato al suo numero INSIEME ad altri, lo eredita.
    if not lotto["offerte_ricevute"]:
        # Intestazione condivisa "LOTTO 1 e LOTTO 2" su riga a se', dopo
        # "offerte ricevute:"; le righe offerta seguono. Si aggancia se il
        # numero di QUESTO lotto compare nell'intestazione condivisa.
        # intestazione condivisa = RIGA INTERA dopo "offerte ricevute:" che
        # contiene "LOTTO" (es. "LOTTO 1 e LOTTO 2"); poi le righe offerta.
        m_cond = re.search(
            r'offerte\s+ricevute\s*:?\s*\n([^\n]*LOTTO[^\n]*)\n([\s\S]*?)(?=Numero\s+offerte\s+ammesse|Nome\s+e\s+indirizzo|\Z)',
            testo, re.IGNORECASE
        )
        if m_cond and re.search(rf'\b{nome_lotto}\b', m_cond.group(1)):
            _corpo = m_cond.group(2)
        else:
            _corpo = None
        if _corpo:
            _off = re.findall(
                r'^(?:\d{1,4}[.)]\s*)?([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)\s*[Oo]fferta\s+del',
                _corpo, re.MULTILINE
            )
            if _off:
                lotto["offerte_ricevute"] = [n.strip() for n in _off]
                if lotto["num_offerte_ricevute"] == "Non presente":
                    lotto["num_offerte_ricevute"] = str(len(_off))

    # — Aggiudicatario — RITAGLIATO per lotto: il blocco "LOTTO N\nNome e
    # indirizzo dell'aggiudicatario: ... Ribasso ... Valore ..." si ripete per
    # ogni lotto (CIG 9435123DCC). Si isola quello di QUESTO lotto fino al lotto
    # successivo, cosi' ribasso e valore non si mescolano tra lotti.
    _m_lotto_agg = re.search(
        rf'LOTTO\s*{nome_lotto}\b\s*\n\s*[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario\s*:?'
        rf'([\s\S]*?)(?=\n\s*LOTTO\s*[A-Z0-9]+\s*\n\s*[Nn]ome\s+e\s+indirizzo|\Z)',
        testo, re.IGNORECASE
    )
    sezione_aggiud = _m_lotto_agg or re.search(r'[Nn]ome\s+e\s+indirizzo\s+dell.aggiudicatario[\s\S]*', testo)
    if sezione_aggiud:
        if _m_lotto_agg:
            # ramo per-lotto: il nome inizia subito, aggiungo un'etichetta fittizia
            # cosi' il pattern LOTTO N ... sotto trova comunque il suo attacco
            testo_aggiud_sezione = (f"LOTTO {nome_lotto} " + sezione_aggiud.group(1)).replace('\n', ' ')
        else:
            testo_aggiud_sezione = sezione_aggiud.group(0).replace('\n', ' ')
        # ([\s\S]+?): il blocco contiene parentesi, virgole, punti e virgola
        # ("(PT)", "P.I.\n0160...;") — la vecchia classe ristretta non poteva
        # attraversarli e il match INTERO falliva: nessun aggiudicatario estratto.
        pattern_aggiud = (
            rf'LOTTO\s+{nome_lotto}\s+'
            rf'([\s\S]+?)'
            rf'(?=\s+LOTTO\s+[A-Z0-9]+\s|\s+[Oo]rgano\s+competente|$)'
        )
        match_aggiud = re.search(pattern_aggiud, testo_aggiud_sezione)
        if match_aggiud:
            testo_aggiud = match_aggiud.group(1)
            match_nome = re.search(
                r'^([A-Za-z0-9][A-Za-z0-9\s\'\.\-–&àèìòùÀÈÌÒÙ]+?)'
                r'(?:,\s*con\s+sede|\s+con\s+sede|\s*\(|$)',
                testo_aggiud
            )
            if match_nome and match_nome.group(1).strip():
                lotto["aggiudicatario_pdf"] = match_nome.group(1).strip()
            else:
                # indirizzo attaccato al nome senza "con sede" ne' parentesi
                # ("INTESA SANPAOLO S.P.A. PIAZZA SAN CARLO...", CIG 9435123DCC):
                # _pulisci_nome col taglio-indirizzi tronca su "PIAZZA".
                _n = _pulisci_nome(testo_aggiud.strip(), taglia_indirizzi=True)
                if _n:
                    lotto["aggiudicatario_pdf"] = _n
            # Ribasso e valore DAL blocco per-lotto (testo_aggiud)
            _mr = re.search(r'[Rr]ibasso[^\d]*([\d,\.]+)\s*%', testo_aggiud)
            if _mr:
                lotto["ribasso"] = f"{_mr.group(1)}%"
            _mv = re.search(r"Valore\s+dell[\'\u2019]offerta[^\d\u20ac]*(?:\u20ac|Euro)\s*([\d\.,]+)", testo_aggiud)
            if _mv:
                lotto["valore_offerta"] = f"\u20ac {_mv.group(1).rstrip(',').strip()}"
            # P.IVA: l'embrione non la estraeva affatto. Etichette viste in
            # gara CIG A023816D71: "P.I. 0160..." e "Partita IVA 0353..." (lotto 5).
            # Preferenza al C.F. quando P.IVA e C.F. sono ENTRAMBI presenti e
            # diversi ("P.IVA -11991500015, C.F. 00799960158", CIG 9435123DCC): il
            # C.F. numerico di 11 cifre e' l'identificativo fiscale primario di
            # queste societa'. Se c'e' solo la P.IVA, si usa quella.
            match_cf = re.search(r'(?:C\.?F\.?|[Cc]odice\s+fiscale)[.:\s]*(\d{11})', testo_aggiud)
            match_pi = re.search(r'(?:P\.?\s*I\.?(?:VA)?\.?|Partita\s+IVA)[.:\s-]*(\d{11})', testo_aggiud)
            if match_cf:
                lotto["aggiudicatario_piva"] = match_cf.group(1)
            elif match_pi:
                lotto["aggiudicatario_piva"] = match_pi.group(1)
            match_ribasso = re.search(
                r'[Rr]ibasso\s+offerto[:\s]+?([\d,\.]+)\s*%', testo_aggiud, re.IGNORECASE
            )
            if match_ribasso:
                lotto["ribasso"] = f"{match_ribasso.group(1)}%"
            match_valore = re.search(
                r'[Vv]alore\s+dell.offerta[\s\S]{0,30}?(?:€|Euro)\s*([\d\.,]+)',
                testo_aggiud, re.IGNORECASE
            )
            if match_valore:
                lotto["valore_offerta"] = f"€ {match_valore.group(1)}"

    return lotto


def _estrai_formato_per_lotto(testo, dati_pdf, lotto_corrente, indice_lotto):
    """
    Gestisce l'estrazione completa per il formato 'per_lotto'.
    Popola dati_pdf.
    """
    # "LOTTO2:" INCOLLATO (San Marcello Piteglio CIG 9435123DCC) oltre a
    # "LOTTO 1 :": il numero puo' non avere spazio prima. [A-Z]? evita che
    # "LOTTO2" catturi "2" con la O finale — qui il gruppo prende solo cifre/
    # lettere del nome lotto.
    nomi_lotti_trovati = re.findall(
        r'LOTTO\s*([A-Z0-9]+)\s*[:\s]*[Mm]anifestanti\s*\d+', testo, re.IGNORECASE
    )
    nomi_lotti_unici = list(dict.fromkeys([n.upper() for n in nomi_lotti_trovati]))

    if indice_lotto is not None and len(nomi_lotti_unici) > indice_lotto:
        lotto_da_estrarre = nomi_lotti_unici[indice_lotto]
    elif lotto_corrente and lotto_corrente in nomi_lotti_unici:
        lotto_da_estrarre = lotto_corrente
    else:
        lotto_da_estrarre = None

    lotti_da_processare = [lotto_da_estrarre] if lotto_da_estrarre else nomi_lotti_unici

    for nome_lotto in lotti_da_processare:
        altri_lotti = [l for l in nomi_lotti_unici if l != nome_lotto]
        dati_pdf["lotti"].append(
            _estrai_lotto_per_lotto(testo, nome_lotto, altri_lotti)
        )

    # — CIG per lotto dalla TESTATA ("Lotto 1 A023816D71: Pianosinatico...",
    # es. sgombero neve Abetone CIG A023816D71, 5 lotti): mappa numero->CIG riversata nei
    # lotti, per l'aggancio per contenuto di seleziona_lotto_per_cig.
    for num, cig in re.findall(r'Lotto\s+([A-Z0-9]+)\s+([A-Z0-9]{10})\b', testo):
        for lotto in dati_pdf["lotti"]:
            if (lotto["nome_lotto"].upper() == f"LOTTO {num}".upper()
                    and lotto.get("cig_lotto", "Non presente") == "Non presente"):
                lotto["cig_lotto"] = cig

    # Invitati non elencati ma invio dichiarato: qui la dichiarazione compare
    # una volta sola per l'intero verbale (dopo gli elenchi dei manifestanti di
    # tutti i lotti), quindi vale per ogni lotto.
    if dichiara_invio_invito(testo):
        _travasa_manifestanti_nei_lotti(dati_pdf)


# ── Estrattori formato PER_LOTTO_SUB ─────────────────────────────────────────

def _estrai_lotto_per_lotto_sub(testo, num_lotto, altri, testo_aggiud_g):
    """
    Estrae dati per un singolo lotto nel formato 'per_lotto_sub'.
    Restituisce il dict lotto completo.
    """
    lotto = {
        "nome_lotto": f"Lotto {num_lotto}",
        "cig_lotto": "Non presente",
        "deserto": False,
        "num_manifestanti": "Non presente",
        "manifestanti": [],
        "num_invitati": "Non presente",
        "invitati": [],
        "num_offerte_ricevute": "Non presente",
        "offerte_ricevute": [],
        "num_offerte_ammesse": "Non presente",
        "offerte_ammesse": [],
        "num_offerte_escluse": "Non presente",
        "aggiudicatario_pdf": "Non presente",
        "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
        "ribasso": "Non presente",
        "valore_offerta": "Non presente"
    }

    # Pattern per trovare sub-header "Lotto N" — gestisce anche l'artefatto
    # PDF dove "Lotto 3" è concatenato con "1." → "Lotto 31." (senza newline).
    _pat_lotto_hdr = (
        r'(?:^|\n)\s*Lotto\s+' + re.escape(num_lotto) + r'(?=\s*(?:\n|\d))'
    )
    pat_fine_altri = (
        r'(?:^|\n)\s*Lotto\s+(?:' + '|'.join(re.escape(l) for l in altri)
        + r')(?=\s*(?:\n|\d))'
        if altri else None
    )

    def _estrai_sezione_lotto(pat_inizio_sezione, pat_fine_sezione_list):
        """Estrae il testo tra l'header della sezione e l'header Lotto N."""
        m_sez = re.search(pat_inizio_sezione, testo, re.IGNORECASE)
        if not m_sez:
            return ""
        pos_sez = m_sez.end()
        m_lotto = re.search(_pat_lotto_hdr, testo[pos_sez:], re.MULTILINE)
        if not m_lotto:
            return ""
        pos_lotto = pos_sez + m_lotto.end()
        # Salta eventuale cifra staccata (artefatto "Lotto 31." → skip "1")
        skip = re.match(r'\d+\.', testo[pos_lotto:])
        if skip:
            pos_lotto += skip.end()
        candidates = []
        if pat_fine_altri:
            mf = re.search(pat_fine_altri, testo[pos_lotto:], re.MULTILINE)
            if mf:
                candidates.append(mf.start())
        for p in pat_fine_sezione_list:
            mf2 = re.search(p, testo[pos_lotto:], re.IGNORECASE)
            if mf2:
                candidates.append(mf2.start())
        fine = min(candidates) if candidates else len(testo) - pos_lotto
        return testo[pos_lotto: pos_lotto + fine]

    # — Manifestanti —
    txt_m = _estrai_sezione_lotto(
        r'Numero\s+(?:di\s+)?(?:operatori\s+)?(?:economici\s+)?manifestanti[:\s]*',
        [r'Numero\s+operatori\s+economici\s+invitati', r'Data\s+di\s+spedizione']
    )
    if txt_m:
        # [.)]? — il punto/parentesi dopo il numero è OPZIONALE: nel primo PDF
        # reale di questo formato (Monsummano/Pescia, CIG B1DE6AB698, 2 lotti) le righe sono
        # "1 SICUREZZA E AMBIENTE SPA manifestazione di interesse del ...", senza punto;
        nomi_m = re.findall(
            r'^\s*\d+[.)]?\s+(.+?)\s+[Mm]anifestazione\s+(?:di\s+)?(?:interesse\s+)?del',
            txt_m, re.MULTILINE
        )
        if nomi_m:
            lotto["manifestanti"] = [{"nome": _pulisci_nome(n.strip()), "piva": "Non presente", "cf": "Non presente"}
                                     for n in nomi_m]
            lotto["num_manifestanti"] = str(len(nomi_m))

    # — Invitati —
    txt_i = _estrai_sezione_lotto(
        r'Numero\s+(?:di\s+)?(?:operatori\s+|OO\.?\s*EE\.?\s+)(?:economici\s+)?(?:invitati|selezionati|estratti\s+a\s+sorte)[:\s]*',
        [r'Data\s+di\s+spedizione', r'Numero\s+offerte']
    )
    if txt_i:
        nomi_i = re.findall(r'^\d+\.\s+(.+?)\s*$', txt_i, re.MULTILINE)
        if nomi_i:
            lotto["invitati"] = [
                {"nome": _pulisci_nome(n.strip()), "piva": "Non presente", "cf": "Non presente"}
                for n in nomi_i if len(n.strip()) > 2
            ]
            lotto["num_invitati"] = str(len(lotto["invitati"]))

    # — Offerte —
    txt_o = _estrai_sezione_lotto(
        r'Numero\s+offerte\s+ricevute[:\s]*',
        [r'Numero\s+offerte\s+ammesse', r'[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario']
    )
    if txt_o:
        # [.)]? opzionale: come per i manifestanti (righe "1 NOME offerta del ...")
        nomi_o = re.findall(r'^\s*\d+[.)]?\s+(.+?)\s+[Oo]fferta\s+del', txt_o, re.MULTILINE)
        if not nomi_o:
            # fallback: riga "Lotto N1. NOME offerta del" (artefatto PDF)
            nomi_o = re.findall(r'Lotto\s+\d+\d+\.\s+(.+?)\s+[Oo]fferta\s+del', txt_o)
        if nomi_o:
            lotto["offerte_ricevute"] = [_pulisci_nome(n.strip()) for n in nomi_o]
            lotto["num_offerte_ricevute"] = str(len(nomi_o))

    # — Aggiudicatario —
    m_aggiud_lotto = re.search(
        r'(?:^|\n)\s*Lotto\s+' + re.escape(num_lotto)
        + r'\s*\n([\s\S]+?)(?=\n\s*Lotto\s+\d+|\n\s*[Ss]ubappalto|\n\s*[Dd]ata\s+di\s+decisione|$)',
        testo_aggiud_g, re.IGNORECASE
    )
    if m_aggiud_lotto:
        riga = m_aggiud_lotto.group(1).replace('\n', ' ').strip()
        mp = re.search(r'P\.?IVA[-:\s]*(\d{11})', riga, re.IGNORECASE)
        if mp:
            lotto["aggiudicatario_piva"] = mp.group(1)
        mn = re.search(
            r'^(.+?)(?:\s+P\.?IVA|\s+[Vv]ia(?:le)?\s|\s+[Pp]iazza\s|\s+[Cc]orso\s)',
            riga, re.IGNORECASE
        )
        if mn:
            lotto["aggiudicatario_pdf"] = _pulisci_nome(mn.group(1), taglia_indirizzi=True)
        # Ribasso ([\s:]* gestisce typo "20,11: %")
        mr = re.search(
            r'[Rr]ibasso[^:]*:\s*([\d,\.]+)[\s:]*%',
            testo_aggiud_g[m_aggiud_lotto.start(): m_aggiud_lotto.start() + 500]
        )
        if mr:
            lotto["ribasso"] = f"{mr.group(1)}%"
        mv = re.search(
            r'[Vv]alore[^:€\n]*:?\s*(?:€|Euro)?\s*([\d\.,]+)',
            testo_aggiud_g[m_aggiud_lotto.start(): m_aggiud_lotto.start() + 500], re.IGNORECASE
        )
        if mv:
            lotto["valore_offerta"] = f"€ {mv.group(1)}"

    return lotto


def _estrai_formato_per_lotto_sub(testo, dati_pdf, lotto_corrente, indice_lotto):
    """
    Gestisce l'estrazione completa per il formato 'per_lotto_sub'.
    Popola dati_pdf.
    """
    nomi_lotti_raw = re.findall(r'(?:^|\n)\s*Lotto\s+(\d+)\s*\n', testo)
    nomi_lotti_unici = list(dict.fromkeys(nomi_lotti_raw))

    m_aggiud_g = re.search(
        r'[Nn]ome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[\s\S]{0,3000}', testo
    )
    testo_aggiud_g = m_aggiud_g.group(0) if m_aggiud_g else ""

    for num_lotto in nomi_lotti_unici:
        altri = [l for l in nomi_lotti_unici if l != num_lotto]
        dati_pdf["lotti"].append(
            _estrai_lotto_per_lotto_sub(testo, num_lotto, altri, testo_aggiud_g)
        )

    # — CIG per lotto dichiarati in TESTATA ("CIG: Lotto 1 B1DE6AB698 / Lotto 2
    # B1DE6AA5C5", es. Monsummano/Pescia CIG B1DE6AB698, 2 lotti): la mappa numero->CIG viene
    # riversata nei lotti, cosi' seleziona_lotto_per_cig puo' agganciare per
    # contenuto anche in questo formato.
    for num, cig in re.findall(r'Lotto\s+(\d+)\s+([A-Z0-9]{10})\b', testo):
        for lotto in dati_pdf["lotti"]:
            if lotto["nome_lotto"] == f"Lotto {num}" and lotto.get("cig_lotto", "Non presente") == "Non presente":
                lotto["cig_lotto"] = cig

    # Seconda forma di testata: "- LOTTO N: TITOLO PROGETTO CIG:codice" (il CIG
    # e' in coda alla riga del lotto, dopo il titolo, con etichetta "CIG:",
    # es. CIG 91949264F9, 3 lotti-2 centri estivi). Il titolo tra numero e CIG puo' essere lungo,
    # quindi si aggancia il primo codice di 10 alfanum dopo "LOTTO N" fino a fine
    # riga, senza scavalcare nella riga del lotto successivo.
    for num, cig in re.findall(r'LOTTO\s+(\d+)\s*:[^\n]*?CIG[:\s]*([A-Z0-9]{10})\b', testo, re.IGNORECASE):
        for lotto in dati_pdf["lotti"]:
            if lotto["nome_lotto"] == f"Lotto {num}" and lotto.get("cig_lotto", "Non presente") == "Non presente":
                lotto["cig_lotto"] = cig

    # — Fallback CONDIVISI (es. CIG B1DE6AB698, 2 lotti: un solo aggiudicatario per entrambi
    # i lotti, ribasso unico, valori spartiti per lotto). L'embrione gestiva
    # solo blocchi aggiudicatario per-lotto; se un lotto e' rimasto senza,
    # si estrae il blocco condiviso con l'helper standard e lo si copia.
    lotti_scoperti = [l for l in dati_pdf["lotti"] if l["aggiudicatario_pdf"] == "Non presente"]
    if lotti_scoperti and testo_aggiud_g:
        matches = _estrai_aggiudicatario_std(testo_aggiud_g)
        if matches:
            nomi, pive, visti = [], [], set()
            for nome_completo, piva in matches:
                piva = piva.strip()
                if piva not in visti:
                    visti.add(piva)
                    nomi.append(_pulisci_nome(nome_completo, taglia_indirizzi=True))
                    pive.append(piva)
            agg = ", ".join(nomi)
            piva_agg = ", ".join(p for p in pive if p) or "Non presente"
            for lotto in lotti_scoperti:
                lotto["aggiudicatario_pdf"] = agg
                lotto["aggiudicatario_piva"] = piva_agg
        mr = re.search(r'[Rr]ibasso[\s\S]{0,30}?([\d,\.]+)\s*%', testo_aggiud_g)
        if mr:
            for lotto in lotti_scoperti:
                if lotto["ribasso"] == "Non presente":
                    lotto["ribasso"] = f"{mr.group(1)}%"

    # — Valore dell'offerta PER LOTTO ("Valore dell'offerta: Lotto 1 Euro
    # 39.600,00 / Lotto 2 Euro 9.485,85"): la coppia lotto->importo vince sul
    # valore eventualmente gia' estratto dal blocco per-lotto
    m_val = re.search(r"Valore\s+dell[\'\u2019]offerta[\s\S]{0,200}", testo, re.IGNORECASE)
    if m_val:
        for num, importo in re.findall(r'Lotto\s+(\d+)\s*(?:\u20ac|Euro)\s*([\d\.,]+)', m_val.group(0)):
            for lotto in dati_pdf["lotti"]:
                if lotto["nome_lotto"] == f"Lotto {num}":
                    lotto["valore_offerta"] = f"\u20ac {importo.rstrip(',').strip()}"

    # Invitati non elencati ma invio dichiarato: come nel formato 'per_lotto',
    # la dichiarazione compare una volta per l'intero verbale e vale per tutti
    # i lotti (es. CIG B1DE6AB698, ripristino sicurezza Monsummano/Pescia).
    if dichiara_invio_invito(testo):
        _travasa_manifestanti_nei_lotti(dati_pdf)


# ── Funzioni pubbliche ────────────────────────────────────────────────────────

def estrai_link_pdf_esito(url_bando, BASE_URL="https://www.provincia.pistoia.it"):
    """
    Entra nella pagina del bando e cerca tutti i link ai PDF dell'esito.
    Restituisce una lista di URL dei PDF trovati (vuota se nessuno).
    """
    try:
        from bs4 import BeautifulSoup
        risposta = requests.get(url_bando, timeout=10)
        if risposta.status_code != 200:
            return []

        soup = BeautifulSoup(risposta.text, 'html.parser')
        pdf_trovati = []
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            titolo = link.get('title', '').lower()
            testo_link = link.get_text(strip=True).lower()
            if '/system/files/' in href and href.lower().endswith('.pdf'):
                if (re.search(r'\besito\b', titolo)
                        or re.search(r'\besito\b', testo_link)
                        or 'aggiudicato' in testo_link):
                    url_completo = href if href.startswith('http') else f"{BASE_URL}{href}"
                    if url_completo not in pdf_trovati:
                        pdf_trovati.append(url_completo)
        # Con più PDF, precedenza a quelli il cui NOME FILE contiene "esito"
        # (es. determina DET_DETE_548_2023.pdf + esito.pdf: quello giusto è esito.pdf).
        # sort stabile: a parità di priorità conserva l'ordine della pagina, quindi
        # i bandi con un PDF per lotto (CIG 9046558FD0, CIG 9323641FF0, ...) non cambiano ordine.
        if len(pdf_trovati) > 1:
            pdf_trovati.sort(key=lambda u: 0 if 'esito' in u.rsplit('/', 1)[-1].lower() else 1)
        return pdf_trovati

    except Exception as e:
        log(f"[-] Errore ricerca PDF nel bando {url_bando}: {e}")
        return []


# ── Estrattore formato MULTI_LOTTO_STD (CIG B2D396AD9F, 8 lotti) ──────────────────────────

def _estrai_formato_multi_lotto_std(testo, dati_pdf):
    """
    Un unico PDF con più sezioni auto-contenute "Lotto N – Titolo" (es. derrate
    Chiesina Uzzanese, CIG B2D396AD9F, 8 lotti): ogni sezione dichiara il PROPRIO CIG inline
    ("CIG B2D396AD9F", senza due punti) e contiene i propri manifestanti e
    offerte in righe "(ID: NNNN) NOME manifestazione di interesse/offerta del
    data ora", le ammesse "N c.s.", l'aggiudicatario in formato standard oppure
    la dicitura "Lotto dichiarato deserto".

    Il testo viene affettato sulle intestazioni di lotto e ogni ritaglio è
    processato in autonomia, riusando gli helper del formato standard
    (_estrai_aggiudicatario_std, _pulisci_nome, pattern ribasso/valore) — i
    metodi mono-lotto NON vengono toccati, solo richiamati sui ritagli.

    Estrae SEMPRE tutti i lotti: la scelta del lotto giusto per un CIG spetta
    a seleziona_lotto_per_cig (aggancio per contenuto via "cig_lotto").
    """
    intestazioni = list(re.finditer(r'(?im)^\s*(Lotto\s+\d+\s*[–-][^\n]*)', testo))
    lotti_con_invito = set()   # indici dei lotti che dichiarano l'invio degli inviti
    for i, h in enumerate(intestazioni):
        inizio = h.end()
        fine = intestazioni[i + 1].start() if i + 1 < len(intestazioni) else len(testo)
        sez = testo[inizio:fine]

        lotto = {
            "nome_lotto": re.sub(r'\s+', ' ', h.group(1)).strip(),
            "cig_lotto": "Non presente",
            "deserto": False,
            "num_manifestanti": "Non presente",
            "manifestanti": [],
            "num_invitati": "Non presente",
            "invitati": [],
            "num_offerte_ricevute": "Non presente",
            "offerte_ricevute": [],
            "num_offerte_ammesse": "Non presente",
            "offerte_ammesse": [],
            "num_offerte_escluse": "Non presente",
            "aggiudicatario_pdf": "Non presente",
            "aggiudicatario_piva": "Non presente",
        "aggiudicatario_cf": "Non presente",
            "ribasso": "Non presente",
            "valore_offerta": "Non presente"
        }

        m = re.search(r'\bCIG\b[.:\s]*([A-Z0-9]{10})\b', sez)
        if m:
            lotto["cig_lotto"] = m.group(1)

        if re.search(r'Lotto\s+dichiarato\s+deserto', sez, re.IGNORECASE):
            lotto["deserto"] = True
            # convenzione già usata dal formato per_lotto: l'esito compare
            # nel campo aggiudicatario, così arriva anche in Excel
            lotto["aggiudicatario_pdf"] = "Deserto"

        m = re.search(r'Numero\s+di\s+operatori\s+manifestanti[:\s]*(\d+)', sez, re.IGNORECASE)
        if m:
            lotto["num_manifestanti"] = m.group(1)
        # righe "(ID: NNNN) NOME manifestazione di interesse del ...": "
        righe_manif = re.findall(
            r'\(?\s*ID\s*[:\s]\s*\d+\s*\)\s*(.+?)\s+manifestazione\s+di\s+interesse\s+del\b',
            sez, re.IGNORECASE
        )
        lotto["manifestanti"] = [{"nome": _pulisci_nome(r), "piva": "Non presente", "cf": "Non presente"} for r in righe_manif]
        if lotto["num_manifestanti"] == "Non presente" and lotto["manifestanti"]:
            lotto["num_manifestanti"] = str(len(lotto["manifestanti"]))

        # Invitati non elencati ma invio dichiarato: in questo formato la
        # dichiarazione compare DENTRO il blocco del singolo lotto, quindi si
        # verifica lotto per lotto invece che sull'intero documento (es. CIG
        # B2D396AD9F, derrate Chiesina Uzzanese: 8 lotti, ciascuno con la
        # propria data di spedizione).
        if dichiara_invio_invito(sez):
            lotti_con_invito.add(len(dati_pdf["lotti"]))

        m = re.search(r'Numero\s+offerte\s+ricevute[:\s]*(\d+)', sez, re.IGNORECASE)
        if m:
            lotto["num_offerte_ricevute"] = m.group(1)
        righe_off = re.findall(
            r'\(?\s*ID\s*[:\s]\s*\d+\s*\)\s*(.+?)\s+offerta\s+del\b',
            sez, re.IGNORECASE
        )
        lotto["offerte_ricevute"] = [_pulisci_nome(r) for r in righe_off]
        if lotto["num_offerte_ricevute"] == "Non presente" and lotto["offerte_ricevute"]:
            lotto["num_offerte_ricevute"] = str(len(lotto["offerte_ricevute"]))

        # ammesse: solo il conteggio ("N c.s.", il travaso della lista è tra i
        # lavori in sospeso, come per il formato standard)
        m = re.search(r'Numero\s+offerte\s+ammesse[^\n]{0,50}?(\d+)', sez, re.IGNORECASE)
        if m:
            lotto["num_offerte_ammesse"] = m.group(1)

        # aggiudicatario / P.IVA: helper standard sul ritaglio
        matches = _estrai_aggiudicatario_std(sez)
        if matches:
            nomi, pive, visti_piva = [], [], set()
            for nome_completo, piva in matches:
                piva = piva.strip()
                if piva not in visti_piva:
                    visti_piva.add(piva)
                    nomi.append(_pulisci_nome(nome_completo, taglia_indirizzi=True))
                    pive.append(piva)
            lotto["aggiudicatario_pdf"] = ", ".join(nomi)
            pive_valide = [p for p in pive if p]
            if pive_valide:
                lotto["aggiudicatario_piva"] = ", ".join(pive_valide)

        m = re.search(r'(?:Ribasso[\s\S]{0,30}?)([\d,\.]+)\s*%', sez, re.IGNORECASE)
        if m:
            lotto["ribasso"] = f"{m.group(1)}%"
        m = re.search(
            r"(?:Valore dell[\'\u2019]offerta[\s\S]{0,60}?|Importo di aggiudicazione[\s\S]{0,40}?)"
            r"(?:\u20ac|Euro)\s*([\d\.,]+)",
            sez, re.IGNORECASE
        )
        if m:
            lotto["valore_offerta"] = f"\u20ac {m.group(1).rstrip(',').strip()}"

        dati_pdf["lotti"].append(lotto)

    # Travaso limitato ai soli lotti che hanno dichiarato l'invio degli inviti
    # (indici raccolti nel ciclo qui sopra): un lotto che tace resta con la
    # lista vuota e l'assenza dichiarata.
    if lotti_con_invito:
        _travasa_manifestanti_nei_lotti(dati_pdf, indici=lotti_con_invito)


def rileva_formato_pdf(testo):
    """
    Rileva il formato del PDF dell'esito.
    - 'per_lotto':     manifestanti/invitati divisi per lotto nel testo
    - 'per_lotto_sub': sezioni comuni con sub-header "Lotto N" dentro ogni sezione
    - 'standard':      formato unificato (singolo o multi-lotto)
    """
    # \s* invece di \s+ per gestire "LOTTO2:" senza spazio
    if re.search(r'LOTTO\s*[A-Z0-9]+[:\s]*manifestanti\s*\d+', testo, re.IGNORECASE):
        return 'per_lotto'
    if re.search(
        r'Numero\s+(?:di\s+)?(?:operatori\s+)?(?:economici\s+)?manifestanti[:\s]*\n'
        r'(?:[\s\S]{0,30}\n)?Lotto\s+\d+\s*\n',
        testo, re.IGNORECASE
    ):
        return 'per_lotto_sub'
    # 'multi_lotto_std': un unico PDF con più sezioni auto-contenute
    # "Lotto N – Titolo", ciascuna col PROPRIO CIG inline e i propri
    # manifestanti/offerte/aggiudicatario (es. derrate Chiesina Uzzanese,
    # CIG B2D396AD9F, 8 lotti, 8 lotti di cui 3 deserti). Richiede ALMENO 2 intestazioni
    # numerate col trattino E almeno 2 CIG nel testo: i mono-lotto non hanno
    # né le une né gli altri. Il check sta in coda apposta:
    # non tocca i formati esistenti, aggiunge solo un'uscita nuova.
    intestazioni_ml = re.findall(r'(?im)^\s*Lotto\s+\d+\s*[–-]', testo)
    if len(intestazioni_ml) >= 2 and len(re.findall(r'\bCIG\b[.:\s]*[A-Z0-9]{10}\b', testo)) >= 2:
        return 'multi_lotto_std'
    return 'standard'


def estrai_dati_pdf_esito(url_pdf, lotto_corrente=None, indice_lotto=None):
    """
    Scarica il PDF dell'esito e ne estrae i dati strutturati.

    Formati gestiti: 'standard', 'per_lotto', 'per_lotto_sub'.
    """
    dati_pdf = {
        "num_operatori_manifestanti": "Non presente",
        "operatori_manifestanti": [],
        "num_operatori_invitati": "Non presente",
        "operatori_invitati": [],
        "cig_pdf": "Non presente",
        "lotti": []
    }

    try:
        risposta = requests.get(url_pdf, timeout=15)
        if risposta.status_code != 200:
            log(f"[-] Impossibile scaricare il PDF: {url_pdf}")
            return dati_pdf

        with pdfplumber.open(io.BytesIO(risposta.content)) as pdf:
            testo = ""
            n_immagini = 0
            for pagina in pdf.pages:
                # FIX: Aggiunto + "\n" per evitare righe incollate a causa del salto pagina
                testo += (pagina.extract_text() or "") + "\n"
                n_immagini += len(pagina.images)

        # PDF scannerizzato (solo immagini, nessun layer di testo): pdfplumber non può
        # estrarre nulla e tutti i campi resterebbero vuoti in silenzio. Lo segnala
        # esplicitamente e si ferma.
        if len(testo.strip()) < 50 and n_immagini > 0:
            log(f"    [!] PDF scannerizzato (immagine, nessun testo estraibile): {url_pdf}")
            log(f"        -> dati non estraibili senza OCR; campi lasciati a 'Non presente'")
            dati_pdf["pdf_scansionato"] = True
            return dati_pdf

        # Normalizza caratteri tipografici Unicode → equivalenti ASCII
        testo = testo.replace('’', "'").replace('‘', "'")
        testo = testo.replace('“', '"').replace('”', '"')

        formato = rileva_formato_pdf(testo)

        # — CIG dichiarato nel PDF —
        # Serve a main.py per agganciare CIG→PDF per CONTENUTO invece che per
        # posizione. Pattern: "CIG: B2E0277731" ma anche "SmartCIG ZEECDCAC17"
        # il \b dopo CIG evita falsi positivi
        # su parole che iniziano per CIG.
        m_cig = re.search(r'\b(?:Smart\s*)?CIG\b[.:\s]*([A-Z0-9]{10})\b', testo)
        if m_cig:
            dati_pdf["cig_pdf"] = m_cig.group(1)
        else:
            # —Caso CIG etichettato per refuso come "CPV" (es. Montale 2019,
            # CIG 7898785B93: "CPV 7898785B93") — un CPV vero e' cifre+trattino
            # (45233141-9): un token di 10 alfanumerici CON almeno una lettera
            # dopo l'etichetta CPV non puo' essere un CPV ed e' il CIG della
            # gara.
            m_cig = re.search(r'\bCPV\b[:\s]*([A-Z0-9]{10})\b', testo)
            if m_cig and re.search(r'[A-Z]', m_cig.group(1)):
                dati_pdf["cig_pdf"] = m_cig.group(1)

        if formato == 'per_lotto':
            _estrai_formato_per_lotto(testo, dati_pdf, lotto_corrente, indice_lotto)
        elif formato == 'per_lotto_sub':
            _estrai_formato_per_lotto_sub(testo, dati_pdf, lotto_corrente, indice_lotto)
        elif formato == 'multi_lotto_std':
            _estrai_formato_multi_lotto_std(testo, dati_pdf)
        else:
            _estrai_formato_standard(testo, dati_pdf, lotto_corrente, indice_lotto)

        # — INVITATI CONDIVISI PROPAGATI AI LOTTI — Regola trasversale ai
        # formati: se il documento ha piu' lotti e la lista degli invitati e'
        # dichiarata a livello documento (es. Ciclovia del Sole CIG A03589F8C6, 3 lotti, dove
        # il PDF non dice chi fu invitato a quale lotto), ogni lotto senza
        # invitati propri la eredita per intero: gli invitati della gara si
        # considerano invitati di ciascun lotto. I mono-lotto (1 solo lotto)
        # non vengono toccati: leggono gia' il livello documento.
        # ECCEZIONE: quando i manifestanti sono dichiarati PER LOTTO
        # ("manifestazioni interesse ricevute Lotto n.1: 1" e
        # "Lotto n.2: 3"), la lista a livello documento non e' una lista
        # autonoma ma la semplice FUSIONE delle liste dei lotti (1+3=4 nomi,
        # con "ASD Ponte 2000" ripetuto perche' presente in entrambi). Se in
        # piu' gli invitati derivano da "Come sopra", propagarla darebbe a
        # OGNI lotto i manifestanti di TUTTI i lotti. In quel caso si lascia
        # decidere alla regola "invitati = manifestanti" qui sotto, che lavora
        # sul singolo lotto.
        _somma_manif_lotti = sum(len(_l.get("manifestanti", []))
                                 for _l in dati_pdf["lotti"])
        _glob_e_fusione = (_somma_manif_lotti > 0
                           and len(dati_pdf["operatori_manifestanti"]) == _somma_manif_lotti
                           and any(_l.get("manifestanti") for _l in dati_pdf["lotti"]))
        _inv_da_manif_globali = (
            dati_pdf["operatori_invitati"]
            and [_o["nome"] for _o in dati_pdf["operatori_invitati"]]
            == [_o["nome"] for _o in dati_pdf["operatori_manifestanti"]]
        )
        if (len(dati_pdf["lotti"]) >= 2
                and dati_pdf["num_operatori_invitati"] != "Non presente"
                and not (_glob_e_fusione and _inv_da_manif_globali)):
            for _lotto in dati_pdf["lotti"]:
                if (_lotto.get("num_invitati", "Non presente") == "Non presente"
                        and not _lotto.get("invitati")):
                    _lotto["num_invitati"] = dati_pdf["num_operatori_invitati"]
                    _lotto["invitati"] = list(dati_pdf["operatori_invitati"])

        # — INVITATI = MANIFESTANTI quando i conteggi coincidono — Se un lotto
        # dichiara il NUMERO di invitati ma non i nomi (es. sgombero neve
        # Abetone, gara CIG A023816D71: "LOTTO 1: invitati 2") e quel numero e' UGUALE ai
        # manifestanti elencati del lotto, gli invitati sono per forza loro:
        # la lista si riempie con la copia dei manifestanti.
        for _lotto in dati_pdf["lotti"]:
            _n_inv = _lotto.get("num_invitati", "Non presente")
            if (_n_inv != "Non presente" and not _lotto.get("invitati")
                    and _lotto.get("manifestanti")
                    and _n_inv.strip().isdigit()
                    and int(_n_inv) == len(_lotto["manifestanti"])):
                _lotto["invitati"] = list(_lotto["manifestanti"])

        # — "COME SOPRA" CON MANIFESTANTI PER LOTTO — Quando gli invitati sono
        # dichiarati solo con la formula "Come sopra" e i manifestanti sono
        # elencati per lotto, il rimando va sciolto DENTRO
        # ciascun lotto: gli invitati del Lotto N sono i manifestanti del
        # Lotto N, non la fusione delle
        # due liste. Il totale dichiarato a livello documento resta
        # quello del PDF e non viene "corretto": e' un dato della gara, non
        # del singolo lotto, e i due lotti condividono un operatore.
        if _glob_e_fusione and _inv_da_manif_globali:
            for _lotto in dati_pdf["lotti"]:
                if not _lotto.get("invitati") and _lotto.get("manifestanti"):
                    _lotto["invitati"] = list(_lotto["manifestanti"])
                    _lotto["num_invitati"] = str(len(_lotto["manifestanti"]))

        # — C.F. DELL'AGGIUDICATARIO — Ricavato una volta sola qui, invece che
        # nei dieci punti che assegnano la P.IVA. Si cerca nel testo la riga che
        # porta la P.IVA gia' estratta e da quella si legge il codice fiscale,
        # quando il PDF lo dichiara a parte (CIG 9435123DCC: "P.IVA -11991500015,
        # C.F. 00799960158"). Con l'etichetta unica "CF/P.IVA" il codice e' uno
        # solo e vale per entrambi i campi.
        # La riga va cercata DENTRO la sezione dell'aggiudicatario, non in
        # tutto il documento: la stessa P.IVA compare spesso anche nell'elenco
        # degli invitati, che viene PRIMA (CIG B355976FEB: "37. MI.CO.SRL P.IVA:
        # 01418060859" riga 140, l'aggiudicatario a riga 207). Prendendo la
        # prima occorrenza si leggeva la riga dell'invitato, priva del C.F., e
        # il campo finiva per ripiegare sulla P.IVA — perdendo il codice
        # fiscale che il PDF invece dichiara ("C.F. 01965240789 e P.I.
        # 01418060859").
        _m_sez = re.search(r"Nome\s+e[d]?\s+indirizzo\s+dell.aggiudicatario[\s\S]*", testo,
                           re.IGNORECASE)
        _testo_agg = _m_sez.group(0) if _m_sez else testo
        for _lotto in dati_pdf["lotti"]:
            if _lotto.get("aggiudicatario_cf", "Non presente") != "Non presente":
                continue
            _pv = _lotto.get("aggiudicatario_piva", "Non presente")
            _riga_agg = ""
            if _pv and _pv != "Non presente":
                _primo = _pv.split(",")[0].strip()
                for _r in _testo_agg.split('\n'):
                    if _primo and _primo in _r:
                        _riga_agg = _r
                        break
            _lotto["aggiudicatario_cf"] = _cf_da_riga(_riga_agg, _pv)

    except Exception as e:
        log(f"[-] Errore estrazione dati PDF {url_pdf}: {e}")

    return dati_pdf


def dichiara_invio_invito(testo):
    """
    True se il testo dichiara la SPEDIZIONE delle lettere d'invito.

    E' la condizione che autorizza il travaso dei manifestanti fra gli
    invitati quando il verbale non riporta l'elenco.
    Si aggancia a una dichiarazione esplicita e non alla sola assenza della
    sezione, cosi' un verbale che tace sull'invito non viene toccato.

    Etichette osservate: "Data di spedizione della Lettera d'invito:" e
     "Data spedizione invito:", "di" en"della Lettera" sono facoltativi.
    """
    return bool(re.search(
        r'Data\s+(?:di\s+)?spedizione\s+(?:della\s+)?'
        r'(?:lettera\s+)?(?:d\s*\'\s*)?invito', testo, re.IGNORECASE))


def _travasa_manifestanti_nei_lotti(dati_pdf, indici=None):
    """
    Copia i manifestanti fra gli invitati nei lotti che ne sono privi.

    Usata dai formati multi-lotto quando il verbale dichiara l'invio degli
    inviti ma non elenca gli invitati lotto per lotto. Tocca solo i lotti con
    manifestanti e senza invitati; gli altri restano invariati. I lotti
    dichiarati DESERTI vengono comunque travasati: deserto significa che
    nessuno ha presentato offerta, non che nessuno sia stato invitato.

    Il conteggio degli invitati eventualmente dichiarato nel verbale NON viene
    usato come condizione: nei documenti reali quei numeri sono spesso errati,
    quindi un valore discordante non e' indizio affidabile di un invito
    ristretto a un sottoinsieme dei manifestanti.
    """
    for i, lotto in enumerate(dati_pdf.get("lotti", [])):
        if indici is not None and i not in indici:
            continue
        if lotto.get("invitati") or not lotto.get("manifestanti"):
            continue
        lotto["invitati"] = [
            {"nome": m["nome"], "piva": m.get("piva", "Non presente"),
             "cf": m.get("cf", "Non presente")}
            for m in lotto["manifestanti"]
        ]
        if lotto.get("num_invitati", "Non presente") == "Non presente":
            lotto["num_invitati"] = str(len(lotto["invitati"]))


def normalizza_piva(codice):
    """
    Ripulisce una P.IVA o un codice fiscale per il confronto: toglie spazi,
    punti, trattini e l'eventuale prefisso nazionale "IT", e porta a maiuscolo.
    Cosi' "IT 03382330482", "03382330482" e "IT-03382330482" si equivalgono.
    """
    if not codice or codice == "Non presente":
        return ""
    pulito = re.sub(r'[\s.\-/]', '', str(codice)).upper()
    if pulito.startswith('IT') and len(pulito) > 2:
        pulito = pulito[2:]
    return pulito


def invitato_con_piva(dati_pdf, piva_cercata):
    """
    Cerca un operatore fra gli INVITATI del bando e restituisce il suo dict
    ({"nome","piva","cf"}) se lo trova, altrimenti None.

    Il confronto guarda SIA la P.IVA SIA il codice fiscale dell'operatore: i
    PDF a volte riportano l'uno, a volte l'altro, e per i professionisti
    persona fisica il C.F. e' l'unico identificativo disponibile. Cercare un
    solo campo farebbe perdere corrispondenze legittime.

    Vengono considerati gli invitati sia a livello di GARA sia quelli per
    LOTTO, perche' nei multi-lotto le liste stanno solo dentro i lotti.

    Utilizzato per il filtro con P.IVA/CF
    """
    cercata = normalizza_piva(piva_cercata)
    if not cercata:
        return None

    candidati = list(dati_pdf.get("operatori_invitati") or [])
    for lotto in dati_pdf.get("lotti") or []:
        candidati.extend(lotto.get("invitati") or [])

    for op in candidati:
        if not isinstance(op, dict):
            continue
        if cercata in (normalizza_piva(op.get("piva")), normalizza_piva(op.get("cf"))):
            return op
    return None


def cig_compatibile(dichiarato, cercato):
    """
    True se il CIG dichiarato (nel PDF o nel lotto) corrisponde a quello
    cercato. Oltre all'uguaglianza esatta, accetta il caso del CIG di pagina
    TRONCATO (refuso di pagina, 8-9 caratteri invece di 10):
    un codice monco combacia se e' PREFISSO del CIG pieno.
    Il troncamento conserva il prefisso; se il refuso fosse un carattere
    mancante in mezzo, il prefisso non combacia e resta il fallback posizionale.

    Refusi di battitura tollerati: un carattere in piu'/in meno, un carattere sostituito, e due
    caratteri ADIACENTI SCAMBIATI ("...D5E" vs "...DE5").
    """
    if not dichiarato or dichiarato == "Non presente" or not cercato:
        return False
    d, c = dichiarato.upper(), cercato.upper()
    if d == c:
        return True
    # Prefisso SIMMETRICO: copre sia il CIG di pagina TRONCATO (8-9,
    # prefisso del CIG pieno del PDF) sia il CIG del PDF con un carattere IN
    # PIU' per refuso (11 char, es. "A01EF539010" CIG A01E792BE2, 2 lotti, di cui il CIG
    # vero di pagina e' prefisso). Il minimo di 8 caratteri comuni esclude
    # collisioni accidentali tra CIG di lotti diversi.
    if min(len(d), len(c)) < 8:
        return False
    if d.startswith(c) or c.startswith(d):
        return True
    # Refuso di UN carattere inserito nel codice piu' lungo (es. dichiarato
    # "A01EF539010" con una E di troppo, cercato "A01F539010"): combaciano se
    # rimuovendo un solo carattere dal piu' lungo si ottiene il piu' corto.
    lungo, corto = (d, c) if len(d) > len(c) else (c, d)
    if len(lungo) - len(corto) == 1:
        for i in range(len(lungo)):
            if lungo[:i] + lungo[i+1:] == corto:
                return True
    # Refuso di UN carattere SOSTITUITO, stessa lunghezza
    if len(d) == len(c) and sum(1 for x, y in zip(d, c) if x != y) == 1:
        return True
    # Refuso di DUE caratteri ADIACENTI SCAMBIATI, stessa lunghezza (es. pagina
    # "9376278D5E" vs PDF "9376278DE5").
    # E' l'errore di battitura piu' comune dopo la sostituzione,
    # ma in distanza di edit vale 2, quindi i rami sopra non lo coprono.
    # Si accetta SOLO la trasposizione vera e propria: le due posizioni devono
    # essere contigue e i caratteri incrociarsi esattamente (d[i]==c[i+1] e
    # d[i+1]==c[i]). Due sostituzioni indipendenti adiacenti NON passano.
    if len(d) == len(c):
        _diff = [i for i, (x, y) in enumerate(zip(d, c)) if x != y]
        if (len(_diff) == 2 and _diff[1] == _diff[0] + 1
                and d[_diff[0]] == c[_diff[1]] and d[_diff[1]] == c[_diff[0]]):
            return True
    return False


def seleziona_pdf_per_cig(lista_pdf, idx, cig_singolo, cache=None, estrai=None):
    """
    Sceglie i dati del PDF giusto per il CIG corrente quando il bando ha
    PIU' PDF (tipicamente un PDF per lotto, es. gara SP17/SP24 CIG B2E0277731/184).

    Definita qui  perche' e' logica di dominio
    riusabile da QUALUNQUE frontend: main.py e interfaccie grafiche.

    Strategia:
      1. Aggancio per CONTENUTO: si estrae ogni PDF (con cache, per non
         scaricare/estrarre due volte) e si cerca quello che dichiara in
         testata il CIG corrente (campo "cig_pdf").
      2. Fallback POSIZIONALE: se nessun PDF dichiara quel CIG, si assume ordine di
         pagina = ordine dei PDF e si prende lista_pdf[idx], come prima.

    Parametri:
      cache  - dict opzionale condiviso tra chiamate della stessa gara
               (evita ri-download iterando su piu' CIG); se None, la dedup
               vale solo all'interno della singola chiamata.
      estrai - iniettabile per i test (default: estrai_dati_pdf_esito).

    Ritorna il dict dati_pdf, o None se idx fuori range e nessun match.
    """
    if estrai is None:
        estrai = estrai_dati_pdf_esito
    if cache is None:
        cache = {}

    def _cached(url):
        """Estrae i dati di un PDF una volta sola, riusando la cache."""
        if url not in cache:
            cache[url] = estrai(url, indice_lotto=None)
        return cache[url]

    for url_pdf in lista_pdf:
        d = _cached(url_pdf)
        if cig_compatibile(d.get("cig_pdf", "Non presente"), cig_singolo):
            return d
    if idx < len(lista_pdf):
        return _cached(lista_pdf[idx])
    return None


def seleziona_lotto_per_cig(dati_pdf, cig_singolo, indice_lotto=None):
    """
    Sceglie il LOTTO giusto per il CIG corrente quando un unico PDF contiene
    più lotti, ognuno col proprio CIG (formato 'multi_lotto_std', es. derrate
    Chiesina Uzzanese CIG B2D396AD9F, 8 lotti). Gemella di seleziona_pdf_per_cig, che invece
    sceglie tra più PDF: qui si sceglie tra i lotti di uno stesso documento.

    Strategia:
      1. Aggancio per CONTENUTO: il lotto il cui campo "cig_lotto" combacia
         col CIG cercato.
      2. Fallback POSIZIONALE: se nessun lotto dichiara quel CIG, si assume
         ordine di pagina = ordine dei lotti nel PDF e si usa indice_lotto.

    Ritorna il dict del lotto, o None se nessun match e indice fuori range.
    """
    lotti = (dati_pdf or {}).get("lotti") or []
    for l in lotti:
        if cig_compatibile(l.get("cig_lotto", "Non presente"), cig_singolo):
            return l
    if indice_lotto is not None and 0 <= indice_lotto < len(lotti):
        return lotti[indice_lotto]
    return None


def costruisci_lista_cig(cig_list_pagina, lista_pdf, cache=None, estrai=None,
                         con_divergenti=False):
    """
    Costruisce la lista dei CIG su cui iterare: LA PAGINA GUIDA, IL PDF
    INTEGRA E CORREGGE, I CODICI INVALIDI SI SCARTANO.

    1. I CIG di PAGINA a 10 caratteri si processano SEMPRE, nel loro ordine:
       la pagina e' la fonte primaria (esiste anche quando il PDF manca, e
       senza PDF e' l'unica via per i dati ANAC).
    2. Un CIG di pagina TRONCATO (8-9 caratteri) viene "promosso" al codice
       pieno dichiarato nel PDF di cui e' prefisso. Se NESSUN PDF lo completa
       (nessun riscontro, PDF muti o assenti) viene SCARTATO e restituito tra
       gli "scartati": un codice monco non esiste in ANAC e ogni chiamata
       brucerebbe tutti i tentativi (15 x 8s) per nulla.
    3. Il PDF INTEGRA: i CIG che dichiara (per-lotto "cig_lotto" o testata
       "cig_pdf") e che la pagina non espone vengono AGGIUNTI in coda.
    4. I CIG di pagina a 10 caratteri non riscontrati in alcun PDF restano in
       lista — ci si fida della pagina — ma finiscono tra i "non riscontrati"
       perche' il chiamante segnali la possibile incoerenza.

    Con lista_pdf vuota non estrae nulla: applica solo il filtro di validita'
    alla lista di pagina. Usa la cache di estrazione del chiamante.

    Un CIG dichiarato dal PDF viene AGGIUNTO solo se puo' corrispondere a un
    lotto scoperto. Con UN SOLO PDF, UN SOLO LOTTO e nessun CIG per-lotto non
    c'e' nulla da aggiungere: un codice diverso da quello di pagina e' una
    DIVERGENZA fra le fonti, non un lotto in piu', e viene scartato (vince la
    pagina, che e' il codice valido per ANAC) e restituito tra i "divergenti".

    Ritorna (lista_cig_effettiva, non_riscontrati, integrati_dal_pdf, scartati).
    Con con_divergenti=True aggiunge in coda la lista dei CIG di PDF scartati
    per divergenza (quinto valore); il default a 4 valori resta compatibile.
    """
    if estrai is None:
        estrai = estrai_dati_pdf_esito
    if cache is None:
        cache = {}

    def _cached(url):
        """Estrae i dati di un PDF una volta sola, riusando la cache."""
        if url not in cache:
            cache[url] = estrai(url, indice_lotto=None)
        return cache[url]

    pagina = list(cig_list_pagina or [])
    cig_da_pdf = []
    for url in (lista_pdf or []):
        d = _cached(url)
        per_lotto = [l.get("cig_lotto") for l in d.get("lotti", [])
                     if l.get("cig_lotto", "Non presente") != "Non presente"]
        if per_lotto:
            cig_da_pdf.extend(per_lotto)
        elif d.get("cig_pdf", "Non presente") != "Non presente":
            cig_da_pdf.append(d["cig_pdf"])
    visti = set()
    cig_da_pdf = [c for c in cig_da_pdf if not (c in visti or visti.add(c))]

    effettiva, non_riscontrati, integrati, scartati = [], [], [], []
    coperti = set()
    for c in pagina:
        pieno = next((cp for cp in cig_da_pdf if cig_compatibile(cp, c)), None)
        if pieno is not None:
            # Quale dei due codici finisce in lista? LA PAGINA E' IL RIFERIMENTO
            # CORRETTO: se il CIG di pagina e' gia' completo (10 caratteri) si
            # tiene QUELLO, anche quando il PDF ne dichiara uno diverso per
            # refuso
            # Il codice del PDF si usa solo per PROMUOVERE un CIG di pagina
            # TRONCATO (8-9 caratteri), dove il PDF aggiunge informazione.
            effettiva.append(c if len(c) == 10 else pieno)
            coperti.add(pieno)
        elif len(c) == 10:
            effettiva.append(c)              # valido: la pagina guida
            if cig_da_pdf:
                non_riscontrati.append(c)    # ...ma nessun PDF lo conferma
        else:
            scartati.append(c)               # monco e non completabile: fuori
    # — IL PDF NON INTEGRA SE NON C'E' NULLA DA AGGIUNGERE — Un CIG dichiarato
    # dal PDF entra in lista solo se puo' corrispondere a un LOTTO che la
    # pagina non copre. Quando c'e' UN SOLO PDF, con UN SOLO LOTTO e senza CIG
    # per-lotto, il documento descrive una gara sola: se il suo CIG di testata
    # e' diverso da quello di pagina non e' un lotto in piu', e' una
    # DIVERGENZA fra le due fonti
    # LA PAGINA E' IL RIFERIMENTO: e' la fonte primaria ed e' il codice valido
    # per ANAC, quindi il CIG del PDF si scarta e resta segnalato al chiamante
    # tra i "divergenti", perche' l'incoerenza non vada perduta.
    divergenti = []
    _un_solo_pdf_mono_lotto = (
        len(lista_pdf or []) == 1
        and len(_cached(lista_pdf[0]).get("lotti", [])) <= 1
        and not any(l.get("cig_lotto", "Non presente") != "Non presente"
                    for l in _cached(lista_pdf[0]).get("lotti", []))
    )
    if _un_solo_pdf_mono_lotto and pagina:
        divergenti = [cp for cp in cig_da_pdf if cp not in coperti]
    else:
        integrati.extend(cp for cp in cig_da_pdf if cp not in coperti)
    effettiva += integrati

    visti = set()
    effettiva = [c for c in effettiva if not (c in visti or visti.add(c))]
    if con_divergenti:
        return effettiva, non_riscontrati, integrati, scartati, divergenti
    return effettiva, non_riscontrati, integrati, scartati


def risolvi_cig(cig_pagina, dati_pdf):
    """
    Ritorna il CIG effettivo di una gara componendo le due fonti disponibili:

      1. il CIG rilevato nella PAGINA web (fonte primaria), se presente;
      2. altrimenti il CIG dichiarato in testata del PDF (campo "cig_pdf"
         estratto da estrai_dati_pdf_esito), se il PDF lo riporta;
      3. altrimenti "Non trovato".

    Parametri:
      cig_pagina - CIG dalla pagina web, o None/""/"Non trovato" se assente
      dati_pdf   - dict prodotto da estrai_dati_pdf_esito (o None/{})
    """
    if cig_pagina and cig_pagina != "Non trovato":
        # CIG di pagina TRONCATO (refuso, 8-9 caratteri): se il PDF dichiara il
        # CIG pieno di cui quello di pagina e' prefisso — in testata (cig_pdf)
        # o in uno dei lotti (cig_lotto) — si "promuove" al codice pieno, che
        # e' quello valido per risultati, Excel e chiamate ANAC.
        if len(cig_pagina) < 10 and dati_pdf:
            candidati = [(dati_pdf.get("cig_pdf") or "Non presente")]
            candidati += [l.get("cig_lotto", "Non presente") for l in dati_pdf.get("lotti", [])]
            for cand in candidati:
                if cand != "Non presente" and len(cand) == 10 and cig_compatibile(cand, cig_pagina):
                    return cand
        return cig_pagina
    cig_pdf = (dati_pdf or {}).get("cig_pdf", "Non presente")
    return cig_pdf if cig_pdf != "Non presente" else "Non trovato"