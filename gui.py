import sys
import threading
import time
import queue
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QProgressBar,
    QFileDialog, QFrame, QSizePolicy, QLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from scraper import (genera_url_con_filtri, estrai_lista_bandi, BASE_URL,
                     estrai_dati_json_anac, scarica_json_anac, estrai_dettagli_bando)
from save_data import salva_in_excel

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
            border: 1.5px solid #1a73e8;  /*Bordo blu*/
        border-radius: 6px;
        padding: 5px 10px;
        min-height: 22px;
        background-color: #ffffff;
    }
    QComboBox:hover {
        border: 1.5px solid #155cb4;   /*Il bordo diventa più scuro al passaggio del mouse*/
    }
    /*Stile della freccina del menu a tendina*/
    QComboBox::drop-down {
        border: none;
        padding-right: 10px;
    }
    /*Stile della lista che si apre quando clicchi sul menu*/
    QComboBox QAbstractItemView {
        border: 1px solid #1a73e8;
        selection-background-color: #1a73e8;        /*Sfondo dell'opzione selezionata (blu)*/
        selection-color: white;                     /*Testo dell'opzione selezionata (bianco)*/
        background-color: #ffffff;
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


class BandiPistoiaApp(QMainWindow):
    def __init__(self):
        super().__init__()      #Chiama l'inizializzazione della classe genitore (QMainWindow)

        self.setWindowTitle("Bandi Provincia di Pistoia")  #Imposta il titolo che appare in alto nella finestra del sistema operativo

        self.resize(760, 720)  #Imposta la dimensione iniziale della finestra all'apertura

        self.setStyleSheet(STILE_APPLICAZIONE)   #Applica alla finestra lo stile CSS definito sopra

        #VARIABILI PER LA GESTIONE DEI THREAD E DEI PROCESSI
        self._interrompi = threading.Event()    # Evento usato come "bandiera": se alzato (set), dice al thread dello scraper di fermarsi

        self._coda = queue.Queue()      # Coda di messaggi (Queue), serve per passare in sicurezza i dati dal thread dello scraper
                                        # (che lavora in background) al thread della GUI (che gestisce la grafica).

        self._ricerca_in_corso = False      #Variabile booleana per sapere se c'è un'elaborazione attiva in questo momento

        #Crea un Timer. Questo timer scatterà a intervalli regolari (es. ogni 100ms)
        #e chiamerà la funzione `_controlla_coda` per vedere se lo scraper ha inviato nuovi messaggi
        self._timer = QTimer()
        self._timer.timeout.connect(self._controlla_coda)

        #COSTRUZIONE LAYOUT PRINCIPALE
        centrale = QWidget()            # Crea il widget (contenitore) invisibile che farà da base per tutto

        # Imposta la politica di Focus: se l'utente clicca sul bianco (sul contenitore), toglie il focus (il cursore lampeggiante) dai campi di testo.
        centrale.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(centrale)

        self._layout = QVBoxLayout(centrale) # Crea un Layout Verticale: impila gli elementi dall'alto verso il basso

        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize) #Definisce spazio minimo per non far schiacciare i componenti
        self._layout.setSpacing(12) #Spazio verticale tra i vari blocchi
        self._layout.setContentsMargins(30, 25, 30, 25)  #Spazio ai bordi

        #IMPLEMENTAZIONE INTERFACCIA
        #Chiama in sequenza le funzioni che creano i singoli pezzi dell'applicazione
        self._crea_intestazione()
        self._aggiungi_separatore()
        self._crea_filtri()
        self._aggiungi_separatore()
        self._crea_sezione_data()
        self._aggiungi_separatore()
        self._crea_sezione_salvataggio()
        self._aggiungi_separatore()
        self._crea_pulsanti()
        self._crea_barra_avanzamento()

        # Aggiunge uno spazio flessibile alla fine per spingere tutto in alto se la finestra è grande
        self._layout.addStretch()

        self.setFocus()     #Assicura che all'avvio nessun campo di testo sia selezionato di default

    def _aggiungi_separatore(self): #Funzione per creare linee orizzontali per separare i blocchi
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        self._layout.addWidget(sep)

    def _crea_intestazione(self): #Costruisce la parte alta dell'interfaccia: Titolo principale e Sottotitolo
        #Crea un "Frame" (una scatola invisibile) che conterrà titolo e sottotitolo
        frame = QWidget()

        #Assegna a questa scatola un layout verticale (gli elementi saranno impilati)
        layout = QVBoxLayout(frame)
        layout.setSpacing(4)            #Spazio di 4 pixel tra titolo e sottotitolo
        layout.setContentsMargins(0, 0, 0, 5)           #Margine inferiore di 5 pixel

        #Testo per il titolo
        titolo = QLabel("Bandi Provincia di Pistoia")
        # Questo sovrascrive il CSS globale solo per questa etichetta (lo fa blu, grande 28px e grassetto)
        titolo.setStyleSheet("color: #1a73e8; font-size: 28px; font-weight: bold;")
        titolo.setAlignment(Qt.AlignmentFlag.AlignCenter)  #Centra il testo

        #Sottotitolo
        sottotitolo = QLabel("Piattaforma Desktop per lo Scraping ed Estrazione Dati ANAC")
        sottotitolo.setFont(QFont("Helvetica", 12))
        sottotitolo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sottotitolo.setStyleSheet("color: #7f8c8d;")

        # Inserisce le due etichette dentro il layout della "scatola"
        layout.addWidget(titolo)
        layout.addWidget(sottotitolo)

        #prende l'intera scatola (frame) e la aggiunge al layout verticale PRINCIPALE della finestra
        self._layout.addWidget(frame)

    def _crea_filtri(self):     #Costruisce la sezione centrale contenente i campi di testo e i menu a tendina

        #creiamo una "scatola" contenitore con layout verticale
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)            # Distanza tra i vari campi di input
        layout.setContentsMargins(0, 0, 0, 0)

        # Titolo
        titolo = QLabel("FILTRI DI RICERCA")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        # CAMPO OGGETTO
        lbl_ogg = QLabel("Parola chiave oggetto:")
        lbl_ogg.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        layout.addWidget(lbl_ogg)   #Aggiunge la scritta
        self.campo_oggetto = QLineEdit()   #Crea la casella in cui l'utente può scrivere
        self.campo_oggetto.setPlaceholderText("Qualsiasi...")   #Testo grigio di suggerimento che scompare quando scrivi
        self.campo_oggetto.setFixedHeight(35)
        layout.addWidget(self.campo_oggetto)     # Aggiunge la casella al layout

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
        griglia.setSpacing(15)  #Spazio orizzontale tra la colonna di sinistra e quella di destra

        #Colonna di Sinistra: Stato gara
        col1 = QVBoxLayout()   # Mini-layout verticale per impilare Scritta + Menu a tendina
        col1.setSpacing(4)
        lbl_st = QLabel("Stato gara:")
        lbl_st.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        col1.addWidget(lbl_st)

        self.menu_stato = QComboBox()   # Crea il menu a tendina
        self.menu_stato.addItems(list(MAPPA_STATO.keys()))   # Lo riempie prendendo le "chiavi" dal dizionario in cima al file
        self.menu_stato.setFixedHeight(35)
        col1.addWidget(self.menu_stato)   #Lo aggiunge

        # Colonna di Destra: Tipologia gara
        col2 = QVBoxLayout()
        col2.setSpacing(4)
        lbl_tip = QLabel("Tipologia gara:")
        lbl_tip.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        col2.addWidget(lbl_tip)

        self.menu_tipologia = QComboBox()
        self.menu_tipologia.addItems(list(MAPPA_TIPOLOGIA.keys()))
        self.menu_tipologia.setFixedHeight(35)
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
        self.menu_contraente.setFixedHeight(35)
        layout.addWidget(self.menu_contraente)

        # Aggiunge l'intero blocco "Filtri" al layout della finestra principale
        self._layout.addWidget(frame)

    def _crea_sezione_data(self):    #Costruisce la sezione per filtrare i bandi in base alla data di pubblicazione
        # Creiamo il contenitore principale per la sezione data
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        #Titolo
        titolo = QLabel("FILTRO DATA DI PUBBLICAZIONE")
        titolo.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        titolo.setStyleSheet("color: #1a73e8; letter-spacing: 1px;")
        layout.addWidget(titolo)

        #CheckBox di attivazione
        self.checkbox_data = QCheckBox("Attiva limite temporale sui bandi")
        self.checkbox_data.setStyleSheet("font-weight: 500;")

        # SIGNAL & SLOT: Quando lo stato della casella cambia (cliccata o deselezionata),
        # PyQt chiama automaticamente la nostra funzione `_toggle_data`
        self.checkbox_data.stateChanged.connect(self._toggle_data)
        layout.addWidget(self.checkbox_data)

        #Riga orizzontale dei menu data
        riga_data = QHBoxLayout()
        riga_data.setSpacing(8)

        lbl_dm = QLabel("Bandi pubblicati a partire dal:")
        lbl_dm.setStyleSheet("color: #5c6b73;")
        riga_data.addWidget(lbl_dm)

        anno_corrente = datetime.now().year   # Recupera l'anno attuale dal sistema

        # MENU GIORNO
        self.menu_giorno = QComboBox()
        #Crea una lista di numeri da 1 a 31
        self.menu_giorno.addItems([str(g).zfill(2) for g in range(1, 32)])
        # Imposta di default il giorno di oggi
        self.menu_giorno.setCurrentText(str(datetime.now().day).zfill(2))
        self.menu_giorno.setEnabled(False) # Disabilitato all'avvio (fino a che non si spunta la checkbox)
        self.menu_giorno.setFixedSize(65, 32)
        self.menu_giorno.currentTextChanged.connect(self._valida_data) # Se l'utente cambia il giorno, chiama la funzione `_valida_data` per controllare se è corretto

        # MENU MESE
        self.menu_mese = QComboBox()
        self.menu_mese.addItems([str(m).zfill(2) for m in range(1, 13)]) # Mesi da 01 a 12
        self.menu_mese.setCurrentText(str(datetime.now().month).zfill(2))
        self.menu_mese.setEnabled(False)
        self.menu_mese.setFixedSize(65, 32)
        self.menu_mese.currentTextChanged.connect(self._valida_data)

        # MENU ANNO
        self.menu_anno = QComboBox()
        # Genera un elenco dal 2010 fino all'anno corrente
        self.menu_anno.addItems([str(a) for a in range(2010, anno_corrente + 1)])
        self.menu_anno.setCurrentText(str(anno_corrente))
        self.menu_anno.setEnabled(False)
        self.menu_anno.setFixedSize(85, 32)
        self.menu_anno.currentTextChanged.connect(self._valida_data)

        #Aggiunge i tre menu alla riga orizzontale
        riga_data.addWidget(self.menu_giorno)
        riga_data.addWidget(self.menu_mese)
        riga_data.addWidget(self.menu_anno)
        riga_data.addStretch()            # Spinge tutto a sinistra lasciando vuoto lo spazio a destra
        layout.addLayout(riga_data)

        # MESSAGGIO DI ERRORE DATA
        # Etichetta pronta a mostrare errori se la combinazione di data è errata. All'inizio è vuota (""), quindi invisibile.
        self.label_errore_data = QLabel("")
        self.label_errore_data.setStyleSheet("color: #d63031; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.label_errore_data)

        self._layout.addWidget(frame)


    def _toggle_data(self):    #Attiva o spegne i menu delle date e cambia il loro stile visivo in base alla Checkbox
        # Controlla se la casella è spuntata (True) o no (False)
        attivo = self.checkbox_data.isChecked()

        # Abilita o disabilita fisicamente i tre menu
        self.menu_giorno.setEnabled(attivo)
        self.menu_mese.setEnabled(attivo)
        self.menu_anno.setEnabled(attivo)

        # Quando disabilitati, togliamo il bordo blu intenso visivamente tramite foglio di stile
        if attivo:
            self.menu_giorno.setStyleSheet("")
            self.menu_mese.setStyleSheet("")
            self.menu_anno.setStyleSheet("")
        else:
            stile_disabilitato = "QComboBox { border: 1px solid #dcdde1; background-color: #f1f2f6; }"
            self.menu_giorno.setStyleSheet(stile_disabilitato)
            self.menu_mese.setStyleSheet(stile_disabilitato)
            self.menu_anno.setStyleSheet(stile_disabilitato)

        #controlliamo se la data inserita (anche se appena riattivata) è valida
        self._valida_data()



    def _valida_data(self):     #Verifica che la data selezionata dall'utente esista davvero

        # Se la spunta non è attiva, non c'è bisogno di validare nulla. Cancelliamo eventuali errori.
        if not self.checkbox_data.isChecked():
            self.label_errore_data.setText("")
            return True
        try:
            # datetime.strptime prende una stringa (es. "31/02/2026") e prova a convertirla in una data reale
            # usando il formato "%d/%m/%Y" (giorno/mese/anno a 4 cifre)
            datetime.strptime(
                f"{self.menu_giorno.currentText()}/{self.menu_mese.currentText()}/{self.menu_anno.currentText()}",
                "%d/%m/%Y"
            )
            # Se la conversione riesce, la data esiste. Cancelliamo l'errore.
            self.label_errore_data.setText("")
            return True
        except ValueError:
            #Se la conversione fallisce mostriamo l'errore con la scritta rossa
            self.label_errore_data.setText("⚠ Configurazione data non valida.")
            return False

    def _crea_sezione_salvataggio(self):   #Costruisce i campi relativi alla scelta del nome file e a dove salvare il file Excel di output
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

        #Campo di testo in cui l'utente può digitare il nome dell'Excel
        self.campo_nome_file = QLineEdit()
        self.campo_nome_file.setPlaceholderText("Lascia vuoto per generazione automatica")
        self.campo_nome_file.setFixedHeight(35)

        #Ogni volta che il testo nella casella cambia viene attivato il controllo tramite la funzione _valida_nome_file
        self.campo_nome_file.textChanged.connect(self._valida_nome_file)
        layout.addWidget(self.campo_nome_file)

        #Etichetta di errore dedicata ai simboli vietati (inizialmente vuota)
        self.label_errore_nome = QLabel("")
        self.label_errore_nome.setStyleSheet("color: #d63031; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.label_errore_nome)

        #Riga orizzontale per la sezione dell cartella
        riga_cartella = QHBoxLayout()
        riga_cartella.setSpacing(10)

        #mostra il percorso scelto
        lbl_dest = QLabel("Destinazione:")
        lbl_dest.setStyleSheet("font-weight: bold; color: #4e5d6c;")
        riga_cartella.addWidget(lbl_dest)

        self.label_cartella = QLabel("Cartella del progetto (Default)")
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
        self.pulsante_sfoglia.clicked.connect(self._scegli_cartella)  # Quando viene cliccato, si apre la finestra nativa di scelta cartella
        riga_cartella.addWidget(self.pulsante_sfoglia)

        # Aggiungiamo la riga orizzontale della cartella al layout principale di questa sezione
        layout.addLayout(riga_cartella)

        self._layout.addWidget(frame)

    def _scegli_cartella(self):         #Apre la finestra per selezionare interattivamente la cartella di destinazione
        cartella = QFileDialog.getExistingDirectory(self, "Scegli cartella di destinazione")  #apre la finestra standard per scegliere una cartella, restituisce una stringa con il percorso
        # Se è stata scelta una cartella (la stringa non è vuota)
        if cartella:
            self.label_cartella.setText(cartella)  # Aggiorna il testo dell'etichetta mostrando il percorso reale
            self.label_cartella.setStyleSheet("color: #2c3e50; font-style: normal; font-weight: 500;")

    def _valida_nome_file(self):            #Verifica l'uso di caratteri vietati nel nome del file
        nome = self.campo_nome_file.text().strip()  #Prende il testo digitato
        caratteri_vietati = {'/', '\\', ':', '*', '?', '"', '<', '>', '|'}   #Set di caratteri vietati
        trovati = [c for c in nome if c in caratteri_vietati]  #lista contenente solo i caratteri vietati inseriti
        if trovati:   ## Se la lista "trovati" contiene qualcosa, c'è un errore
            self.label_errore_nome.setText("⚠ Il nome contiene simboli non validi per i file di sistema.") #appare scritta di errore
            return False
        self.label_errore_nome.setText("")  #se non ci sono caratteri vietati cancella il testo e dà il via libera
        return True

    def _crea_pulsanti(self):    #Costruisce i due pulsanti (Reset e Avvia) in fondo
        frame = QWidget()
        layout = QHBoxLayout(frame)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 5, 0, 0)

        #Pulsante di reset
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
        self.pulsante_reset.clicked.connect(self._reset_filtri) #con il click esegue funzione '_reset_filtri'

        #Pulsante avvia ricerca
        self.pulsante_ricerca = QPushButton("🔍  Avvia Ricerca")
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
        self.pulsante_ricerca.clicked.connect(self._gestisci_pulsante)  #con il click esgue il metodo '_gestisci_pulsante'

        # Li aggiungiamo al layout orizzontale (Reset a sinistra, Ricerca a destra)
        layout.addWidget(self.pulsante_reset)
        layout.addWidget(self.pulsante_ricerca)

        #Aggiunge il layout alla finestra
        self._layout.addWidget(frame)

    def _reset_filtri(self):       #Ripristina tutti i campi dell'interfaccia allo stato iniziale
        #Se c'è una ricerca in corso blocca il reset
        if self._ricerca_in_corso:
            return

        #ripristina tutti i campi
        self.campo_oggetto.clear()
        self.campo_cig.clear()
        self.campo_nome_file.clear()
        self.label_cartella.setText("Cartella del progetto (default)")
        self.label_cartella.setStyleSheet("color: #7f8c8d; font-style: italic;")
        self.menu_stato.setCurrentIndex(0)
        self.menu_tipologia.setCurrentIndex(0)
        self.menu_contraente.setCurrentIndex(0)
        self.checkbox_data.setChecked(False)
        self.etichetta_finale.setText("")
        self.etichetta_finale.hide()

    def _gestisci_pulsante(self):     #Metodo di controllo, gestisce se avviare la ricerca o interromperla in base allo stato attuale

        #Se la ricerca è già attiva allora il click è avvenuto su interrompi
        if self._ricerca_in_corso:
            self._interrompi.set()    #interrompe il thread
            self.etichetta_stato.setText("Interruzione richiesta... Attendere il bando in esecuzione.")
            self.pulsante_ricerca.setEnabled(False)  #disattiva il pulsante per evitare click multipli
            return

        #Se la ricerca non è attiva ma i controlli di validita della data non vengono superati si ferma qui
        if not self._valida_data():
            return

        nome = self.campo_nome_file.text().strip() #prende il nome inserito
        if not self._valida_nome_file():   #se il nome non supera i controlli di attività si ferma
            return

        # se non è stato inserito il nome lo genera
        if not nome:
            nome = f"bandi_pistoia_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        #controlla se il nome ha l'estensione excel, senno la aggiunge
        if not nome.endswith(".xlsx"):
            nome += ".xlsx"

        cartella = self.label_cartella.text()  #recupera il percorso della cartella scelta
        percorso = nome if cartella == "Cartella del progetto (default)" else f"{cartella}/{nome}"  #Crea il percorso finale

        #Lancia la funzione che avvia lo scraping, passando il percorso in cui salvare
        self._avvia_ricerca(percorso)

    def _crea_barra_avanzamento(self):          #Costruisce la barra di progresso e stato in fondo alla pagina

        # Etichetta di stato, serve per mostrare messaggi tipo "Connessione al portale..." o "Elaborazione bando 5 di 10"
        self.etichetta_stato = QLabel("")
        self.etichetta_stato.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.etichetta_stato.setStyleSheet("color: #2c3e50; font-weight: 500; font-size: 12px;")
        self._layout.addWidget(self.etichetta_stato)

        #Barra di progresso
        self.barra = QProgressBar()
        self.barra.setFixedHeight(16)
        self.barra.hide()   #Si nasconde all'avvio del programma, si mostra solo durante la ricerca
        self._layout.addWidget(self.barra)

        #Etichetta finale, mostra il messaggio conclusivo
        self.etichetta_finale = QLabel("")
        self.etichetta_finale.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.etichetta_finale.setFont(QFont("Helvetica", 13, QFont.Weight.Bold))
        self.etichetta_finale.hide()      #Nascosta, si mostra solo alla fine della ricerca
        self._layout.addWidget(self.etichetta_finale)

    def _avvia_ricerca(self, percorso_file):     #Prepara l'interfaccia per il caricamento e lancia il thread separato per lo scraping
        self._interrompi.clear()   #Pulisce l'evento di interruzione (nel caso fosse rimasto "alzato" da una ricerca precedente)
        self._ricerca_in_corso = True  #Segnala che c'è una ricerca attiva
        self.etichetta_finale.hide()   #Nasconde eventuali messaggi di fine operazione precedenti

        #Cambia l'aspetto e la funzione del tasto "Avvia", facendolo diventare "Interrompi" (rosso)
        self.pulsante_ricerca.setText("⏹  Interrompi Ricerca")
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

        #Disabilita i pulsanti e i campi che non devono essere toccati mentre lo scraper lavora
        self.pulsante_reset.setEnabled(False)
        self.pulsante_sfoglia.setEnabled(False)
        self.campo_nome_file.setEnabled(False)

        #Inizializza la barra di progresso
        self.barra.setMaximum(0)
        self.barra.show()
        self.etichetta_stato.setText("Connessione al portale e analisi filtri...")

        #Raccolta dei filtri dell'interfaccia, crea un dizionario prendendo i testi inseriti e traducendoli con le mappe scritte in cima al file
        filtri = {
            "parola_chiave": self.campo_oggetto.text().strip(),
            "cig": self.campo_cig.text().strip(),
            "stato": MAPPA_STATO[self.menu_stato.currentText()],
            "tipologia": MAPPA_TIPOLOGIA[self.menu_tipologia.currentText()],
            "contraente": MAPPA_CONTRAENTE[self.menu_contraente.currentText()],
            "data_limite": (
                f"{self.menu_anno.currentText()}-{self.menu_mese.currentText()}-{self.menu_giorno.currentText()}"
                if self.checkbox_data.isChecked() else None
            )
        }

        # LANCIO DEL THREAD DI BACKGROUND E DEL TIMER
        # Qui avviene la magia: invece di eseguire la ricerca sul processo principale (che bloccherebbe la grafica),
        # diciamo a Python: "Esegui _esegui_ricerca in parallelo, passandogli questi argomenti".
        # daemon=True fa in modo che se l'utente chiude la finestra con la X, anche questo thread muoia subito.
        threading.Thread(target=self._esegui_ricerca, args=(percorso_file, filtri), daemon=True).start()

        # Fa partire il timer che ogni 100 millisecondi controllerà la coda per aggiornare i testi e la barra
        self._timer.start(100)

    def _controlla_coda(self):   #Controlla periodicamente la coda dei messaggi per aggiornare l'interfaccia grafica
        try:
            ## Ciclo infinito per svuotare TUTTI i messaggi accumulati nella coda in quel millesimo di secondo
            while True:
                #get_nowait() tenta di prendere un messaggio dalla coda.
                # Se la coda è vuota, non aspetta ma lancia immediatamente l'eccezione queue.Empty
                messaggio = self._coda.get_nowait()
                # Estrae il tipo di messaggio (es: "stato", "barra_valore", "fine")
                tipo = messaggio.get("tipo")

                #Aggiornamento del testo di stato
                if tipo == "stato":
                    #Cambia il testo dell'etichetta mostrando cosa sta facendo lo scraper
                    self.etichetta_stato.setText(messaggio.get("testo", ""))
                #Impostazione del totale dei bandi da elaborare
                elif tipo == "barra_determinata":
                    # Lo scraper ha scoperto quanti bandi ci sono in totale (es. 15).
                    # Impostiamo questo valore come massimo della barra e la azzeriamo.
                    self.barra.setMaximum(messaggio.get("totale", 100))
                    self.barra.setValue(0)
                #Avanzamento della barra di caricamento
                elif tipo == "barra_valore":
                    #Aggiorna il quadratino blu della barra (es. bando 4 di 15, poi 5 di 15...)
                    self.barra.setValue(messaggio.get("valore", 0))
                #Processo terminato (con successo, errore o interruzione)
                elif tipo == "fine":
                    #Ferma il Timer: non c'è più bisogno di controllare la coda
                    self._timer.stop()
                    # Chiama il metodo per ripristinare i pulsanti e mostrare il verdetto finale
                    self._fine_ricerca(messaggio.get("percorso"), messaggio.get("testo", ""))
                    return       #Esce dal metodo immediatamente
        except queue.Empty:
            pass    #Questa eccezione viene catturata quando la coda si svuota completamente.
                    # È il segnale per uscire dal ciclo 'while True' e ridare il controllo alla GUI,
                     # in attesa del prossimo scatto del Timer.

    def _esegui_ricerca(self, percorso_file, filtri):       #Esegue lo scraping
        try:
            #Crea l'URL di partenza inserendo i filtri
            url_ricerca = genera_url_con_filtri(
                parola_chiave=filtri["parola_chiave"],
                cig=filtri["cig"],
                stato=filtri["stato"],
                tipologia=filtri["tipologia"],
                contraente=filtri["contraente"]
            )
            #Scarica l'elenco di tutti i link dei bandi
            elenco_link = estrai_lista_bandi(url_ricerca, data_limite=filtri["data_limite"])
            totale = len(elenco_link)

            # Se la lista è vuota, si ferma subito e avvisa la GUI
            if totale == 0:
                self._coda.put({"tipo": "fine", "percorso": None, "testo": "Nessun bando trovato con i filtri selezionati."})
                return

            # Dice alla GUI quanti bandi ci sono in totale per impostare il massimo della barra
            self._coda.put({"tipo": "barra_determinata", "totale": totale})

            lista_risultati = []       # Conterrà i dati finali pronti per l'Excel
            contatore_falliti = 0       # Conta quante chiamate ANAC vanno in errore o non restituiscono dati

            #Ciclo di elaborazione dei singoli bandi, li enumera partendo da 1
            for i, link in enumerate(elenco_link, 1):
                #Verifica se l'utente ha cliccato interrompi
                if self._interrompi.is_set():
                    self._coda.put({
                        "tipo": "fine", "percorso": None,
                        "testo": f"Ricerca interrotta dopo {i - 1} bandi su {totale}."
                    })
                    return

                #Dalla seconda richiesta in poi aspetta 1,5 secondi per non sovraccaricare il server della provincia
                if i > 1:
                    time.sleep(1.5)

                # Aggiorna il testo di stato e la posizione della barra sulla GUI
                self._coda.put({"tipo": "stato", "testo": f"Elaborazione bando {i} di {totale}..."})
                self._coda.put({"tipo": "barra_valore", "valore": i})

                # Ricostruisce l'URL assoluto della pagina del bando se il link estratto è relativo
                url_completo = f"{BASE_URL}{link}" if not link.startswith("http") else link

                # Entra nella pagina del bando ed estrae le informazioni (Oggetto, Scadenza, lista CIG, ecc.)
                dati_bando = estrai_dettagli_bando(url_completo)
                lista_cig = dati_bando.get("cig_list", [])

                #Logica per il codice CIG dei singoli bandi
                if not lista_cig:    #Se il bando non ha codici CIG inseriti nella pagina della Provincia
                    #Salva comunque il bando lasciando vuoti i dati ANAC
                    lista_risultati.append({"provincia": dati_bando, "anac": {}, "cig_corrente": "Non trovato"})
                else:    #Se il bando contiene uno o più codici CIG
                    for cig_singolo in lista_cig:
                        if self._interrompi.is_set():    #Controllo di interruzione
                            break

                        #Interroga l'API di ANAC passando il singolo codice CIG
                        json_anac = scarica_json_anac(cig_singolo)
                        dati_anac = {}

                        if json_anac:
                            #Se l'API risponde correttamente, estrae le informazioni strutturate (importo, aggiudicatario...)
                            dati_anac = estrai_dati_json_anac(json_anac)
                        else:
                            #Se la chiamata fallisce o il CIG non esiste su ANAC, incrementa il contatore degli errori
                            contatore_falliti += 1

                        #Unisce i dati della Provincia, i dati ANAC e il CIG di riferimento in un unico blocco
                        lista_risultati.append({"provincia": dati_bando, "anac": dati_anac, "cig_corrente": cig_singolo})

                        time.sleep(1)       #Pausa di 1 secondo tra una chiamata ANAC e la successiva

            #Esportazione file
            #Dopo che tutti i bandi sono stati elaborati comunica il salvataggio
            self._coda.put({"tipo": "stato", "testo": "Salvataggio file Excel..."})

            #Genera il file Excel finale scrivendo i dati ottenuti
            salva_in_excel(lista_risultati, nome_file=percorso_file)

            #Messaggio di successo
            messaggio = f"✅  Completato! {totale} bandi elaborati ed esportati con successo."
            if contatore_falliti > 0:
                messaggio += f" ({contatore_falliti} CIG senza dati ANAC)"

            #Invia il segnale di fine alla GUI passando il messaggio e il percorso del file creato
            self._coda.put({"tipo": "fine", "percorso": percorso_file, "testo": messaggio})

        except Exception as e:
            # GESTIONE ERRORI CRITICI: Se qualcosa va in crash (es. salta internet),
            # cattura l'errore e lo mostra all'utente in modo sicuro senza far crashare l'intera app
            self._coda.put({"tipo": "fine", "percorso": None, "testo": f"Errore: {e}"})

    def _fine_ricerca(self, percorso_file, messaggio):  #Ripristina l'interfaccia al termine dello scraping o in caso di interruzione

        self._ricerca_in_corso = False      #Reset dello stato interno, dice che non c'è più nessuna ricerca in corso

        self.barra.hide()     #Nasconde la barra di caricamento

        self.etichetta_stato.setText("")        #Nasconde la scritta che mostrava i passaggi

        #Ripristina il pulsante principale, trasforma il pulsante rosso interrompi nel pulsante originale avvia ricerca
        self.pulsante_ricerca.setText("🔍  Avvia Ricerca")
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
        self.pulsante_ricerca.setEnabled(True) # Riattiva il pulsante

        #Riattiva tutti i pulsanti che erano stati disattivati per sicurezza durante la ricerca
        self.pulsante_reset.setEnabled(True)
        self.pulsante_sfoglia.setEnabled(True)
        self.campo_nome_file.setEnabled(True)

        #Logica dei colori: se 'percorso_file' esiste (True), significa che il file è stato salvato con successo
        # e usa il verde. Se è None (False), significa errore, nessun bando o interruzione, e usa l'arancione.
        colore = "#2ecc71" if percorso_file else "#e67e22"
        self.etichetta_finale.setStyleSheet(f"color: {colore}; font-weight: bold;")  # Applica il colore scelto e imposta il grassetto
        self.etichetta_finale.setText(messaggio)     # Inserisce il testo (es. "Completato! 10 bandi elaborati" oppure "Operazione interrotta")
        self.etichetta_finale.show()   # Rende finalmente visibile il messaggio di risultato in fondo all'applicazione

if __name__ == "__main__":
    # Controlla se la versione di Qt in uso possiede l'attributo per lo scaling automatico.
    # Se presente, lo attiva per evitare che i font e i pulsanti si vedano sfocati o minuscoli.
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    # Fa la stessa verifica per le icone e le immagini (Pixmap),
    # garantendo che vengano renderizzate a doppia densità dove supportato.
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    #INIZIALIZZAZIONE E AVVIO DELL'APPLICAZIONE
    # Crea l'oggetto QApplication fondamentale. Gestisce il flusso di controllo,
    # le impostazioni principali e riceve gli argomenti passati da riga di comando (sys.argv).
    app = QApplication(sys.argv)

    # Istanzia la classe della nostra interfaccia grafica (chiamando il metodo __init__ )
    finestra = BandiPistoiaApp()

    # Rende la finestra visibile sullo schermo dell'utente
    finestra.show()

    # Avvia l'event loop principale di PyQt (app.exec()). Il programma rimane "vivo" e in ascolto
    # dei click dell'utente finché la finestra non viene chiusa.
    # sys.exit passera al sistema operativo il codice di uscita corretto (0 se si chiude normalmente).
    sys.exit(app.exec())