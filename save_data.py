from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from datetime import datetime

def salva_in_excel(lista_bandi, nome_file=None):
    #Se non viene fornito un nome da dare al file ne crea uno formato da "bandi_pistoia" +  data e ora correnti
    if not nome_file:
        ora = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_file = f"bandi_pistoia_{ora}.xlsx"

    #Workbook() crea un nuovo file Excel in memoria (non ancora salvato su disco).
    #wb.active ottiene il primo foglio di lavoro, creato automaticamente.
    #ws.title = ... rinomina quel foglio da "Sheet" (default) a "Bandi di Gara"
    wb = Workbook()
    ws = wb.active
    ws.title = "Bandi di Gara"

    #definiamo le intestazioni della tabella
    intestazioni = [
        "CIG", "Oggetto Gara", "Tipologia", "Scelta Contraente", "Enti",
        "Data Pubblicazione", "Scadenza Manif. Interesse", "Data Scadenza",
        "CUP", "CPV", "Descrizione CPV", "Tipo Scelta Contraente (ANAC)",
        "Aggiudicatario", "CF Aggiudicatario", "Numero Gara", "URL Bando"
    ]

    #definizione dello stile della tabella
    stile_intestazione = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    stile_sfondo = PatternFill("solid", start_color="1F4E79")
    stile_centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    bordo_sottile = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    #definisce lo stile delle intestazioni
    for col, intestazione in enumerate(intestazioni, 1):
        cella = ws.cell(row=1, column=col, value=intestazione)
        cella.font = stile_intestazione
        cella.fill = stile_sfondo
        cella.alignment = stile_centro
        cella.border = bordo_sottile  # <- bordi intestazioni

    colore_pari = PatternFill("solid", start_color="D6E4F0")
    colore_dispari = PatternFill("solid", start_color="FFFFFF")
    font_dati = Font(name="Arial", size=10)
    allineamento_dati = Alignment(vertical="center", wrap_text=True)

    # Inserimento dati riga per riga
    for riga_idx, bando in enumerate(lista_bandi, 2):
        dati_provincia = bando.get("provincia", {})
        dati_anac = bando.get("anac", {})

        riga = [
            bando.get("cig_corrente", "Non trovato"),
            dati_anac.get("oggetto_gara", ""),
            dati_provincia.get("tipologia", ""),
            dati_provincia.get("scelta_contraente", ""),
            dati_provincia.get("enti", ""),
            dati_provincia.get("data_pubblicazione", ""),
            dati_provincia.get("scadenza_manifestazione", ""),
            dati_provincia.get("data_scadenza", ""),
            dati_anac.get("cup", ""),
            dati_anac.get("cod_cpv", ""),
            dati_anac.get("descrizione_cpv", ""),
            dati_anac.get("tipo_scelta_contraente", ""),
            dati_anac.get("aggiudicatario", ""),
            dati_anac.get("aggiudicatario_cf", ""),
            dati_anac.get("numero_gara", ""),
            dati_provincia.get("url_provincia", "")
        ]

        colore_riga = colore_pari if riga_idx % 2 == 0 else colore_dispari

        for col_idx, valore in enumerate(riga, 1):
            cella = ws.cell(row=riga_idx, column=col_idx, value=valore)
            cella.font = font_dati
            cella.fill = colore_riga
            cella.alignment = allineamento_dati
            cella.border = bordo_sottile

    # Altezza righe dati
    for r in range(2, len(lista_bandi) + 2):
        ws.row_dimensions[r].height = 60

    # Larghezze colonne adattate al contenuto
    for col in ws.columns:
        larghezza_max = 0
        for cella in col:
            if cella.value:
                larghezza_max = max(larghezza_max, len(str(cella.value)) * 1.2)
        ws.column_dimensions[col[0].column_letter].width = min(max(larghezza_max, 12), 80)

    # Larghezze fisse per colonne specifiche (sovrascrivono il calcolo automatico)
    ws.column_dimensions['A'].width = 18   # CIG
    ws.column_dimensions['B'].width = 70   # Oggetto Gara

    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"

    wb.save(nome_file) #scrive effettivamente il file .xlsx su disco, nella cartella corrente del progetto, con il nome calcolato/scelto all'inizio
    print(f"\n[+] File Excel salvato: {nome_file}")
    return nome_file

