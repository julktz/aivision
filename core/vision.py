import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import cv2
import numpy as np
import math
import json
from ultralytics import YOLO
from collections import deque
import torch
# GANZ WICHTIG: Verhindert "core dumped" Abstürze bei Multithreading auf Intel CPUs!
torch.set_num_threads(1) 

import threading
import time

# --- KONSTANTEN ---
OBJECT_Z_LEVEL = 0.02  # Tisch-Höhe in Metern (relativ zu Base)
YOLO_MODEL = "yolo11.pt" # Name des Modells im gleichen Ordner

class VisionSystem:
    def __init__(self):
        print("👁️ Starte Vision System (Universal Modus)...")
        
        # Threading Variablen
        self.frame_lock = threading.Lock()
        self.hw_lock = threading.Lock() # NEU: Sperrt den direkten Kamerazugriff (Hardware)
        self.latest_frame = None
        self.running = True
        self.paused = False # NEU: Pausiert AI & Kamera für stabilere Menüs
        
        self.cap = None
        self.camera_ok = False
        self.img_width = 640
        self.img_height = 480
        self.focal_length = 662.75
        self.focal_length = 662.75
        self.dist_coeffs = [0.0]*5

        self.camera_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "camera_config.json")
        self.reload_camera_config()

        # Wir suchen die Kamera prioritär in Reihenfolge:
        # 1 = Externe Logitech Webcam (aktuell auf video1)
        # 0 = Fallback Cobot/Externe
        # 2 = Laptop interne Kamera (soll zuletzt gesucht werden)
        for index in [1, 0, 2, 3, 4, 5]:
            try:
                # Wir nutzen CAP_V4L2 unter Linux für USB-Webcams
                temp_cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
                
                if temp_cap.isOpened():
                    # Logitech C920 Optimierung
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.img_width)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.img_height)
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    temp_cap.set(cv2.CAP_PROP_FPS, 30)
                    
                    # Test-Frame lesen
                    ret, frame = temp_cap.read()
                    if ret:
                        self.cap = temp_cap
                        self.camera_ok = True
                        print(f"✅ Kamera auf Index {index} gefunden & gestartet!")
                        break
                    else:
                        temp_cap.release()
            except Exception as e:
                print(f"⚠️ Port {index} Fehler: {e}")

        if not self.camera_ok:
            print("❌ FEHLER: Keine Kamera gefunden. (Blind-Modus)")
            self.cap = None
        else:
            # Start Camera Thread
            self.cam_thread = threading.Thread(target=self._update_camera, daemon=True)
            self.cam_thread.start()
        
        self.device = torch.device('mps' if torch.backends.mps.is_available() else ('cuda' if torch.cuda.is_available() else 'cpu'))
        print(f"⚡ Nutze Hardware-Beschleunigung: {self.device}")
        
        # 1. YOLO
        # Lade das konfigurierte Modell
        model_path = os.path.join(os.path.dirname(__file__), "..", "models", YOLO_MODEL)
        self.model = YOLO(model_path)
        self.model.to(self.device)

        self.angle_history = deque(maxlen=5)
        
        # NEU: 1 FPS Limitierung asynchron + Thread Sicherung
        self.proc_lock = threading.Lock()
        self.last_ai_time = 0
        self.cached_detections = []
        self.is_ai_running = False # NEU: Status für GUI

        # Thread starten
        self.ai_thread = threading.Thread(target=self._ai_loop, daemon=True)
        self.ai_thread.start()

    def reload_camera_config(self):
        try:
            if os.path.exists(self.camera_config_path):
                with open(self.camera_config_path, 'r') as f:
                    data = json.load(f)
                
                intrin = data.get("intrinsics", {})
                self.focal_length = intrin.get("focal_length", 662.75)
                self.img_width = intrin.get("image_width", 640)
                self.img_height = intrin.get("image_height", 480)
                # Keep distortion for future use in unwarping if needed
                self.dist_coeffs = intrin.get("distortion_coefficients", [0.0]*5)
                
                print(f"👁️ Vision Config geladen: W={self.img_width}, H={self.img_height}, focal={self.focal_length}")
                
                with self.hw_lock:
                    if self.cap is not None and self.cap.isOpened():
                        # Assert for Pyre2
                        cap = self.cap
                        assert cap is not None
                        current_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                        current_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                        
                        if current_w != self.img_width or current_h != self.img_height:
                            print(f"🔄 Ändere Kamera-Auflösung auf {self.img_width}x{self.img_height}...")
                            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.img_width)
                            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.img_height)
                return True
        except Exception as e:
            print(f"⚠️ Fehler beim Laden der Vision Config: {e}")
            
        self.focal_length = 662.75
        self.img_width = 640
        self.img_height = 480
        return False

    def _ai_loop(self):
        """Dieser Thread berechnet die KI sicher im Hintergrund (1 FPS)."""
        print("🧠 AI-Background-Thread gestartet...")
        while self.running:
            if not self.camera_ok or self.latest_frame is None or self.paused:
                time.sleep(0.1)
                continue

            current_time = time.time()
            # Nur jede Sekunde einmal die schwere KI laufen lassen
            if current_time - self.last_ai_time >= 1.0:
                with self.frame_lock:
                    frame_to_process = self.latest_frame.copy()
                
                try:
                    self.is_ai_running = True
                    _, detections = self._run_ai(frame_to_process)
                    self.is_ai_running = False
                    
                    with self.proc_lock:
                        if len(detections) > 0:
                            print(f"[{time.strftime('%H:%M:%S')}] KI Live: {len(detections)} Objekte gefunden.")
                        self.cached_detections = detections
                        self.last_ai_time = current_time
                except Exception as e:
                    self.is_ai_running = False
                    print(f"⚠️ Fehler im AI Thread: {e}")
            
            # WICHTIG: Thread schlafen legen, um CPU nicht auszulasten
            time.sleep(0.05)

    def _update_camera(self):
        while self.running and self.camera_ok:
            if self.paused:
                time.sleep(0.1)
                continue
            with self.hw_lock:
                ret, frame = self.cap.read()
            if ret:
                with self.frame_lock:
                    self.latest_frame = frame
            else:
                self.camera_ok = False
                print("⚠️ Kamera Signal weg!")
            time.sleep(0.01) # Kleine Pause für CPU

    def get_frame(self):
        with self.frame_lock:
            if self.camera_ok and self.latest_frame is not None:
                # WICHTIG: Wir machen hier eine Kopie (.copy()), damit der 
                # Haupt-Thread (GUI) nicht auf dem Speicher malt, den der 
                # KI-Thread im Hintergrund gerade lesen möchte!
                return self.latest_frame.copy()
        
        # Fallback: Dummy Bild
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(dummy, "KEINE KAMERA", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        return dummy

    def process_image(self, frame):
        if not self.camera_ok:
            return frame, []

        # Sicher die letzten Boxen aus dem Hintergrund-Prozess holen
        with self.proc_lock:
            current_dets = list(self.cached_detections)

        # Zeichnen der Boxen auf das immer aktuelle Live-Bild (30 FPS)
        for det in current_dets:
            color = (0, 255, 0)
            text = f"{det['label']} ({det['score']:.2f})"
                
            if 'corners' in det and 'p2' in det:
                cv2.polylines(frame, [det['corners']], isClosed=True, color=color, thickness=2)
                cv2.line(frame, det['center'], det['p2'], (0, 0, 255), 2)
                # corners hat shape (4, 1, 2), wir extrahieren x und y
                px, py = int(det['corners'][0][0][0]), int(det['corners'][0][0][1])
                cv2.putText(frame, text, (px, py - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        return frame, current_dets

    def _run_ai(self, frame):
        if self.paused: return frame, []

        # OBB-Modelle brauchen keine retina_masks mehr
        results = self.model(
            frame, 
            imgsz=self.img_width,   # Dynamische Bildgröße
            stream=True, 
            conf=0.50,   
            device=self.device, 
            verbose=False
        )
        detections = []

        for r in results:
            if self.paused: break # Sofort stoppen wenn GUI es will
            
            # 1. Prüfen welcher Detektions-Typ vorliegt (OBB oder Standard Boxen)
            curr_detections = []
            
            if r.obb is not None and len(r.obb) > 0:
                # --- FALL A: Oriented Bounding Boxes (OBB) ---
                for j in range(len(r.obb)):
                    xywhr = r.obb.xywhr[j].cpu().numpy()
                    cx, cy, w, h, angle_rad = xywhr
                    center = (int(cx), int(cy))
                    angle_deg = math.degrees(angle_rad)
                    
                    corners = r.obb.xyxyxyxy[j].cpu().numpy()
                    x_coords = corners[:, 0]; y_coords = corners[:, 1]
                    x1, x2 = int(np.min(x_coords)), int(np.max(x_coords))
                    y1, y2 = int(np.min(y_coords)), int(np.max(y_coords))
                    
                    curr_detections.append({
                        "center": center, "angle": angle_deg, "box": [x1, y1, x2, y2],
                        "corners": corners.reshape((-1, 1, 2)).astype(np.int32),
                        "cls": int(r.obb.cls[j].item()), "angle_rad": angle_rad
                    })
            
            elif r.boxes is not None and len(r.boxes) > 0:
                # --- FALL B: Standard Horizontal Boxes ---
                for j in range(len(r.boxes)):
                    b = r.boxes.xyxy[j].cpu().numpy() # [x1, y1, x2, y2]
                    x1, y1, x2, y2 = b
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    
                    # Bei Standard Boxen gibt es keinen Winkel (0 Grad)
                    corners = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                    
                    curr_detections.append({
                        "center": (int(cx), int(cy)), "angle": 0.0, "box": [int(x1), int(y1), int(x2), int(y2)],
                        "corners": corners.reshape((-1, 1, 2)).astype(np.int32),
                        "cls": int(r.boxes.cls[j].item()), "angle_rad": 0.0
                    })

            # --- Verarbeitung der gesammelten Detektionen ---
            for d in curr_detections:
                center = d["center"]
                angle_deg = d["angle"]
                angle_rad = d["angle_rad"]
                box_coords = d["box"]
                x1, y1, x2, y2 = box_coords

                # --- Relative Pixel-Offsets für Roboter ---
                dx_px = center[0] - (self.img_width/2)
                dy_px = center[1] - (self.img_height/2)
                
                label_name = r.names[d["cls"]]
                p2 = (int(center[0] + 40 * math.cos(angle_rad)), int(center[1] + 40 * math.sin(angle_rad)))

                det = {
                    "label": label_name,
                    "box": box_coords,
                    "center": center,
                    "angle": angle_deg,
                    "match": False,
                    "score": 1.0, # Placeholder, will be fixed below
                    "pixel_data": { 'dx': dx_px, 'dy': dy_px, 'angle_deg': angle_deg },
                    "corners": d["corners"],
                    "p2": p2
                }
                detections.append(det)

        return frame, detections

    def cleanup(self):
        self.running = False
        if hasattr(self, 'cam_thread'):
            self.cam_thread.join(timeout=1.0)
        if hasattr(self, 'ai_thread'):
            self.ai_thread.join(timeout=2.0)
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()

if __name__ == "__main__":
    vision = VisionSystem()
    print("Starte Kamera-Stream... (Abbruch mit 'q')")
    try:
        while True:
            frame = vision.get_frame()
            if frame is not None:
                processed_frame, detections = vision.process_image(frame)
                cv2.imshow("Mac Vision Test", processed_frame)
                
                # Mit 'q' abbrechen
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        vision.cleanup()
        cv2.destroyAllWindows()