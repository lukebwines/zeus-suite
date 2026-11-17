# Piano di distribuzione di ZEUS Suite

## Stato e obiettivo

- Sorgenti privati nel repository `lukebwines/zeus-suite`, ramo `main`.
- Versione base: `1.7.17-rc4-nav1`.
- Installazione attiva Home Assistant: add-on locale `local_casa_consumi`.
- Obiettivo finale: aggiornamenti visibili nel catalogo di Home Assistant e dati di produzione conservati.

## Vincoli

1. Un repository privato GitHub non è un URL di distribuzione pubblicamente clonabile dal Supervisor. Il file `repository.yaml` rende riconoscibile la struttura, non fornisce autenticazione.
2. Gli add-on associati a repository differenti hanno identificativi distinti (`local_casa_consumi` non diventa automaticamente l'add-on del nuovo repository).
3. L'eventuale immagine Docker pubblica rende recuperabile il codice dell'applicazione contenuto nell'immagine. Non pubblicare immagini o sorgenti senza autorizzazione.
4. Non sostituire il database di produzione e non installare il nuovo add-on sulle stesse porte mentre il precedente è in funzione.

## Passaggio di verifica predisposto

- In GitHub Actions è presente `Verifica ZEUS Suite`: avvio manuale, lettura del codice, controllo sintassi Python e JavaScript, build Docker locale di prova per `amd64`.
- Il workflow non esegue push delle immagini, pubblicazione di package, migrazioni, comandi verso Home Assistant, né modifiche ai dati.
- Un esito positivo prova la compilazione, non l'equivalenza con l'immagine attualmente installata o la correttezza delle funzioni contabili.

## Opzioni per la distribuzione

**A — Tutto privato, canale interno**: usare un repository Git leggibile solo dalla rete locale o attraverso un canale sicuro gestito dall'amministratore, in modo che Home Assistant possa consultarlo e proporre gli aggiornamenti. Serve predisporre e proteggere il servizio di distribuzione interno e testare il supporto del Supervisor.

**B — Repository pubblico di sola distribuzione**: catalogo accessibile dal Supervisor con immagine Docker precompilata pubblicamente scaricabile, mantenendo il repository sorgente GitHub privato. Questa scelta espone comunque il contenuto dell'immagine a chiunque la scarichi; richiede approvazione esplicita prima della pubblicazione.

**C — Aggiornamento locale controllato**: mantenere `local_casa_consumi` e sincronizzare i sorgenti da GitHub tramite un processo autenticato di gestione locale. Evita un nuovo identificativo, ma non realizza automaticamente il normale flusso 'Aggiorna' del catalogo senza ulteriore infrastruttura.

## Prerequisiti obbligatori della migrazione

1. Ruotare password e token di Home Assistant già esposti nella chat; non inserirli nel repository né negli URL.
2. Eseguire un backup completo verificato, con copia fuori dal Raspberry e prova di ripristino.
3. Collaudare un'istanza separata senza collegarla al database di produzione; usare porte alternative.
4. Verificare bollette, letture QR, rendiconti, scadenze, archivio e flussi Finance, inclusi i dati storici.
5. Conservare l'add-on `local_casa_consumi` finché la procedura di ritorno alla versione precedente non sia stata verificata.

## Decisione da prendere

Selezionare il canale di distribuzione A, B o C prima di creare repository pubblici, pubblicare immagini o procedere alla migrazione.
