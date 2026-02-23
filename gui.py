import customtkinter as ctk
from PIL import Image, ImageTk
import cv2
from tkinter import filedialog 
import time # Neu importiert für Sicherheits-Timeouts

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class ModernGUI(ctk.CTk):
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
        self.btn_home.grid(row=2, column=0, padx=20, pady=10)

        # Trennlinie
        self.line = ctk.CTkFrame(self.frame_controls, height=2, fg_color="gray30")
        self.line.grid(row=3, column=0, padx=10, pady=20, sticky="ew")

        # --- AI Suche ---
        self.lbl_ai = ctk.CTkLabel(self.frame_controls, text="Visuelle Suche:", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_ai.grid(row=4, column=0, padx=20, pady=(0, 10))

        # Info Label für User
        self.lbl_hint = ctk.CTkLabel(self.frame_controls, text="Tipp: Rechtsklick im Video\nzum Auswählen!", text_color="gray70", font=ctk.CTkFont(size=11))
        self.lbl_hint.grid(row=5, column=0, padx=5, pady=(0, 5))

        self.btn_upload = ctk.CTkButton(self.frame_controls, text="📷 Bild hochladen", command=self.cmd_upload_image)
        self.btn_upload.grid(row=6, column=0, padx=20, pady=5)

        self.btn_clear = ctk.CTkButton(self.frame_controls, text="❌ Suche löschen", command=self.cmd_clear_search, fg_color="darkred")
        self.btn_clear.grid(row=7, column=0, padx=20, pady=5)

        # Kleines Vorschaubild (Initial leer)
        self.lbl_ref_img = ctk.CTkLabel(self.frame_controls, text="[Kein Bild]", width=120, height=120, fg_color="black", corner_radius=5)
        self.lbl_ref_img.grid(row=8, column=0, padx=20, pady=20)

        self.lbl_status = ctk.CTkLabel(self.frame_controls, text="Status: Bereit", text_color="gray")
        self.lbl_status.grid(row=10, column=0, padx=20, pady=50)

        # --- Rechte Seite: Video ---
        self.frame_video = ctk.CTkFrame(self)
        self.frame_video.grid(row=0, column=1, sticky="nswe", padx=10, pady=10)
        
        self.lbl_video = ctk.CTkLabel(self.frame_video, text="")
        self.lbl_video.pack(expand=True, fill="both")
        
        # Binds
        self.lbl_video.bind("<Button-1>", self.video_clicked)       # Linksklick
        self.lbl_video.bind("<Button-3>", self.on_right_click)      # Rechtsklick

        # --- Variablen ---
        self.current_detections = []
        self.last_pil_image = None
        self._current_ref_img = None 
        # NEU: Eine Liste, die Referenzen speichert, damit sie NIEMALS gelöscht werden (Fix für pyimage Error)
        self._img_trash_can = [] 
        
        self.update_video()

    def update_video(self):
        raw_frame = self.vision.get_frame()
        if raw_frame is None: 
            self.after(10, self.update_video)
            return

        processed_frame, detections = self.vision.process_image(raw_frame)
        self.current_detections = detections
        self.robot.publish_video_cloud(raw_frame)

        # Konvertierung für GUI
        img = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
        
        # Wir speichern das Bild IMMER ab für den Zugriff bei Klick
        self.last_pil_image = Image.fromarray(img)
        
        img_ctk = ctk.CTkImage(light_image=self.last_pil_image, dark_image=self.last_pil_image, size=(640, 480))
        self.lbl_video.configure(image=img_ctk)
        # Referenz halten!
        self._img_trash_can.append(img_ctk)
        if len(self._img_trash_can) > 5: self._img_trash_can.pop(0) # Speicher sauber halten

        self.after(30, self.update_video)

    def on_right_click(self, event):
        """Wählt das angeklickte Objekt als neue AI-Referenz aus."""
        if self.last_pil_image is None: 
            return

        clicked_obj = None
        # Prüfen, ob wir in eine Bounding Box geklickt haben
        for obj in self.current_detections:
            b = obj['box'] 
            if b[0] < event.x < b[2] and b[1] < event.y < b[3]:
                clicked_obj = obj
                break 
        
        if clicked_obj:
            print(f"🎯 Rechtsklick auf: {clicked_obj['label']} -> Crop...")
            self.lbl_status.configure(text="Analysiere...", text_color="cyan")

            # 1. Koordinaten berechnen
            x1, y1, x2, y2 = map(int, clicked_obj['box'])
            w, h = self.last_pil_image.size
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(w, x2); y2 = min(h, y2)

            if x2 <= x1 or y2 <= y1:
                print("❌ Fehler: Box ungültig")
                return

            # Crop erstellen - WICHTIG: copy() nutzen
            crop = self.last_pil_image.crop((x1, y1, x2, y2)).copy()

            # 2. Vision System updaten
            success = self.vision.set_reference_from_crop(crop)

            if success:
                print("✅ Vision OK. Aktualisiere GUI...")
                
                # --- FIX FÜR PYIMAGE ERROR ---
                # 1. Bild vorbereiten
                preview_display = crop.copy()
                
                # 2. Neues CTkImage erstellen
                new_ctk_img = ctk.CTkImage(
                    light_image=preview_display, 
                    dark_image=preview_display, 
                    size=(120, 120)
                )

                # 3. GUI zwingen, den alten Zustand zu vergessen
                self.lbl_ref_img.configure(image=None, text="")
                self.lbl_ref_img.update_idletasks() # Warten bis GUI gezeichnet hat

                # 4. Das neue Bild sicher speichern (in self._current_ref_img UND in der Müll-Liste)
                self._current_ref_img = new_ctk_img
                # Dieser Trick verhindert, dass Python das Bild löscht:
                self._img_trash_can.append(new_ctk_img) 
                
                # 5. Jetzt setzen
                try:
                    self.lbl_ref_img.configure(image=self._current_ref_img, text="")
                    self.lbl_status.configure(text=f"Suche: {clicked_obj['label']}", text_color="green")
                except Exception as e:
                    print(f"⚠️ GUI Fehler abgefangen: {e}")
                    # Notfall-Versuch
                    self.lbl_ref_img.configure(text="BILD FEHLER")
            else:
                self.lbl_status.configure(text="Fehler Vision", text_color="red")
        else:
            print("⚠️ Klick ins Leere")

    def cmd_upload_image(self):
        file_path = filedialog.askopenfilename(filetypes=[("Bilder", "*.jpg *.jpeg *.png *.bmp")])
        if file_path:
            print(f"Lade Bild: {file_path}")
            success = self.vision.set_reference_image(file_path)
            if success:
                self.lbl_status.configure(text="Suche läuft...", text_color="cyan")
                img = Image.open(file_path)
                
                self._current_ref_img = ctk.CTkImage(light_image=img, dark_image=img, size=(120, 120))
                # Auch hier sicherstellen, dass es nicht gelöscht wird
                self._img_trash_can.append(self._current_ref_img)
                
                self.lbl_ref_img.configure(image=self._current_ref_img, text="")
            else:
                self.lbl_status.configure(text="Fehler beim Bild!", text_color="red")

    def cmd_clear_search(self):
        print("🗑️ Lösche Suche...")
        self.vision.clear_reference()
        
        # 1. Variable leeren
        self._current_ref_img = None
        
        # 2. GUI leeren
        self.lbl_ref_img.configure(image=None, text="[Kein Bild]")
        self.lbl_status.configure(text="Suche beendet", text_color="gray")
        
        # 3. Update erzwingen
        self.lbl_ref_img.update_idletasks()

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