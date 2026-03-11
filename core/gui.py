import customtkinter as ctk
from PIL import Image, ImageTk
import cv2
from tkinter import filedialog 
import time
import json
import os
import subprocess

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class ModernGUI(ctk.CTk):
    def update_video(self):
        if not self.is_video_running:
            return

        raw_frame = self.vision.get_frame()
        if raw_frame is None: 
            if self.is_video_running:
                self.after(10, self.update_video)
            return

        clean_frame = raw_frame.copy()

        processed_frame, detections = self.vision.process_image(raw_frame)
        self.current_detections = detections
        self.robot.publish_camera_image(clean_frame)
        self.robot.publish_all_targets_rviz(detections)

        # Update Stats
        obj_count = len(detections)
        self.lbl_count.configure(text=f"Objekte: {obj_count}")

        # Draw crosshair in center for visual reference (optional)
        h, w = processed_frame.shape[:2]
        cv2.drawMarker(processed_frame, (w//2, h//2), (255, 255, 255), cv2.MARKER_CROSS, 20, 1)

        # Konvertierung für GUI
        img = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
        
        # Wir speichern das Bild IMMER ab für den Zugriff bei Klick
        self.last_pil_image = Image.fromarray(img)
        
        img_ctk = ctk.CTkImage(light_image=self.last_pil_image, dark_image=self.last_pil_image, size=(640, 480))
        self.lbl_video.configure(image=img_ctk)
        # Referenz halten!
        self._img_trash_can.append(img_ctk)
        if len(self._img_trash_can) > 5: self._img_trash_can.pop(0) # Speicher sauber halten

        self.fps_frames += 1
        self.after(30, self.update_video)

    def update_fps_display(self):
        if self.is_video_running:
            self.lbl_fps.configure(text=f"FPS: {self.fps_frames}")
        self.fps_frames = 0
        self.after(1000, self.update_fps_display)

    def __init__(self, vision_system, robot_controller, on_click_callback):
        super().__init__()
        
        self.vision = vision_system
        self.robot = robot_controller
        self.on_click_callback = on_click_callback
        
        self.title("Robot Vision AI Control")
        self.geometry("1100x700")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Linke Seite: Controls ---
        self.frame_controls = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.frame_controls.grid(row=0, column=0, sticky="nswe")
        
        self.lbl_title = ctk.CTkLabel(self.frame_controls, text="AI Steuerung", font=ctk.CTkFont(size=20, weight="bold"))
        self.lbl_title.grid(row=0, column=0, padx=20, pady=20)

        # Buttons Robot
        self.btn_pick = ctk.CTkButton(self.frame_controls, text="GREIFEN (G)", command=self.cmd_pick, fg_color="green")
        self.btn_pick.grid(row=1, column=0, padx=20, pady=10)

        self.btn_home = ctk.CTkButton(self.frame_controls, text="HOME (R)", command=self.cmd_home, fg_color="gray")
        self.btn_home.grid(row=2, column=0, padx=20, pady=5)

        self.btn_set_home = ctk.CTkButton(self.frame_controls, text="HOME SPEICHERN", command=self.cmd_set_home, fg_color="#336699")
        self.btn_set_home.grid(row=3, column=0, padx=20, pady=5)

        self.btn_settings = ctk.CTkButton(self.frame_controls, text="⚙️ EINSTELLUNGEN", command=self.open_settings, fg_color="#555555")
        self.btn_settings.grid(row=4, column=0, padx=20, pady=5)

        self.line = ctk.CTkFrame(self.frame_controls, height=2, fg_color="gray30")
        self.line.grid(row=5, column=0, padx=10, pady=10, sticky="ew")

        # --- Speed Control ---
        self.lbl_speed_title = ctk.CTkLabel(self.frame_controls, text="Geschwindigkeit:", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_speed_title.grid(row=6, column=0, padx=20, pady=(10, 0), sticky="w")
        
        self.speed_frame = ctk.CTkFrame(self.frame_controls, fg_color="transparent")
        self.speed_frame.grid(row=7, column=0, padx=20, pady=5, sticky="ew")
        
        self.slider_speed = ctk.CTkSlider(self.speed_frame, from_=0.05, to=1.0, number_of_steps=19, command=self.cmd_speed_changed)
        self.slider_speed.set(self.robot.speed_scale)
        self.slider_speed.pack(side="left", fill="x", expand=True)
        
        self.lbl_speed_val = ctk.CTkLabel(self.speed_frame, text=f"{int(self.robot.speed_scale*100)}%", width=40)
        self.lbl_speed_val.pack(side="right", padx=(5, 0))

        # --- Gripping Depth Control ---
        self.lbl_depth_title = ctk.CTkLabel(self.frame_controls, text="Greiftiefe (m):", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_depth_title.grid(row=8, column=0, padx=20, pady=(10, 0), sticky="w")
        
        self.depth_frame = ctk.CTkFrame(self.frame_controls, fg_color="transparent")
        self.depth_frame.grid(row=9, column=0, padx=20, pady=5, sticky="ew")
        
        self.slider_depth = ctk.CTkSlider(self.depth_frame, from_=0.0, to=0.5, number_of_steps=50, command=self.cmd_depth_changed)
        self.slider_depth.set(self.robot.grip_depth)
        self.slider_depth.pack(side="left", fill="x", expand=True)
        
        self.lbl_depth_val = ctk.CTkLabel(self.depth_frame, text=f"{int(self.robot.grip_depth*100)}cm", width=40)
        self.lbl_depth_val.pack(side="right", padx=(5, 0))

        # --- Hover Height (Approach) Control ---
        self.lbl_approach_title = ctk.CTkLabel(self.frame_controls, text="Hover Höhe (m):", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_approach_title.grid(row=10, column=0, padx=20, pady=(10, 0), sticky="w")
        
        self.approach_frame = ctk.CTkFrame(self.frame_controls, fg_color="transparent")
        self.approach_frame.grid(row=11, column=0, padx=20, pady=5, sticky="ew")
        
        self.slider_approach = ctk.CTkSlider(self.approach_frame, from_=0.05, to=0.5, number_of_steps=45, command=self.cmd_approach_changed)
        self.slider_approach.set(self.robot.approach_height)
        self.slider_approach.pack(side="left", fill="x", expand=True)
        
        self.lbl_approach_val = ctk.CTkLabel(self.approach_frame, text=f"{int(self.robot.approach_height*100)}cm", width=40)
        self.lbl_approach_val.pack(side="right", padx=(5, 0))

        # --- Live Stats ---
        self.stats_frame = ctk.CTkFrame(self.frame_controls, fg_color="transparent")
        self.stats_frame.grid(row=12, column=0, padx=20, pady=10, sticky="w") # Fix: grid call was missing but pack was used internally
        
        self.lbl_fps = ctk.CTkLabel(self.stats_frame, text="FPS: --", font=ctk.CTkFont(size=14, weight="bold"), text_color="#00FFAA")
        self.lbl_fps.pack(anchor="w", pady=2)

        self.lbl_count = ctk.CTkLabel(self.stats_frame, text="Objekte: 0", font=ctk.CTkFont(size=14))
        self.lbl_count.pack(anchor="w", pady=2)

        # Trennlinie
        self.line2 = ctk.CTkFrame(self.frame_controls, height=2, fg_color="gray30")
        self.line2.grid(row=13, column=0, padx=10, pady=10, sticky="ew")
        
        # Info Box (Log)
        self.lbl_status = ctk.CTkLabel(self.frame_controls, text="Status: Bereit", text_color="gray")
        self.lbl_status.grid(row=14, column=0, padx=20, pady=15)

        # --- Rechte Seite: Video ---
        self.frame_video = ctk.CTkFrame(self)
        self.frame_video.grid(row=0, column=1, sticky="nswe", padx=10, pady=10)
        
        self.lbl_video = ctk.CTkLabel(self.frame_video, text="")
        self.lbl_video.pack(expand=True, fill="both")
        
        # Binds
        self.lbl_video.bind("<Button-1>", self.video_clicked)       # Linksklick

        # --- Variablen ---
        self.current_detections = []
        self.last_pil_image = None
        self._current_ref_img = None 
        # NEU: Eine Liste, die Referenzen speichert, damit sie NIEMALS gelöscht werden (Fix für pyimage Error)
        self._img_trash_can = [] 
        
        # Settings Overlay (Hidden initially)
        self.settings_overlay = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=10)
        self.settings_open = False
        
        self.fps_frames = 0
        self.current_fps = 0
        self.is_video_running = True # NEU: Absolute Kontrolle über den Loop
        
        # Start the video loop with a small delay for stability
        self.after(100, self.update_video)
        self.after(1000, self.update_fps_display)


    def video_clicked(self, event):
        # Linksklick Logik
        for obj in self.current_detections:
            b = obj['box']
            if b[0] < event.x < b[2] and b[1] < event.y < b[3]:
                print(f"👆 Linksklick auf: {obj['label']}")
                self.on_click_callback(event.x, event.y, [obj]) 
                return
        self.on_click_callback(event.x, event.y, self.current_detections)

    def cmd_pick(self):
        self.lbl_status.configure(text="Greife...", text_color="orange")
        self.robot.execute_pick_cycle()
        self.lbl_status.configure(text="Bereit", text_color="gray")

    def cmd_home(self):
        self.robot.go_home()
        self.lbl_status.configure(text="Home", text_color="gray")

    def cmd_set_home(self):
        success = self.robot.record_home_position()
        if success:
            self.lbl_status.configure(text="Home gespeichert", text_color="green")
        else:
            self.lbl_status.configure(text="Fehler Joints", text_color="red")

    def cmd_speed_changed(self, value):
        self.robot.speed_scale = float(value)
        self.lbl_speed_val.configure(text=f"{int(value*100)}%")

    def cmd_depth_changed(self, value):
        self.robot.grip_depth = float(value)
        self.lbl_depth_val.configure(text=f"{int(value*100)}cm")

    def cmd_approach_changed(self, value):
        self.robot.approach_height = float(value)
        self.lbl_approach_val.configure(text=f"{int(value*100)}cm")

    def close_settings(self):
        self.settings_open = False
        if hasattr(self, 'settings_window') and self.settings_window:
            self.settings_window.destroy()
            self.settings_window = None
        
        self.vision.paused = False # Resume AI
        self.is_video_running = True # Resume Video
        self.after(100, self.update_video) # Start loop again

    def open_settings(self):
        """Erste Phase: Alles anhalten."""
        if self.settings_open: return
        self.settings_open = True
        
        self.is_video_running = False # STOP GUI LOOP
        self.vision.paused = True     # STOP AI THREAD
        
        # Wir geben dem System Zeit zum "Abkühlen"
        self.after(300, self._build_settings_ui)

    def _build_settings_ui(self):
        """Zweite Phase: Toplevel Fenster öffnen."""
        if not self.settings_open: return
        
        # Falls die KI immer noch läuft, warten wir noch etwas länger
        if hasattr(self.vision, 'is_ai_running') and self.vision.is_ai_running:
            self.after(100, self._build_settings_ui)
            return

        # CTkToplevel ist auf Linux oft stabiler als verschachtelte Frames
        self.settings_window = ctk.CTkToplevel(self)
        self.settings_window.title("Kamera & Kalibrierung Einstellungen")
        self.settings_window.geometry("600x800")
        self.settings_window.protocol("WM_DELETE_WINDOW", self.close_settings)
        self.settings_window.transient(self) # On top of main
        
        # Inner scrollable frame
        settings_win = ctk.CTkScrollableFrame(self.settings_window, fg_color="transparent")
        settings_win.pack(fill="both", expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(settings_win, text="🔧 Kamera & Kalibrierung", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=10)
        
        # Lade aktuelle Config
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "camera_config.json")
        data = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = json.load(f)
                
        intrin = data.get("intrinsics")
        if not isinstance(intrin, dict): intrin = {}
        extrin = data.get("extrinsics")
        if not isinstance(extrin, dict): extrin = {}
        trans = extrin.get("translation")
        if not isinstance(trans, dict): trans = {}
        rot = extrin.get("rotation_euler")
        if not isinstance(rot, dict): rot = {}

        # --- Extrinsics (Eye In Hand) ---
        ctk.CTkLabel(settings_win, text="Mount Frame (z.B. wrist_2_link):").pack(pady=(10, 0))
        entry_mount = ctk.CTkEntry(settings_win)
        entry_mount.insert(0, str(extrin.get("mount_frame", "wrist_2_link")))
        entry_mount.pack(pady=(0, 10))
        
        ctk.CTkLabel(settings_win, text="Translation [m] (X, Y, Z):").pack(pady=(10, 0))
        frame_trans = ctk.CTkFrame(settings_win)
        frame_trans.pack(pady=5, fill="x", padx=20)
        entry_x = ctk.CTkEntry(frame_trans, width=80); entry_x.insert(0, str(trans.get("x", 0.0))); entry_x.pack(side="left", padx=10, expand=True)
        entry_y = ctk.CTkEntry(frame_trans, width=80); entry_y.insert(0, str(trans.get("y", 0.08))); entry_y.pack(side="left", padx=10, expand=True)
        entry_z = ctk.CTkEntry(frame_trans, width=80); entry_z.insert(0, str(trans.get("z", 0.05))); entry_z.pack(side="left", padx=10, expand=True)

        ctk.CTkLabel(settings_win, text="Rotation [deg] (Roll, Pitch, Yaw):").pack(pady=(10, 0))
        frame_rot = ctk.CTkFrame(settings_win)
        frame_rot.pack(pady=5, fill="x", padx=20)
        entry_r = ctk.CTkEntry(frame_rot, width=80); entry_r.insert(0, str(rot.get("roll", 0.0))); entry_r.pack(side="left", padx=10, expand=True)
        entry_p = ctk.CTkEntry(frame_rot, width=80); entry_p.insert(0, str(rot.get("pitch", -90.0))); entry_p.pack(side="left", padx=10, expand=True)
        entry_yaw = ctk.CTkEntry(frame_rot, width=80); entry_yaw.insert(0, str(rot.get("yaw", 0.0))); entry_yaw.pack(side="left", padx=10, expand=True)

        # --- Intrinsics ---
        ctk.CTkLabel(settings_win, text="Focal Length [px] (fx):").pack(pady=(20, 0))
        entry_focal = ctk.CTkEntry(settings_win)
        entry_focal.insert(0, str(intrin.get("focal_length", 662.75)))
        entry_focal.pack(pady=(0, 10))
        
        frame_res = ctk.CTkFrame(settings_win)
        frame_res.pack(pady=5, fill="x", padx=20)
        ctk.CTkLabel(frame_res, text="Resolution W x H:").pack()
        entry_w = ctk.CTkEntry(frame_res, width=80); entry_w.insert(0, str(intrin.get("image_width", 640))); entry_w.pack(side="left", padx=10, expand=True)
        entry_h = ctk.CTkEntry(frame_res, width=80); entry_h.insert(0, str(intrin.get("image_height", 480))); entry_h.pack(side="right", padx=10, expand=True)

        ctk.CTkLabel(settings_win, text="Kalibrierung Checkerboard (SpaltenxZeilen, Kantenlänger in m):").pack(pady=(20, 0))
        frame_cal = ctk.CTkFrame(settings_win)
        frame_cal.pack(pady=5, fill="x", padx=20)
        entry_cal_size = ctk.CTkEntry(frame_cal, width=80); entry_cal_size.insert(0, "9x6"); entry_cal_size.pack(side="left", padx=10, expand=True)
        entry_cal_square = ctk.CTkEntry(frame_cal, width=80); entry_cal_square.insert(0, "0.024"); entry_cal_square.pack(side="right", padx=10, expand=True)

        def save_settings():
            try:
                # Lade Daten frisch, um Typprobleme zu vermeiden
                with open(config_path, 'r') as f:
                    current_data = json.load(f)
                
                # Extrinsics sichern
                current_data["extrinsics"] = {
                    "mount_frame": entry_mount.get(),
                    "translation": {
                        "x": float(entry_x.get().replace(',', '.')), 
                        "y": float(entry_y.get().replace(',', '.')), 
                        "z": float(entry_z.get().replace(',', '.'))
                    },
                    "rotation_euler": {
                        "roll": float(entry_r.get().replace(',', '.')), 
                        "pitch": float(entry_p.get().replace(',', '.')), 
                        "yaw": float(entry_yaw.get().replace(',', '.'))
                    }
                }
                
                # Intrinsics sichern ohne Matrix/Distortion zu löschen
                if "intrinsics" not in current_data:
                    current_data["intrinsics"] = {}
                
                current_data["intrinsics"]["focal_length"] = float(entry_focal.get().replace(',', '.'))
                current_data["intrinsics"]["image_width"] = int(entry_w.get())
                current_data["intrinsics"]["image_height"] = int(entry_h.get())
                
                with open(config_path, 'w') as f:
                    json.dump(current_data, f, indent=4)
                    
                print("💾 Einstellungen erfolgreich gespeichert!")
                self.robot.reload_camera_config()
                self.vision.reload_camera_config()
                self.close_settings()
            except ValueError as e:
                print(f"⚠️ Eingabefehler: Bitte nur Zahlen verwenden! ({e})")
            except Exception as e:
                print(f"❌ Fehler beim Speichern: {e}")

        def run_calibration():
            print("📸 Starte offizielle ROS Intrinsics Kalibrierung...")
            
            size_val = entry_cal_size.get()
            square_val = entry_cal_square.get()
            
            calib_cmd = [
                "ros2", "run", "camera_calibration", "cameracalibrator",
                "--size", size_val,
                "--square", square_val,
                "--ros-args", 
                "-r", "image:=/video_frames",
                "-r", "camera:=/camera_info"
            ]
            
            print(f"Starte Befehl: {' '.join(calib_cmd)}")
            subprocess.Popen(calib_cmd)
            self.close_settings()

        ctk.CTkButton(settings_win, text="💾 Speichern & Anwenden", command=save_settings, fg_color="green", height=40).pack(pady=20)
        ctk.CTkButton(settings_win, text="📸 Schachbrett-Kalibrierung starten", command=run_calibration, fg_color="#336699", height=40).pack(pady=5)
        ctk.CTkButton(settings_win, text="❌ Abbrechen", command=self.close_settings, fg_color="darkred", height=40).pack(pady=10)