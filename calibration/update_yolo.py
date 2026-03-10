import os

with open('/home/ubuntu22/aivision/yolo_robot.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)

# Let's verify the lines to be replaced
# The loop starts at line 551 (1-indexed) which is lines[550]

start_idx = 550
end_idx = 648
print("First line to replace:", lines[start_idx].strip())
print("Last line to replace:", lines[end_idx-1].strip())
print("Line after:", lines[end_idx].strip())

replacement = """            # Detektion (OBB Modelle wie yolo26)
            results = self.model(frame, verbose=False, conf=0.85)

            for i, r in enumerate(results):
                if r.obb is not None:
                    for j in range(len(r.obb)):
                        xywhr = r.obb.xywhr[j].cpu().numpy()
                        cx, cy, w, h, angle_rad = xywhr
                        center = (int(cx), int(cy))
                        smooth_angle = math.degrees(angle_rad)
                        
                        corners = r.obb.xyxyxyxy[j].cpu().numpy()
                        x_coords = corners[:, 0]
                        y_coords = corners[:, 1]
                        x1, x2 = int(np.min(x_coords)), int(np.max(x_coords))
                        y1, y2 = int(np.min(y_coords)), int(np.max(y_coords))
                        box_coords = [x1, y1, x2, y2]
                        
                        # Stabilisiere den Winkel
                        smooth_angle = self.angle_filter.update(smooth_angle)
                        
                        # Ziel-Koordinaten berechnen
                        px = center[0] - (IMG_WIDTH/2)
                        py = center[1] - 240
                        ox = -(py * self.meters_per_pixel)
                        oy = -(px * self.meters_per_pixel)
                        
                        pose = Pose()
                        pose.orientation = self.euler_to_quaternion(180, 0, smooth_angle)
                        pose.position.x = float(CAMERA_X + ox)
                        pose.position.y = float(CAMERA_Y + oy)
                        pose.position.z = float(OBJECT_Z_LEVEL)
                        
                        cls_id = int(r.obb.cls[j].item())
                        label = r.names[cls_id]
                        conf = float(r.obb.conf[j].item())
                        
                        obj_data = {'label': label, 'conf': conf, 'box': box_coords, 'pose': pose}
                        temp_detections.append(obj_data)
                        
                        # Zeichnen
                        color = (0, 255, 0)
                        if self.locked_target is not None:
                            lx = self.locked_target.position.x
                            ly = self.locked_target.position.y
                            if abs(lx - pose.position.x) < 0.01: 
                                color = (0, 0, 255)
                                
                        pts = corners.reshape((-1, 1, 2)).astype(np.int32)
                        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
                        
                        p2 = (int(center[0] + 40 * math.cos(angle_rad)), int(center[1] + 40 * math.sin(angle_rad)))
                        cv2.line(frame, center, p2, (0, 0, 255), 2)
                        cv2.putText(frame, f"{label} {smooth_angle:.0f}", (int(corners[0][0]), int(corners[0][1])-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                        
"""

lines[start_idx:end_idx] = [replacement]

with open('/home/ubuntu22/aivision/yolo_robot.py', 'w') as f:
    f.writelines(lines)

print("Done replacing.")
