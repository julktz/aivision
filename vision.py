import os
# --- WICHTIG: Das hier verhindert den Absturz (Core Dump) bei 4K Kameras ---
os.environ["OPENCV_VIDEOIO_PRIORITY_LIST"] = "V4L2,FFMPEG,GSTREAMER"

import cv2
import numpy as np
import math
from ultralytics import YOLO
from collections import deque
from geometry_msgs.msg import Quaternion
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image

# --- KONSTANTEN ---
CAMERA_X = 0.50      
CAMERA_Y = 0.00
CAMERA_Z = 0.60
FOV_WIDTH_M = 0.30   
IMG_WIDTH = 640
OBJECT_Z_LEVEL = 0.02
SIMILARITY_THRESHOLD = 0.75

class VisionSystem:
    def __init__(self):
        print("👁️ Starte Vision System (Universal Modus)...")
        
        self.cap = None
        self.camera_ok = False

        # Wir suchen die Kamera auf allen Ports (0 bis 5)
        # Wir nutzen CAP_V4L2, weil das unter Linux am stabilsten ist
        for index in [0, 1, 2, 3, 4, 5]:
            try:
                # V4L2 erzwingen + Index nutzen
                temp_cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
                
                if temp_cap.isOpened():
                    # --- FIX: Auflösung begrenzen ---
                    # Egal ob 4K oder 1080p Webcam: Wir stellen sie auf 640x480.
                    # Das schont USB-Bandbreite und CPU.
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    
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

        self.meters_per_pixel = FOV_WIDTH_M / IMG_WIDTH
        
        # 1. YOLO
        # Lade dein selbst trainiertes OBB-Modell
        self.model = YOLO("best (3).pt")

        # 2. ResNet
        print("🧠 Lade ResNet18 für Feature-Matching...")
        self.resnet = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        self.feature_extractor = torch.nn.Sequential(*list(self.resnet.children())[:-1])
        self.feature_extractor.eval() 
        
        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        self.angle_history = deque(maxlen=5)
        self.reference_embedding = None 

    def set_reference_image(self, image_path):
        try:
            img = Image.open(image_path).convert('RGB')
            img_t = self.transform(img).unsqueeze(0) 
            with torch.no_grad():
                embedding = self.feature_extractor(img_t)
            self.reference_embedding = embedding.flatten()
            print("✅ Referenz-Bild geladen!")
            return True
        except Exception as e:
            print(f"❌ Fehler beim Laden: {e}")
            return False

    def set_reference_from_crop(self, crop_pil_image):
        try:
            safe_image = crop_pil_image.copy().convert('RGB')
            img_t = self.transform(safe_image).unsqueeze(0)
            with torch.no_grad():
                embedding = self.feature_extractor(img_t)
            self.reference_embedding = embedding.flatten()
            print("✅ Referenz per Video-Klick gesetzt!")
            return True
        except Exception as e:
            print(f"❌ Fehler bei Video-Referenz: {e}")
            return False
            
    def clear_reference(self):
        self.reference_embedding = None
        print("🗑️ Referenz gelöscht.")

    def get_frame(self):
        # Crash-Schutz
        if not self.camera_ok or self.cap is None:
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(dummy, "KEINE KAMERA", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return dummy

        ret, frame = self.cap.read()
        if not ret:
            print("⚠️ Kamera Signal weg!")
            self.camera_ok = False
            return self.get_frame() 
            
        return frame

    def euler_to_quaternion(self, roll_deg, pitch_deg, yaw_deg):
        roll = math.radians(roll_deg); pitch = math.radians(pitch_deg); yaw = math.radians(yaw_deg)
        qx = math.sin(roll/2) * math.cos(pitch/2) * math.cos(yaw/2) - math.cos(roll/2) * math.sin(pitch/2) * math.sin(yaw/2)
        qy = math.cos(roll/2) * math.sin(pitch/2) * math.cos(yaw/2) + math.sin(roll/2) * math.cos(pitch/2) * math.sin(yaw/2)
        qz = math.cos(roll/2) * math.cos(pitch/2) * math.sin(yaw/2) - math.sin(roll/2) * math.sin(pitch/2) * math.cos(yaw/2)
        qw = math.cos(roll/2) * math.cos(pitch/2) * math.cos(yaw/2) + math.sin(roll/2) * math.sin(pitch/2) * math.sin(yaw/2)
        return Quaternion(x=qx, y=qy, z=qz, w=qw)

    def process_image(self, frame):
        if not self.camera_ok:
            return frame, []

        # OBB-Modelle brauchen keine retina_masks mehr
        results = self.model(
    frame, 
    imgsz=640,   # Reduziert die Pixelanzahl, verdoppelt oft die FPS
    stream=True,    # Nutzt Generator-Modus für flüssigeres Video
    conf=0.9,  # Etwas niedriger für bessere Stabilität
    verbose=False
)
        detections = []
        frame_rgb_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) 

        for r in results:
            # NEU: Wir prüfen auf OBB statt auf Masken
            if r.obb is not None:
                for j in range(len(r.obb)):
                    # 1. Direkte Daten aus der KI holen (x_center, y_center, width, height, winkel_in_radiant)
                    xywhr = r.obb.xywhr[j].cpu().numpy()
                    cx, cy, w, h, angle_rad = xywhr
                    
                    center = (int(cx), int(cy))
                    smooth_angle = math.degrees(angle_rad) # Direkt von der KI!
                    
                    # 4 Eckpunkte der gedrehten Box holen (fürs Zeichnen)
                    corners = r.obb.xyxyxyxy[j].cpu().numpy()
                    
                    # Für die GUI und den Crop brauchen wir eine gerade Box (Min/Max der Ecken)
                    x_coords = corners[:, 0]
                    y_coords = corners[:, 1]
                    x1, x2 = int(np.min(x_coords)), int(np.max(x_coords))
                    y1, y2 = int(np.min(y_coords)), int(np.max(y_coords))
                    box_coords = [x1, y1, x2, y2]
                    
                    # --- 2. AI MATCHING (Bleibt komplett gleich!) ---
                    is_match = False
                    similarity_score = 0.0
                    
                    if self.reference_embedding is not None:
                        x1_c = max(0, x1); y1_c = max(0, y1); x2_c = min(IMG_WIDTH, x2); y2_c = min(480, y2)
                        if x2_c > x1_c and y2_c > y1_c:
                            crop = frame_rgb_pil.crop((x1_c, y1_c, x2_c, y2_c))
                            crop_t = self.transform(crop).unsqueeze(0)
                            with torch.no_grad():
                                curr_embedding = self.feature_extractor(crop_t).flatten()
                            
                            cos = torch.nn.CosineSimilarity(dim=0)
                            similarity_score = cos(self.reference_embedding, curr_embedding).item()
                            
                            if similarity_score > SIMILARITY_THRESHOLD:
                                is_match = True

                    # --- 3. Daten speichern ---
                    px = center[0] - (IMG_WIDTH/2); py = center[1] - 240
                    final_x = CAMERA_X + (-(py * self.meters_per_pixel))
                    final_y = CAMERA_Y + (-(px * self.meters_per_pixel))
                    
                    class_id = int(r.obb.cls[j].item())
                    label_name = r.names[class_id]
                    
                    det = {
                        "label": label_name,
                        "box": box_coords,
                        "center": center,
                        "angle": smooth_angle,
                        "match": is_match,
                        "score": similarity_score,
                        "target_data": { 'x': final_x, 'y': final_y, 'z': OBJECT_Z_LEVEL, 'ori': self.euler_to_quaternion(180, 0, smooth_angle) }
                    }
                    detections.append(det)
                    
                    # --- 4. Zeichnen ---
                    if self.reference_embedding is not None:
                        if is_match: 
                            color = (0, 0, 255) 
                            text = f"MATCH! ({similarity_score:.2f})"
                        else: 
                            color = (100, 100, 100) 
                            text = f"{label_name} ({similarity_score:.2f})"
                    else:
                        color = (0, 255, 0) 
                        text = f"{label_name}"

                    # Wir zeichnen jetzt die echte gedrehte Box!
                    pts = corners.reshape((-1, 1, 2)).astype(np.int32)
                    cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
                    
                    # Rote Linie für den Winkel
                    p2 = (int(center[0] + 40 * math.cos(angle_rad)), int(center[1] + 40 * math.sin(angle_rad)))
                    cv2.line(frame, center, p2, (0, 0, 255), 2)
                    
                    # Text über die Box
                    cv2.putText(frame, text, (int(corners[0][0]), int(corners[0][1])-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        return frame, detections

    def cleanup(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()