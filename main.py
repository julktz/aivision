#!/usr/bin/env python3
import rclpy
import threading
from vision import VisionSystem
from robot import RobotController
from gui import ModernGUI

def main():
    rclpy.init()
    vision = VisionSystem()
    robot = RobotController()
    
    # ROS Thread
    thread = threading.Thread(target=rclpy.spin, args=(robot,), daemon=True)
    thread.start()

    def handle_click(x, y, detections):
        # x,y sind GUI Maus Koordinaten. Da das Video skaliert sein könnte,
        # ist die Trefferquote hier "so lala".
        # Besser: Wir schauen, ob die Maus im Bildbereich war.
        
        # Vereinfacht: Wir nehmen das erste erkannte Objekt zum Testen
        # oder prüfen Box-Koordinaten grob.
        for obj in detections:
             # Einfacher Check: Ist der Klick innerhalb der Bounding Box?
             b = obj['box']
             if b[0] < x < b[2] and b[1] < y < b[3]:
                 print(f"🎯 Sende Befehl an Roboter: {obj['label']}")
                 # Hier rufen wir die neue Funktion auf
                 robot.trigger_move_to(obj['target_data'])
                 break

    app = ModernGUI(vision, robot, handle_click)
    
    try: app.mainloop()
    finally:
        vision.cleanup()
        rclpy.shutdown()

if __name__ == "__main__": main()