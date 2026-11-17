# ZEUS Suite

Repository privato del codice di ZEUS Suite per Home Assistant.

## Base importata

- Versione di riferimento: **1.7.17-rc4-nav1**.
- Provenienza: `lukebwines/zeus-casa-addons`, ramo `fix/suite-rc4-navigation-20261117`.
- Importati i file necessari al `Dockerfile`, il `config.yaml` e la libreria QR, verificando che le impronte Git dei sorgenti coincidano con quelle originali.
- Esclusi dal nuovo repository i vecchi materiali di prova e la cartella sorgente Paghe non inclusa nella build.
- Unico ramo di sviluppo iniziale: `main`.

## Stato di Home Assistant

**Il repository non è ancora collegato all'installazione in produzione.** L'add-on attivo ha identificativo `local_casa_consumi` e mantiene i suoi dati; pubblicare i sorgenti qui non modifica l'installazione.

Home Assistant identifica un add-on tramite **repository + slug**. L'eventuale installazione da questo repository avrebbe un identificativo differente, quindi i dati non vanno considerati trasferiti automaticamente. Prima di qualsiasi migrazione occorrono backup verificato, ripristino di prova e controllo di letture QR, bollette, rendiconti e moduli finanziari.

**Distribuzione da progettare:** essendo privato, il repository richiede un accesso Git autenticato e non è direttamente disponibile con un normale URL GitHub anonimo nel catalogo Home Assistant. Non inserire token o password negli URL condivisi. Va verificata una strategia di distribuzione sicura prima di attivare gli aggiornamenti dall'interfaccia di Home Assistant.

## ZEUSmatic

ZEUSmatic Lab **1.7.16-lab1** resta la base concordata per la successiva integrazione, mantenuta separata finché non sono definiti e collaudati selettore globale classica/ZEUSmatic, conguaglio e conservazione dello storico.
