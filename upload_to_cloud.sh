#!/bin/bash

# Ins richtige Verzeichnis wechseln
cd /home/ubuntu22/Documents/aivision || exit

# Überprüfen, ob es überhaupt Änderungen gibt
if [ -n "$(git status --porcelain)" ]; then
    echo "☁️ Neue Änderungen gefunden! Lade in die Cloud hoch..."
    
    # Änderungen hinzufügen
    git add .
    
    # Aktuelles Datum und Uhrzeit als Commit-Nachricht
    COMMIT_MSG="Automatisches Update: $(date +'%Y-%m-%d %H:%M:%S')"
    git commit -m "$COMMIT_MSG"
    
    # In die Cloud (GitHub) hochladen
    echo "🚀 Sende an GitHub..."
    git push
    
    echo "✅ Erfolgreich in die Cloud hochgeladen!"
else
    echo "✨ Alles ist bereits auf dem neuesten Stand in der Cloud!"
fi
