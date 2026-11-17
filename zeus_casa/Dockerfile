FROM ghcr.io/home-assistant/base:latest
ARG BUILD_VERSION=1.0.0
ARG BUILD_ARCH=aarch64
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache python3 tzdata py3-pillow font-dejavu
WORKDIR /opt/casa-consumi
COPY analisi.html analisi.js analisi.css bollette.html bollette.js bollette.css nuova-bolletta.html nuova-bolletta.js nuova-bolletta.css auth_session.py login_branding.py ha_integration.py tenant_reports.py report_corrections.py documents.py tenant_handover.py tenant_deletions.py server.py full_backup.py activity.py activity.html activity.js manutenzione.html manutenzione.js immobili.html immobili.js settings.html settings.js zeus-admin.css advanced.py consumption_carry.py debts.py finance.py finance_balance.py finance.html finance.js finance.css bilancio.html bilancio.js bilancio.css vault.py vault.html vault.js vault.css readings.py workflow.py qr_readings.py reader.html reader.js reader.css qr-admin.js index.html home.html home.css home.js shell.html shell.js bootstrap.js app.js nav.js zeus-nav.css gestione.html gestione.js gestione.css debiti.html debiti.js debiti.css letture.html letture.js letture.css percorso.html percorso.js percorso.css documenti.html documenti.js documenti.css login.html login.js login.css auth-ui.js document-helper.js favicon.ico favicon-16.png favicon-32.png zeus-icon.svg zeus-icon-192.png zeus-icon-512.png zeus-maskable-512.png apple-touch-icon.png manifest.webmanifest /opt/casa-consumi/
COPY qr_lib /opt/casa-consumi/qr_lib/
EXPOSE 8090 8091
CMD ["python3", "-u", "/opt/casa-consumi/server.py"]
