"""
Interruttore unico per i messaggi diagnostici dei moduli di logica.

Uso:
    - main.py (versione da terminale, usata per il debug) accende l'interruttore:

          import console
          console.VERBOSE = True

    - gui.py e app.py non fanno nulla: restano silenziosi (VERBOSE = False).

ATTENZIONE: per accendere l'interruttore serve 'import console' seguito da
'console.VERBOSE = True'. Scrivere 'from console import VERBOSE' NON funziona:
copierebbe il valore in una variabile locale al file, lasciando invariato
quello che log() legge davvero.

Se un domani servisse di piu' (livelli di gravita', orari, log su file per
l'app web), basta riscrivere log() qui dentro appoggiandosi al modulo standard
'logging': i punti di chiamata negli altri file non vanno toccati.
"""

# Falso per impostazione predefinita: chi non dice niente resta in silenzio.
# Cosi' le interfacce grafica e web sono mute senza dover fare nulla, ed e' la
# versione da terminale a doversi dichiarare esplicitamente "parlante".
VERBOSE = False


def log(*args, **kwargs):
    """
    Stampa il messaggio solo se VERBOSE e' acceso, altrimenti non fa nulla.

    Accetta gli stessi argomenti di print() (compresi end, sep, flush), cosi'
    la conversione dai vecchi print e' stata una semplice sostituzione del nome.
    """
    if VERBOSE:
        print(*args, **kwargs)