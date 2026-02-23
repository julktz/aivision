#!/usr/bin/env python3

print("🔵 Starte Robot Vision (Clean PCA Mode)...")



import rclpy

from rclpy.node import Node

from rclpy.action import ActionClient

from geometry_msgs.msg import Pose, PoseStamped, Quaternion, TransformStamped

from sensor_msgs.msg import Image, JointState, CameraInfo, PointCloud2, PointField

from cv_bridge import CvBridge

from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

from moveit_msgs.action import MoveGroup, ExecuteTrajectory

from moveit_msgs.msg import RobotState, Constraints, PositionConstraint, OrientationConstraint, BoundingVolume, JointConstraint

from moveit_msgs.srv import GetCartesianPath, GetPositionFK

from moveit_msgs.msg import AttachedCollisionObject, CollisionObject

from ultralytics import YOLO

import cv2

import numpy as np

import time

import math

import struct

import copy

from collections import deque



# --- SETUP ---

CAMERA_X = 0.50      

CAMERA_Y = 0.00

CAMERA_Z = 0.60

FOV_WIDTH_M = 0.30   

IMG_WIDTH = 640



OBJECT_Z_LEVEL = 0.02   

HOVER_DISTANCE = 0.15   



# Winkel-Stabilisierung (Durchschnitt über 5 Frames)

ANGLE_HISTORY = 5 



class AngleFilter:

    def __init__(self, history_size=5):

        self.history = deque(maxlen=history_size)

    def update(self, angle):

        self.history.append(angle)

        return sum(self.history) / len(self.history)



class IndustrialRobot(Node):

    def __init__(self):

        super().__init__('industrial_robot_node')

        self.bridge = CvBridge()

        self.angle_filter = AngleFilter(ANGLE_HISTORY)

        

        # Modell laden (Standard YOLO11 Segmentation)

        print("🧠 Lade YOLO11-Segmentation...")

        try: 

            self.model = YOLO("yolo11n-seg.pt")

        except: 

            print("⚠️ yolo11n-seg nicht gefunden, lade Fallback...")

            self.model = YOLO("yolov8n-seg.pt")



        self._move_group_client = ActionClient(self, MoveGroup, 'move_action')

        self._cartesian_client = self.create_client(GetCartesianPath, 'compute_cartesian_path')

        self._execute_client = ActionClient(self, ExecuteTrajectory, 'execute_trajectory')

        self._fk_client = self.create_client(GetPositionFK, 'compute_fk')

        

        self.sub_joints = self.create_subscription(JointState, '/joint_states', self.joint_cb, 10)

        self.img_pub = self.create_publisher(Image, 'video_frames', 10)

        self.cam_info_pub = self.create_publisher(CameraInfo, 'camera_info', 10)

        self.target_pub = self.create_publisher(PoseStamped, '/target_pose', 10)

        self.cloud_pub = self.create_publisher(PointCloud2, 'video_cloud', 10)

        self.att_collision_pub = self.create_publisher(AttachedCollisionObject, '/attached_collision_object', 10)



        self.tf_static_broadcaster = StaticTransformBroadcaster(self)

        self.publish_static_tf()



        self.cap = cv2.VideoCapture(0)

        self.meters_per_pixel = FOV_WIDTH_M / IMG_WIDTH

        self.orientation = self.euler_to_quaternion(180, 0, 0)

        self.focal_length = (IMG_WIDTH * CAMERA_Z) / FOV_WIDTH_M

        

        self.joint_state = None

        self.locked_target = None

        self.motion_executed = False 

        self.last_attempt_time = 0 

        

        # Liste für Klick-Logik

        self.current_detections = [] 

        

        cv2.namedWindow("Industrial Control")

        cv2.setMouseCallback("Industrial Control", self.mouse_callback)



        print("⏳ Warte auf Roboter...")

        while self.joint_state is None and rclpy.ok():

            rclpy.spin_once(self, timeout_sec=0.1)

            time.sleep(0.1)

        

        try: self.remove_virtual_gripper()

        except: pass

        

        print("\n✅ BEREIT.")

        print("   [Maus-Klick] -> Objekt als Ziel setzen")

        print("   [G] -> Greifen")

        print("   [R] -> Reset (Ziel löschen & Home)")



    def remove_virtual_gripper(self):

        time.sleep(1.0)

        gripper = AttachedCollisionObject()

        gripper.link_name = "tool0"

        gripper.object.id = "virtual_gripper"

        gripper.object.operation = CollisionObject.REMOVE

        self.att_collision_pub.publish(gripper)



    def euler_to_quaternion(self, roll_deg, pitch_deg, yaw_deg):

        roll = math.radians(roll_deg); pitch = math.radians(pitch_deg); yaw = math.radians(yaw_deg)

        qx = math.sin(roll/2) * math.cos(pitch/2) * math.cos(yaw/2) - math.cos(roll/2) * math.sin(pitch/2) * math.sin(yaw/2)

        qy = math.cos(roll/2) * math.sin(pitch/2) * math.cos(yaw/2) + math.sin(roll/2) * math.cos(pitch/2) * math.sin(yaw/2)

        qz = math.cos(roll/2) * math.cos(pitch/2) * math.sin(yaw/2) - math.sin(roll/2) * math.sin(pitch/2) * math.cos(yaw/2)

        qw = math.cos(roll/2) * math.cos(pitch/2) * math.cos(yaw/2) + math.sin(roll/2) * math.sin(pitch/2) * math.sin(yaw/2)

        return Quaternion(x=qx, y=qy, z=qz, w=qw)



    def get_orientation_pca(self, contour):

        """Berechnet stabile Rotation mittels PCA"""

        sz = len(contour)

        data_pts = np.empty((sz, 2), dtype=np.float64)

        for i in range(data_pts.shape[0]):

            data_pts[i,0] = contour[i,0,0]

            data_pts[i,1] = contour[i,0,1]

        mean = np.empty((0))

        mean, eigenvectors, eigenvalues = cv2.PCACompute2(data_pts, mean)

        angle = math.atan2(eigenvectors[0,1], eigenvectors[0,0])

        angle_deg = math.degrees(angle)

        

        # Filterung gegen Zittern

        smooth_angle = self.angle_filter.update(angle_deg)

        

        return self.euler_to_quaternion(180, 0, smooth_angle), smooth_angle, (int(mean[0,0]), int(mean[0,1]))



    def publish_static_tf(self):

        t = TransformStamped()

        t.header.stamp = self.get_clock().now().to_msg()

        t.header.frame_id = "base_link"; t.child_frame_id = "camera_optical_frame"

        t.transform.translation.x = float(CAMERA_X); t.transform.translation.y = float(CAMERA_Y); t.transform.translation.z = float(CAMERA_Z)

        t.transform.rotation.x = 0.707; t.transform.rotation.y = -0.707; t.transform.rotation.z = 0.0; t.transform.rotation.w = 0.0

        self.tf_static_broadcaster.sendTransform(t)



    def publish_video_cloud(self, frame):

        scale = 0.25; small_frame = cv2.resize(frame, (0,0), fx=scale, fy=scale); height, width = small_frame.shape[:2]

        center_x = width / 2; center_y = height / 2; mpp_scaled = self.meters_per_pixel / scale

        buffer = bytearray()

        for v in range(height):

            for u in range(width):

                b, g, r = small_frame[v, u]

                rgb = struct.unpack('I', struct.pack('BBBB', b, g, r, 255))[0]

                rgb_float = struct.unpack('f', struct.pack('I', rgb))[0]

                dx_px = u - center_x; dy_px = v - center_y 

                x_world = CAMERA_X - (dy_px * mpp_scaled); y_world = CAMERA_Y - (dx_px * mpp_scaled); z_world = 0.005 

                buffer.extend(struct.pack('ffff', x_world, y_world, z_world, rgb_float))

        msg = PointCloud2(); msg.header.stamp = self.get_clock().now().to_msg(); msg.header.frame_id = "base_link"

        msg.height = 1; msg.width = width * height

        msg.fields = [PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1), PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1), PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1), PointField(name='rgb', offset=12, datatype=PointField.FLOAT32, count=1)]

        msg.is_bigendian = False; msg.point_step = 16; msg.row_step = msg.point_step * msg.width; msg.is_dense = True; msg.data = buffer

        self.cloud_pub.publish(msg)



    def joint_cb(self, msg): self.joint_state = msg

    

    def mouse_callback(self, event, x, y, flags, param):

        if event == cv2.EVENT_LBUTTONDOWN:

            print(f"🖱️ Klick bei {x}, {y}...")

            

            clicked_obj = None

            # Atomare Kopie der Liste nutzen (verhindert 'Klick ins Leere')

            local_list = list(self.current_detections)

            

            for obj in local_list:

                x1, y1, x2, y2 = obj['box']

                if x > x1 and x < x2 and y > y1 and y < y2:

                    clicked_obj = obj

                    break 

            

            if clicked_obj:

                print(f"🎯 ZIEL GESETZT: {clicked_obj['label']}")

                self.locked_target = clicked_obj['pose'] 

                self.motion_executed = False; self.last_attempt_time = 0 

            else:

                print("❌ Kein Objekt getroffen.")



    def get_current_pose(self):

        for _ in range(5): rclpy.spin_once(self, timeout_sec=0.01)

        req = GetPositionFK.Request(); req.header.frame_id = "base_link"; req.fk_link_names = ["tool0"]; req.robot_state.joint_state = self.joint_state

        future = self._fk_client.call_async(req); rclpy.spin_until_future_complete(self, future)

        if future.result() and future.result().pose_stamped: return future.result().pose_stamped[0].pose

        return None



    def execute_trajectory(self, trajectory):

        goal = ExecuteTrajectory.Goal(); goal.trajectory = trajectory

        self._execute_client.wait_for_server(); future = self._execute_client.send_goal_async(goal)

        while not future.done(): rclpy.spin_once(self, timeout_sec=0.01); time.sleep(0.01)

        return True



    def move_smart(self, target_pose):

        rclpy.spin_once(self, timeout_sec=0.1)

        try:

            req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"

            req.start_state = RobotState(); req.start_state.joint_state = self.joint_state

            req.waypoints = [target_pose]; req.max_step = 0.01; req.jump_threshold = 0.0; req.avoid_collisions = True

            future = self._cartesian_client.call_async(req); rclpy.spin_until_future_complete(self, future); res = future.result()

            if res and res.fraction > 0.90: return self.execute_trajectory(res.solution)

        except: pass

        return self.move_ptp(target_pose)



    def move_ptp(self, pose):

        try:

            goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"

            constraints = Constraints(); pos = PositionConstraint(); pos.header.frame_id = "base_link"; pos.link_name = "tool0"; pos.weight = 1.0

            box = BoundingVolume(); prim = SolidPrimitive(); prim.type = SolidPrimitive.BOX; prim.dimensions = [0.01, 0.01, 0.01]

            box.primitives.append(prim); p = PoseStamped(); p.header.frame_id = "base_link"; p.pose = pose

            box.primitive_poses.append(p.pose); pos.constraint_region = box; constraints.position_constraints.append(pos)

            ori = OrientationConstraint(); ori.header.frame_id = "base_link"; ori.link_name = "tool0"; ori.orientation = pose.orientation 

            ori.absolute_x_axis_tolerance = 0.5; ori.absolute_y_axis_tolerance = 0.5; ori.absolute_z_axis_tolerance = 3.14; ori.weight = 1.0

            constraints.orientation_constraints.append(ori); goal.request.goal_constraints.append(constraints)

            self._move_group_client.wait_for_server(); self._move_group_client.send_goal_async(goal)

            return True

        except: return False



    def move_linear_relative(self, dz):

        try:

            time.sleep(0.2); rclpy.spin_once(self, timeout_sec=0.1)

            start = self.get_current_pose(); 

            if start: 

                target = copy.deepcopy(start); target.position.z += dz

                req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"

                req.start_state = RobotState(); req.start_state.joint_state = self.joint_state

                req.waypoints = [target]; req.max_step = 0.01; req.jump_threshold = 0.0

                future = self._cartesian_client.call_async(req); rclpy.spin_until_future_complete(self, future); res = future.result()

                if res.fraction > 0.9: 

                    self.execute_trajectory(res.solution); return True 

                else: print(f"❌ Pfad blockiert!"); return False 

        except Exception as e: print(f"Fehler: {e}"); return False

        return False



    def go_home(self):

        goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"

        target_joints = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0]

        constraints = Constraints(); names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]

        for i, n in enumerate(names):

            jc = JointConstraint(); jc.joint_name = n; jc.position = target_joints[i]; jc.tolerance_above = 0.01; jc.tolerance_below = 0.01; jc.weight = 1.0

            constraints.joint_constraints.append(jc)

        goal.request.goal_constraints.append(constraints)

        self._move_group_client.wait_for_server(); self._move_group_client.send_goal_async(goal)



    def execute_pick_cycle(self):

        if not self.motion_executed: print("⛔ STOP: Erst hinfahren!"); return

        print(f"✊ Greife! Fahre {HOVER_DISTANCE}m runter...")

        if self.move_linear_relative(-HOVER_DISTANCE):

            time.sleep(0.5); print("🛫 Fahre wieder hoch..."); rclpy.spin_once(self, timeout_sec=0.1)

            self.move_linear_relative(HOVER_DISTANCE)

        else: print("⚠️ Abbruch: Konnte nicht sicher runterfahren.")



    def publish_cam_info(self):

        now = self.get_clock().now().to_msg()

        ci = CameraInfo(); ci.header.stamp = now; ci.header.frame_id = "camera_optical_frame"

        ci.width = IMG_WIDTH; ci.height = 480

        ci.k = [self.focal_length, 0.0, IMG_WIDTH/2, 0.0, self.focal_length, 240.0, 0.0, 0.0, 1.0]

        ci.p = [self.focal_length, 0.0, IMG_WIDTH/2, 0.0, 0.0, self.focal_length, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]

        self.cam_info_pub.publish(ci)



    def loop(self):

        while rclpy.ok():

            ret, frame = self.cap.read()

            if not ret: break

            

            self.publish_cam_info()

            img_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")

            img_msg.header.stamp = self.get_clock().now().to_msg(); img_msg.header.frame_id = "camera_optical_frame"

            self.img_pub.publish(img_msg); self.publish_video_cloud(frame)



            temp_detections = [] 

            

            # Detektion (Retina Masks für PCA wichtig)

            # Conf 0.25 (Standard)

            results = self.model(frame, verbose=False, retina_masks=True, conf=0.25)

            

            for i, r in enumerate(results):

                # Wenn wir Masken haben (für PCA Rotation)

                if r.masks is not None:

                    for j, box in enumerate(r.boxes):

                        mask = r.masks.data[j].cpu().numpy().astype('uint8')

                        mask = cv2.resize(mask, (IMG_WIDTH, 480)) 

                        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                        if not contours: continue

                        cnt = max(contours, key=cv2.contourArea)

                        

                        # PCA Rotation berechnen (Stabil!)

                        rotation_quat, angle_deg, center = self.get_orientation_pca(cnt)

                        

                        px = center[0] - (IMG_WIDTH/2); py = center[1] - 240

                        ox = -(py * self.meters_per_pixel); oy = -(px * self.meters_per_pixel)

                        pose = Pose(); pose.orientation = rotation_quat

                        pose.position.x = float(CAMERA_X + ox); pose.position.y = float(CAMERA_Y + oy)

                        pose.position.z = float(OBJECT_Z_LEVEL)



                        cls_id = int(box.cls[0]); label = r.names[cls_id]

                        box_coords = box.xyxy[0].tolist()

                        

                        obj_data = {'label': label, 'conf': float(box.conf[0]), 'box': box_coords, 'pose': pose}

                        temp_detections.append(obj_data)

                        

                        # Zeichnen

                        color = (0, 255, 0)

                        if self.locked_target is not None:

                            lx = self.locked_target.position.x; ly = self.locked_target.position.y

                            if abs(lx - pose.position.x) < 0.01: color = (0, 0, 255)



                        p1 = center

                        p2 = (int(center[0] + 40 * math.cos(math.radians(angle_deg))), int(center[1] + 40 * math.sin(math.radians(angle_deg))))

                        cv2.drawContours(frame, [cnt], 0, (0, 255, 255), 2)

                        cv2.line(frame, p1, p2, (0, 0, 255), 2)

                        cv2.rectangle(frame, (int(box_coords[0]), int(box_coords[1])), (int(box_coords[2]), int(box_coords[3])), color, 2)

                        cv2.putText(frame, f"{label} {angle_deg:.0f}", (int(box_coords[0]), int(box_coords[1])-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                

                # Fallback ohne Maske (nur Box, keine Rotation)

                else:

                    for box in r.boxes:

                        x1, y1, x2, y2 = map(int, box.xyxy[0])

                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

                        cv2.putText(frame, "NO MASK", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)



            # Erst JETZT die Liste aktualisieren (gegen Klick-Fehler)

            self.current_detections = temp_detections



            if self.locked_target is not None:

                p = PoseStamped(); p.header.frame_id = "base_link"; p.header.stamp = self.get_clock().now().to_msg(); p.pose = self.locked_target; self.target_pub.publish(p)

                if not self.motion_executed:

                    if time.time() - self.last_attempt_time > 1.0: 

                        cv2.putText(frame, "FAHRE ZU ZIEL...", (30, 450), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

                        hover_pose = copy.deepcopy(self.locked_target); hover_pose.position.z += HOVER_DISTANCE

                        self.move_smart(hover_pose); self.last_attempt_time = time.time(); self.motion_executed = True 

                else: cv2.putText(frame, "BEREIT [G]", (30, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)



            cv2.imshow("Industrial Control", frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord('g'): self.execute_pick_cycle()

            elif key == ord('r'): self.locked_target = None; self.motion_executed = False; self.go_home()

            elif key == ord('q'): break



def main():

    rclpy.init()

    node = IndustrialRobot()

    try: node.loop()

    except KeyboardInterrupt: pass

    finally: node.cap.release(); cv2.destroyAllWindows(); rclpy.shutdown()



if __name__ == "__main__": main()
