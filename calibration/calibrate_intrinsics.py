import cv2
import numpy as np
import os
import glob
import json
import time

def calibrate_camera():
    print("=======================================")
    print("📸 OpenCV Kamera Intrinsics Kalibrierung")
    print("=======================================")
    
    # --- Konfiguration ---
    chessboard_size = (9, 6) # Anzahl der inneren Ecken (Spalten, Zeilen)
    square_size = 0.024 # Größe eines Quadrats in Metern (24mm = 0.024m)
    
    # Abbruch-Bedingungen (Genauigkeit für Subpixel-Berechnung)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    # Vorbereiten der realen 3D Punkte des Schachbretts (0,0,0), (1,0,0), (2,0,0) ....,(8,5,0)
    objp = np.zeros((chessboard_size[0] * chessboard_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:chessboard_size[0], 0:chessboard_size[1]].T.reshape(-1, 2)
    objp = objp * square_size

    # Arrays zum Speichern von 3D-Punkten und 2D-Punkten aus allen Bildern
    objpoints = [] # 3D Punkte im realen Raum
    imgpoints = [] # 2D Punkte auf der Bildebene

    # Kamera starten (Wir suchen /dev/video0, /dev/video2 usw.)
    cap = None
    for index in [2, 0, 1, 3]:
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if cap.isOpened():
            print(f"✅ Kamera auf Index {index} gefunden.")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            break
        cap.release()
        cap = None

    if cap is None:
        print("❌ FEHLER: Keine Kamera gefunden!")
        return

    print("\nANLEITUNG:")
    print("1. Halte ein Schachbrett (9x6 innere Ecken, 24mm Kantenlänge) in die Kamera.")
    print("2. Drücke 'Leertaste' um ein Bild zu speichern, wenn das Muster erkannt wird (Linien sichtbar).")
    print("3. Bewege das Schachbrett in verschiedene Winkel und Abstände.")
    print("4. Speichere ca. 15 bis 20 gute Bilder.")
    print("5. Drücke 'c' um die Kalibrierung zu berechnen und zu beenden.")
    print("6. Drücke 'q' zum Abbrechen ohne Speichern.")
    
    captured_frames = 0
    img_shape = None

    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️ Fehler beim Lesen des Kamera-Frames...")
            time.sleep(0.1)
            continue
            
        if img_shape is None:
            v_h, v_w = frame.shape[:2]
            img_shape = (v_w, v_h)
            
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Schachbrett-Ecken finden
        ret_chess, corners = cv2.findChessboardCorners(gray, chessboard_size, None)
        
        display_frame = frame.copy()
        
        if ret_chess:
            # Subpixel-Genauigkeit für bessere Ergebnisse
            corners2 = cv2.cornerSubPix(gray, corners, (11,11), (-1,-1), criteria)
            # Ecken einzeichnen
            cv2.drawChessboardCorners(display_frame, chessboard_size, corners2, ret_chess)
            cv2.putText(display_frame, "Muster Erkannt! (Leertaste=Speichern)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        else:
            cv2.putText(display_frame, "Suche Schachbrett...", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
        cv2.putText(display_frame, f"Gespeichert: {captured_frames}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        cv2.putText(display_frame, "C = Calibrieren | Q = Beenden", (10, int(img_shape[1]) - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow('Kamera Kalibrierung', display_frame)
        
        key = cv2.waitKey(1) & 0xFF
        
        if key == ord('q'):
            print("\n❌ Abbruch durch Benutzer.")
            break
        elif key == 32: # Leertaste
            if ret_chess:
                objpoints.append(objp)
                imgpoints.append(corners2)
                captured_frames += 1
                print(f"📸 Bild {captured_frames} gespeichert!")
                # Kurzes visuelles Feedback
                cv2.rectangle(display_frame, (0,0), (img_shape[0], img_shape[1]), (0,255,0), 10)
                cv2.imshow('Kamera Kalibrierung', display_frame)
                cv2.waitKey(200)
            else:
                print("⚠️ Konnte kein vollständiges Muster in diesem Frame finden.")
        elif key == ord('c'):
            if captured_frames < 5:
                print("\n⚠️ Nicht genug Bilder! Bitte mindestens 5-10 speichern.")
                continue
                
            print("\n⚙️ Berechne Kalibrierungs-Matrix... Bitte warten...")
            # Kalibrierung durchführen
            ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, img_shape, None, None)
            
            print(f"\n✅ Kalibrierung erfolgreich (RMS Fehler: {ret:.4f})")
            print("\nKameramatrix (Intrinsics):")
            print(mtx)
            print("\nVerzerrungskoeffizienten:")
            print(dist)
            
            # config.json updaten
            config_path = os.path.join(os.path.dirname(__file__), "camera_config.json")
            data = {}
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        data = {}
            
            if "intrinsics" not in data:
                data["intrinsics"] = {}
                
            data["intrinsics"]["focal_length"] = float(mtx[0][0]) # fx Wert
            data["intrinsics"]["image_width"] = int(img_shape[0])
            data["intrinsics"]["image_height"] = int(img_shape[1])
            data["intrinsics"]["camera_matrix"] = mtx.tolist()
            data["intrinsics"]["distortion_coefficients"] = dist.tolist()
            
            with open(config_path, 'w') as f:
                json.dump(data, f, indent=4)
                
            print(f"\n💾 Neue Werte in {config_path} gespeichert!")
            print("Du kannst dieses Fenster nun schließen.")
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    calibrate_camera()
