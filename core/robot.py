import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import Pose, PoseStamped, Quaternion, TransformStamped, Point, Vector3Stamped
from sensor_msgs.msg import JointState, PointCloud2, PointField, CameraInfo, Image
from shape_msgs.msg import SolidPrimitive
from moveit_msgs.msg import RobotState, Constraints, PositionConstraint, OrientationConstraint, BoundingVolume, JointConstraint
from moveit_msgs.action import MoveGroup, ExecuteTrajectory
from moveit_msgs.srv import GetCartesianPath, GetPositionFK, GetPositionIK
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster
from moveit_msgs.msg import PlanningScene, CollisionObject
from shape_msgs.msg import SolidPrimitive
from visualization_msgs.msg import Marker, MarkerArray
import math
import time
import copy
import struct
from tf2_ros import Buffer, TransformListener, StaticTransformBroadcaster
import tf2_geometry_msgs
import json
import os
import numpy as np

# --- KONSTANTEN ---
IMG_WIDTH = 640
OBJECT_Z_LEVEL = 0.02

class RobotController(Node):
    def __init__(self):
        super().__init__('industrial_robot_node')
        
        # --- CLIENTS ---
        self._move_group_client = ActionClient(self, MoveGroup, 'move_action')
        self._cartesian_client = self.create_client(GetCartesianPath, 'compute_cartesian_path')
        self._execute_client = ActionClient(self, ExecuteTrajectory, 'execute_trajectory')
        self._fk_client = self.create_client(GetPositionFK, 'compute_fk')
        self._ik_client = self.create_client(GetPositionIK, 'compute_ik')
        
        # --- PUBLISHER ---
        self.sub_joints = self.create_subscription(JointState, '/joint_states', self.joint_cb, 10)
        
        # Publisher für RViz und Kalibrierung (Ohne fehleranfällige PointCloud)
        self.image_pub = self.create_publisher(Image, '/video_frames', 10)
        self.camera_info_pub = self.create_publisher(CameraInfo, '/camera_info', 10)
        
        self.target_pub = self.create_publisher(PoseStamped, '/target_pose', 10)
        self.marker_pub = self.create_publisher(MarkerArray, '/target_markers', 10)
        self.frustum_pub = self.create_publisher(Marker, '/camera_frustum', 10)
        self.cloud_pub = self.create_publisher(PointCloud2, '/video_cloud', 10)
        
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        
        # --- PLANNING SCENE (TABLE) ---
        self.scene_pub = self.create_publisher(PlanningScene, '/planning_scene', 10)
        self.table_added = False
        self.create_timer(1.0, self.check_publish_table)
        
        # --- STATE ---
        self.joint_state = None
        self.camera_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "camera_config.json")
        self.reload_camera_config() # Loads focal_length, mount_frame, translation, rotation
        
        self.current_target_msg = None # Speichert den aktuellen Pfeil für RViz
        self.speed_scale = 0.1 # 10% - Teach Pendant muss auf 100% stehen!
        self.grip_depth = 0.15 # 15cm - Standard Greif-Hub (m)
        self.approach_height = 0.15 # 15cm - Standard Anfahr-Höhe (m)
        self.pending_grip_yaw = 0.0 # Zuletzt geklickte Rotation (deg)
        
        # Timer ersetzt den alten while-Loop für konstantes Publishing
        self.create_timer(0.1, self.publish_target_loop)

        # --- HOME CONFIG ---
        self.config_path = os.path.join(os.path.dirname(__file__), "..", "config", "home_config.json")
        self.home_joints = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0] # Default
        self.load_home_config()

        self.static_broadcaster = StaticTransformBroadcaster(self)
        self.publish_static_tf()
        print("🤖 Warte auf Roboter-Verbindung...")

    def check_publish_table(self):
        if not self.table_added and self.scene_pub.get_subscription_count() > 0:
            self.add_table_to_scene()
            self.table_added = True

    def add_table_to_scene(self):
        """Fügt eine virtuelle Tischplatte in MoveIt hinzu, damit der Roboter nicht durch den Tisch fährt."""
        from vision import OBJECT_Z_LEVEL
        p = PlanningScene()
        p.is_diff = True
        
        o = CollisionObject()
        o.header.frame_id = "base_link"
        o.id = "table"
        
        # Tischplatte (ca. 2x2 Meter, 1mm dünn)
        s = SolidPrimitive()
        s.type = SolidPrimitive.BOX
        s.dimensions = [2.0, 2.0, 0.001]
        
        pose = Pose()
        pose.position.z = OBJECT_Z_LEVEL - 0.01 # 1cm unter die Greif-Höhe setzen
        o.primitives.append(s)
        o.primitive_poses.append(pose)
        o.operation = CollisionObject.ADD
        
        p.world.collision_objects.append(o)
        self.scene_pub.publish(p)
        print("🧱 Kollisions-Tisch (MoveIt) hinzugefügt.")

    def joint_cb(self, msg):
        self.joint_state = msg

    def publish_target_loop(self):
        """Sorgt dafür, dass der Pfeil in RViz sichtbar bleibt"""
        if self.current_target_msg is not None:
            # Zeitstempel aktualisieren, damit RViz nicht meckert
            self.current_target_msg.header.stamp = self.get_clock().now().to_msg()
            self.target_pub.publish(self.current_target_msg)

    def load_home_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                    if "home_joints" in data and len(data["home_joints"]) == 6:
                        self.home_joints = data["home_joints"]
                        print(f"🏠 Home-Position geladen: {self.home_joints}")
            except Exception as e:
                print(f"⚠️ Fehler beim Laden der Home-Config: {e}")

    def record_home_position(self):
        """Speichert die aktuelle Roboter-Pose als neue Home-Position."""
        if self.joint_state is None:
            print("❌ Fehler: Keine Joint-Daten verfügbar.")
            return False
            
        try:
            # Wir müssen sicherstellen, dass die Gelenke in der richtigen Reihenfolge sind:
            # shoulder_pan, shoulder_lift, elbow, wrist_1, wrist_2, wrist_3
            names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
            current_vals = []
            for name in names:
                idx = self.joint_state.name.index(name)
                current_vals.append(self.joint_state.position[idx])
            
            self.home_joints = current_vals
            with open(self.config_path, 'w') as f:
                json.dump({"home_joints": self.home_joints}, f)
            
            print(f"🏠 Neue Home-Position gespeichert: {self.home_joints}")
            return True
        except Exception as e:
            print(f"❌ Fehler beim Speichern der Home-Position: {e}")
            return False

    def reload_camera_config(self):
        """Lädt die Kamera-Konfiguration (Intrinsics, Extrinsics) dynamisch neu."""
        try:
            if os.path.exists(self.camera_config_path):
                with open(self.camera_config_path, 'r') as f:
                    data = json.load(f)
                    
                self.focal_length = data.get("intrinsics", {}).get("focal_length", 662.75)
                
                # Volle Matrix und Verzerrung laden für CameraInfo
                intrin = data.get("intrinsics", {})
                self.cam_matrix = intrin.get("camera_matrix", [
                    [self.focal_length, 0.0, 320.0],
                    [0.0, self.focal_length, 240.0],
                    [0.0, 0.0, 1.0]
                ])
                if not isinstance(self.cam_matrix, list):
                    self.cam_matrix = [[self.focal_length, 0.0, 320.0], [0.0, self.focal_length, 240.0], [0.0, 0.0, 1.0]]
                    
                self.dist_coeffs = intrin.get("distortion_coefficients", [0.0, 0.0, 0.0, 0.0, 0.0])
                if not isinstance(self.dist_coeffs, list):
                    self.dist_coeffs = [0.0, 0.0, 0.0, 0.0, 0.0]
                    
                self.img_width = int(intrin.get("image_width", 640))
                self.img_height = int(intrin.get("image_height", 480))
                
                ext = data.get("extrinsics", {})
                
                self.cam_mount_frame = ext.get("mount_frame", "wrist_2_link")
                
                trans = ext.get("translation", {})
                self.cam_tx = trans.get("x", 0.0)
                self.cam_ty = trans.get("y", 0.08)
                self.cam_tz = trans.get("z", 0.05)
                
                rot = ext.get("rotation_euler", {})
                self.cam_roll = rot.get("roll", 0.0)
                self.cam_pitch = rot.get("pitch", -90.0)
                self.cam_yaw = rot.get("yaw", 0.0)
                
                # HEURISTIK: Falls Werte sehr klein sind (z.B. < 6.3), könnten es Bogenmaß (Radians) sein
                # Wir warnen den Nutzer, da unser System Grad erwartet.
                if all(abs(val) < 6.3 for val in [self.cam_roll, self.cam_pitch, self.cam_yaw]) and any(abs(val) > 0.01 for val in [self.cam_roll, self.cam_pitch, self.cam_yaw]):
                     print("⚠️ WARNUNG: Kamera-Rotationen sehen nach Radians (Bogenmaß) aus!")
                     print("   Das System erwartet GRAD (z.B. -90 statt -1.57).")
                
                print(f"📸 Kamera-Konfiguration erfolgreich geladen: fl={self.focal_length}, mount={self.cam_mount_frame}")
                
                # Falls der Broadcaster schon existiert, aktualisieren wir den TF Baum
                if hasattr(self, 'static_broadcaster'):
                    self.publish_static_tf()
                    
                self.init_point_cloud_grid()
                return True
        except Exception as e:
            print(f"⚠️ Fehler beim Laden von {self.camera_config_path}: {e}")
        
        # Fallback values
        self.focal_length = 662.75
        self.cam_matrix = [[662.75, 0.0, 320.0], [0.0, 662.75, 240.0], [0.0, 0.0, 1.0]]
        self.dist_coeffs = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.img_width, self.img_height = 640, 480
        
        self.cam_mount_frame = "wrist_2_link"
        self.cam_tx, self.cam_ty, self.cam_tz = 0.0, 0.08, 0.05
        self.cam_roll, self.cam_pitch, self.cam_yaw = 0.0, -90.0, 0.0
        self.init_point_cloud_grid()
        return False

    def init_point_cloud_grid(self):
        """Pre-calculates the 3D grid for the PointCloud2 projection to save CPU."""
        self.pc_downsample = 8 # 640/8 = 80, 480/8 = 60 (very lightweight)
        if hasattr(self, 'img_width') and hasattr(self, 'img_height'):
            self.pc_width = self.img_width // self.pc_downsample
            self.pc_height = self.img_height // self.pc_downsample
        else:
            self.pc_width = 640 // self.pc_downsample
            self.pc_height = 480 // self.pc_downsample
            
        d = 0.25 # project closer so it doesn't clip into the table
        fx = self.focal_length; fy = self.focal_length
        cx = self.cam_matrix[0][2]; cy = self.cam_matrix[1][2]
        
        u_coords, v_coords = np.meshgrid(np.arange(self.pc_width) * self.pc_downsample, np.arange(self.pc_height) * self.pc_downsample)
        
        x_coords = (u_coords - cx) * d / fx
        y_coords = (v_coords - cy) * d / fy
        z_coords = np.full_like(x_coords, d)
        
        self.pc_dtype = np.dtype([
            ('x', np.float32),
            ('y', np.float32),
            ('z', np.float32),
            ('rgb', np.uint32)
        ])
        
        self.pc_data = np.empty((self.pc_width * self.pc_height,), dtype=self.pc_dtype)
        self.pc_data['x'] = x_coords.flatten()
        self.pc_data['y'] = y_coords.flatten()
        self.pc_data['z'] = z_coords.flatten()
        
        self.pc_fields = [
            PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name='rgb', offset=12, datatype=PointField.FLOAT32, count=1),
        ]

    def publish_static_tf(self):
        # Statische Transformation zum mount_frame (z.B. wrist_2_link oder tool0)
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = self.cam_mount_frame 
        t.child_frame_id = "camera_optical_frame"
        
        t.transform.translation.x = float(self.cam_tx)
        t.transform.translation.y = float(self.cam_ty)
        t.transform.translation.z = float(self.cam_tz)
        
        t.transform.rotation = self.euler_to_quaternion(self.cam_roll, self.cam_pitch, self.cam_yaw)
        
        self.static_broadcaster.sendTransform(t)
        print(f"📡 Kamera fest an '{self.cam_mount_frame}' montiert.")

    def publish_camera_image(self, frame):
        # Sende Standard Image & CameraInfo (für Kalibrierung)
        now = self.get_clock().now().to_msg()
        frame_id = "camera_optical_frame"
        
        # Image (manuell konvertieren um OpenCV/cv_bridge/PyTorch Segfaults in ROS2 zu vermeiden)
        img_msg = Image()
        img_msg.header.stamp = now
        img_msg.header.frame_id = frame_id
        img_msg.height = frame.shape[0]
        img_msg.width = frame.shape[1]
        img_msg.encoding = "bgr8"
        img_msg.is_bigendian = False
        img_msg.step = frame.shape[1] * 3
        img_msg.data = frame.tobytes()
        self.image_pub.publish(img_msg)
        
        # CameraInfo
        info_msg = CameraInfo()
        info_msg.header.stamp = now
        info_msg.header.frame_id = frame_id
        info_msg.height = self.img_height
        info_msg.width = self.img_width
        info_msg.distortion_model = "plumb_bob"
        info_msg.d = self.dist_coeffs
        # K Matrix (3x3 abgeflacht)
        info_msg.k = [self.cam_matrix[0][0], self.cam_matrix[0][1], self.cam_matrix[0][2],
                      self.cam_matrix[1][0], self.cam_matrix[1][1], self.cam_matrix[1][2],
                      self.cam_matrix[2][0], self.cam_matrix[2][1], self.cam_matrix[2][2]]
        # P Matrix (3x4 Projektion, wir nehmen K + 0 Translation)
        info_msg.p = [self.cam_matrix[0][0], self.cam_matrix[0][1], self.cam_matrix[0][2], 0.0,
                      self.cam_matrix[1][0], self.cam_matrix[1][1], self.cam_matrix[1][2], 0.0,
                      0.0, 0.0, 1.0, 0.0]
        self.camera_info_pub.publish(info_msg)
        self.camera_info_pub.publish(info_msg)
        
        self.publish_camera_frustum(now)
        self.publish_camera_pointcloud(frame, now)

    def publish_camera_pointcloud(self, frame, stamp):
        try:
            if not hasattr(self, 'pc_data'):
                return
            
            # Fast downsample
            pixels = frame[::self.pc_downsample, ::self.pc_downsample]
            
            # Ensure shape matches expected grid (rare edge case if resolution changes mid-flight)
            if pixels.shape[0] != self.pc_height or pixels.shape[1] != self.pc_width:
                img_h, img_w = pixels.shape[:2]
                pixels = pixels[:min(img_h, self.pc_height), :min(img_w, self.pc_width)]
                # pad if too small
                padded = np.zeros((self.pc_height, self.pc_width, 3), dtype=np.uint8)
                padded[:pixels.shape[0], :pixels.shape[1]] = pixels
                pixels = padded
                
            r = pixels[:,:,2].astype(np.uint32)
            g = pixels[:,:,1].astype(np.uint32)
            b = pixels[:,:,0].astype(np.uint32)
            
            rgb = (r << 16) | (g << 8) | b
            self.pc_data['rgb'] = rgb.flatten()
            
            cloud_msg = PointCloud2()
            cloud_msg.header.stamp = stamp
            cloud_msg.header.frame_id = "camera_optical_frame"
            cloud_msg.height = self.pc_height
            cloud_msg.width = self.pc_width
            cloud_msg.fields = self.pc_fields
            cloud_msg.is_bigendian = False
            cloud_msg.point_step = 16
            cloud_msg.row_step = 16 * self.pc_width
            cloud_msg.data = self.pc_data.tobytes()
            cloud_msg.is_dense = True
            
            self.cloud_pub.publish(cloud_msg)
        except Exception as e:
            pass

    def publish_camera_frustum(self, stamp):
        try:
            m = Marker()
            m.header.frame_id = "camera_optical_frame"
            m.header.stamp = stamp
            m.ns = "frustum"
            m.id = 0
            m.type = Marker.LINE_LIST
            m.action = Marker.ADD
            m.scale.x = 0.002 # Linien-Dicke
            
            m.color.r = 1.0; m.color.g = 0.0; m.color.b = 1.0; m.color.a = 0.6 # Magenta
            
            d = 0.25 # 15cm weit, passend zur PointCloud
            fx = self.focal_length; fy = self.focal_length
            cx = self.cam_matrix[0][2]; cy = self.cam_matrix[1][2]
            
            x1 = (0 - cx) * d / fx; y1 = (0 - cy) * d / fy
            x2 = (self.img_width - cx) * d / fx; y2 = (0 - cy) * d / fy
            x3 = (self.img_width - cx) * d / fx; y3 = (self.img_height - cy) * d / fy
            x4 = (0 - cx) * d / fx; y4 = (self.img_height - cy) * d / fy
            
            p0 = Point(); p0.x = 0.0; p0.y = 0.0; p0.z = 0.0
            p1 = Point(); p1.x = float(x1); p1.y = float(y1); p1.z = float(d)
            p2 = Point(); p2.x = float(x2); p2.y = float(y2); p2.z = float(d)
            p3 = Point(); p3.x = float(x3); p3.y = float(y3); p3.z = float(d)
            p4 = Point(); p4.y = float(y4); p4.x = float(x4); p4.z = float(d)
            
            # Rays from lens
            m.points.extend([p0, p1, p0, p2, p0, p3, p0, p4])
            # Rectangle connecting ends
            m.points.extend([p1, p2, p2, p3, p3, p4, p4, p1])
            
            m.pose.orientation.w = 1.0
            self.frustum_pub.publish(m)
        except Exception as e:
            pass

    def execute_trajectory(self, trajectory):
        """Führt eine Trajektorie aus."""
        goal = ExecuteTrajectory.Goal(); goal.trajectory = trajectory
        self._execute_client.wait_for_server()
        self._execute_client.send_goal_async(goal)
        return True

    def wait_for_future(self, future, timeout=5.0):
        """Hilfsfunktion: Wartet sicher auf ein ROS-Future mit Timeout."""
        start_time = time.time()
        while rclpy.ok() and not future.done():
            if time.time() - start_time > timeout:
                print(f"⚠️ Timeout ({timeout}s) beim Warten auf Server-Antwort!")
                return False
            time.sleep(0.01)
        return future.done()

    def move_ptp(self, pose):
        """PTP Bewegung - MoveIt plant UND führt direkt aus."""
        try:
            print(f"DEBUG: PTP Target Pos: {pose.position.x:.3f}, {pose.position.y:.3f}, {pose.position.z:.3f}")
            
            goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"
            constraints = Constraints()
            pos = PositionConstraint(); pos.header.frame_id = "base_link"; pos.link_name = "tool0"; pos.weight = 1.0
            box = BoundingVolume(); prim = SolidPrimitive(); prim.type = SolidPrimitive.BOX; prim.dimensions = [0.01, 0.01, 0.01]
            box.primitives.append(prim); p = PoseStamped(); p.header.frame_id = "base_link"; p.pose = pose
            box.primitive_poses.append(p.pose); pos.constraint_region = box; constraints.position_constraints.append(pos)
            ori = OrientationConstraint(); ori.header.frame_id = "base_link"; ori.link_name = "tool0"; ori.orientation = pose.orientation 
            ori.absolute_x_axis_tolerance = 0.1; ori.absolute_y_axis_tolerance = 0.1; ori.absolute_z_axis_tolerance = 0.1; ori.weight = 1.0
            constraints.orientation_constraints.append(ori); goal.request.goal_constraints.append(constraints)
            
            goal.request.max_velocity_scaling_factor = self.speed_scale
            goal.request.max_acceleration_scaling_factor = self.speed_scale
            goal.request.start_state.is_diff = True
            
            self._move_group_client.wait_for_server()
            self._move_group_client.send_goal_async(goal)
            return True
        except Exception as e: 
            print(f"❌ Fehler in move_ptp: {e}")
            return False

    def move_ptp_joints(self, joint_values):
        """PTP Bewegung auf Gelenk-Ziele (Synchron)."""
        try:
            goal = MoveGroup.Goal()
            goal.request.group_name = "ur_manipulator"
            constraints = Constraints()
            joint_names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", 
                           "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
            for name, val in zip(joint_names, joint_values):
                jc = JointConstraint()
                jc.joint_name = name; jc.position = val
                jc.tolerance_above = 0.001; jc.tolerance_below = 0.001; jc.weight = 1.0
                constraints.joint_constraints.append(jc)
            goal.request.goal_constraints.append(constraints)
            goal.request.max_velocity_scaling_factor = self.speed_scale
            goal.request.max_acceleration_scaling_factor = self.speed_scale
            
            self._move_group_client.wait_for_server()
            future = self._move_group_client.send_goal_async(goal)
            if not self.wait_for_future(future): return False
            
            handle = future.result()
            if not handle.accepted: return False
            
            res_future = handle.get_result_async()
            if not self.wait_for_future(res_future, timeout=10.0): return False
            return True
        except Exception as e:
            print(f"❌ Fehler in move_ptp_joints: {e}")
            return False

    def scale_trajectory_time(self, trajectory, scale):
        """
        Streckt die Zeitstempel einer Trajektorie um den Faktor 1/scale.
        GetCartesianPath hat in ROS2 Humble kein max_velocity_scaling_factor,
        deshalb verlangsamen wir manuell über die Zeitachse.
        Velocity/Acceleration-Felder bleiben UNVERÄNDERT (MoveIt setzt sie konsistent).
        """
        if scale <= 0 or scale >= 1.0:
            return trajectory
        time_factor = 1.0 / scale
        for point in trajectory.joint_trajectory.points:
            total_ns = (point.time_from_start.sec * 1_000_000_000 + point.time_from_start.nanosec)
            new_ns = int(total_ns * time_factor)
            point.time_from_start.sec = new_ns // 1_000_000_000
            point.time_from_start.nanosec = new_ns % 1_000_000_000
            # Velocities konsistent mitskalieren
            if point.velocities:
                point.velocities = [v * scale for v in point.velocities]
            if point.accelerations:
                point.accelerations = [a * scale * scale for a in point.accelerations]
        print(f"⏱️ Cartesian-Bahn auf {int(scale*100)}% Speed skaliert")
        return trajectory

    def move_smart(self, target_pose):
        """Cartesian Bewegung - GetCartesianPath + Zeitskalierung + Ausführung."""
        try:
            if self.joint_state and hasattr(self.joint_state, 'name'):
                try:
                    idx = self.joint_state.name.index("shoulder_pan_joint")
                    print(f"DEBUG: Smart Start Joint1: {self.joint_state.position[idx]:.4f}")
                except: pass
            print(f"DEBUG: Smart Target Pos: {target_pose.position.x:.3f}, {target_pose.position.y:.3f}, {target_pose.position.z:.3f}")
            
            req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"
            req.start_state.is_diff = True
            req.waypoints = [target_pose]; req.max_step = 0.01; req.jump_threshold = 0.0; req.avoid_collisions = True
            
            future = self._cartesian_client.call_async(req)
            start = time.time()
            while not future.done():
                time.sleep(0.01)
                if time.time() - start > 0.5: break
            
            if future.done():
                res = future.result()
                if res and res.fraction > 0.90: 
                    print(f"✅ Lineare Bahn gefunden ({res.fraction*100:.1f}%) - Führe aus...")
                    scaled = self.scale_trajectory_time(res.solution, self.speed_scale)
                    return self.execute_trajectory(scaled)
                else:
                    print(f"⚠️ Lineare Bahn fehlgeschlagen (nur {res.fraction*100 if res else 0:.1f}%) - Abbruch.")
                    return False
        except Exception as e: 
            print(f"❌ Fehler in move_smart: {e}")
        return False

    def move_linear_relative(self, dz):
        if self.joint_state is None:
            print("⚠️ Linear Move: Kein JointState!")
            return False
            
        # FK Abfrage (vereinfacht für Thread-Safety)
        req = GetPositionFK.Request(); req.header.frame_id = "base_link"; req.fk_link_names = ["tool0"]; req.robot_state.joint_state = self.joint_state
        future = self._fk_client.call_async(req)
        while not future.done(): time.sleep(0.01)
        
        if future.result():
            start = future.result().pose_stamped[0].pose
            target = copy.deepcopy(start); target.position.z += dz
            
            req = GetCartesianPath.Request(); req.header.frame_id = "base_link"; req.group_name = "ur_manipulator"
            req.start_state = RobotState(); req.start_state.joint_state = self.joint_state
            req.waypoints = [target]; req.max_step = 0.01; req.jump_threshold = 0.0
            
            future = self._cartesian_client.call_async(req)
            while not future.done(): time.sleep(0.01)
            
            res = future.result()
            if res.fraction > 0.9:
                scaled = self.scale_trajectory_time(res.solution, self.speed_scale)
                self.execute_trajectory(scaled)
                return True
        return False

    def execute_pick_cycle(self):
        """Führt den Greif-Zyklus aus: Rotation -> Runter -> Warten -> Hoch."""
        try:
            # 1. Erst jetzt auf die Ziel-Rotation drehen (SYNCHRON)
            print(f"🔄 Rotiere Greifer auf {self.pending_grip_yaw:.1f}° (Synchron)...")
            
            # Aktuelle Pose holen
            req_fk = GetPositionFK.Request(); req_fk.header.frame_id = "base_link"; req_fk.fk_link_names = ["tool0"]; req_fk.robot_state.joint_state = self.joint_state
            future_fk = self._fk_client.call_async(req_fk)
            if not self.wait_for_future(future_fk): return # Fehler beim Warten
            
            if future_fk.result():
                current_pose = future_fk.result().pose_stamped[0].pose
                
                # IK für Ziel-Orientation (festes XYZ)
                req_ik = GetPositionIK.Request()
                req_ik.ik_request.group_name = "ur_manipulator"; req_ik.ik_request.ik_link_name = "tool0"; req_ik.ik_request.avoid_collisions = True
                req_ik.ik_request.pose_stamped.header.frame_id = "base_link"
                req_ik.ik_request.pose_stamped.pose.position = current_pose.position
                req_ik.ik_request.pose_stamped.pose.orientation = self.euler_to_quaternion(180.0, 0.0, self.pending_grip_yaw)
                
                future_ik = self._ik_client.call_async(req_ik)
                if not self.wait_for_future(future_ik): return
                res_ik = future_ik.result()
                
                if res_ik and res_ik.error_code.val == 1:
                    print("✅ IK für Rotation gefunden. Starte Joint-Move...")
                    if self.move_ptp_joints(res_ik.solution.joint_state.position):
                        print("✅ Rotation beendet.")
                    else:
                        print("⚠️ Rotation fehlgeschlagen.")
                else:
                    print("⚠️ Keine IK für Rotation gefunden.")
            
            # 2. Linear Greifen
            print(f"✊ Greife! Fahre {self.grip_depth}m runter...")
            if self.move_linear_relative(-self.grip_depth):
                time.sleep(0.5)
                print("🛫 Fahre wieder hoch...")
                self.move_linear_relative(self.grip_depth)
        except Exception as e:
            print(f"❌ Fehler im Pick-Cycle: {e}")

    def trigger_move_to(self, pixel_data):
        try:
            now = rclpy.time.Time()
            transform = self.tf_buffer.lookup_transform("base_link", "camera_optical_frame", now, rclpy.duration.Duration(seconds=1.0))
            
            # Aktuelle TCP Position und Orientierung merken
            current_tool_rot = None
            try:
                t_tool = self.tf_buffer.lookup_transform("base_link", "tool0", now, rclpy.duration.Duration(seconds=0.1))
                print(f"DEBUG: Current tool0 Pos: {t_tool.transform.translation.x:.3f}, {t_tool.transform.translation.y:.3f}, {t_tool.transform.translation.z:.3f}")
                current_tool_rot = t_tool.transform.rotation
            except Exception as e:
                print(f"DEBUG: Could not lookup tool0: {e}. Falling back to default orientation.")
            
            # --- ROBUSTE PROJEKTION (Ray-Plane Intersection) ---
            # Wir berechnen einen Strahl von der Kameralinse durch den Pixel
            # Kamerakoordinaten: X=rechts, Y=unten, Z=vorwärts (in die Linse)
            vx_cam = (pixel_data['dx']) / self.focal_length
            vy_cam = (pixel_data['dy']) / self.focal_length
            vz_cam = 1.0
            
            # Strahl-Punkt (Linsenmitte) in Welt-Koordinaten
            p0_world = np.array([
                transform.transform.translation.x,
                transform.transform.translation.y,
                transform.transform.translation.z
            ])
            
            # Strahl-Richtung in Welt-Koordinaten transformieren
            q = transform.transform.rotation
            # Quaternion zu Rotationsmatrix (vereinfacht)
            # Wir nutzen tf2_geometry_msgs für die Rotation des Vektors
            v_msg = Vector3Stamped()
            v_msg.header.frame_id = "camera_optical_frame"
            v_msg.vector.x = float(vx_cam); v_msg.vector.y = float(vy_cam); v_msg.vector.z = float(vz_cam)
            
            v_world_msg = tf2_geometry_msgs.do_transform_vector3(v_msg, transform)
            v_world = np.array([v_world_msg.vector.x, v_world_msg.vector.y, v_world_msg.vector.z])
            v_world = v_world / np.linalg.norm(v_world) # Normalisieren
            
            print(f"DEBUG: -----------------------------------------")
            print(f"DEBUG: PIXEL DATA : dx={pixel_data['dx']:.1f}, dy={pixel_data['dy']:.1f} (focal={self.focal_length:.1f})")
            print(f"DEBUG: CAM RAY    : vx={vx_cam:.4f}, vy={vy_cam:.4f}, vz={vz_cam:.4f}")
            print(f"DEBUG: CAM POS (W): x={p0_world[0]:.4f}, y={p0_world[1]:.4f}, z={p0_world[2]:.4f}")
            print(f"DEBUG: RAY DIR (W): x={v_world[0]:.4f}, y={v_world[1]:.4f}, z={v_world[2]:.4f}")
            print(f"DEBUG: -----------------------------------------")
            
            # Schnittpunkt mit Tisch-Ebene (Z = OBJECT_Z_LEVEL)
            
            # SICHERHEITS-CHECKS
            MAX_REACH = 0.85 # Meter (UR5e hat ca. 85cm Reichweite, wir bleiben sicher)
            MIN_VERTICAL_RAY = 0.05 # Mindest-Neigung des Strahls nach unten
            
            if abs(v_world[2]) < MIN_VERTICAL_RAY:
                print(f"⚠️ Strahl ist zu flach (Vz={v_world[2]:.3f}), Punkt läge im Unendlichen!")
                return
                
            t = (OBJECT_Z_LEVEL - p0_world[2]) / v_world[2]
            
            if t < 0:
                print("⚠️ Objekt scheint hinter der Kamera zu liegen (t < 0)!")
                return
                
            if t > MAX_REACH:
                print(f"⚠️ Zielpunkt ist mit {t:.2f}m zu weit weg (Max {MAX_REACH}m)!")
                return
                
            intersection_world = p0_world + t * v_world
            
            target_pose_world = Pose()
            target_pose_world.position.x = intersection_world[0]
            target_pose_world.position.y = intersection_world[1]
            target_pose_world.position.z = intersection_world[2]
            
            # --- GREIF-ORIENTIERUNG (TOP-DOWN) ---
            # 1. Ziel-Rotation merken (wird erst bei 'G' angewendet)
            obj_yaw = pixel_data.get('angle_deg', 0.0)
            self.pending_grip_yaw = obj_yaw
            
            # Anfahrt zwingt Senkrechte (Roll=180, Pitch=0) UND fixiert j6 auf -65°
            # 2. Z-Höhe (Hover) festlegen (Immer X cm über Tisch)
            target_pose_world.position.z = float(OBJECT_Z_LEVEL + self.approach_height)
            # --- IK-SUCHE FÜR J6 = -65° ---
            j6_target_rad = math.radians(-65.0)
            ik_solution = None
            
            print(f"🔍 Suche IK Lösung für Ziel-Pose mit j6=-65°...")
            # Wir sampeln den Yaw feinmaschiger (alle 5°), um eine gültige IK-Lösung mit festem j6 zu finden
            for yaw_deg in range(0, 360, 5):
                req = GetPositionIK.Request()
                req.ik_request.group_name = "ur_manipulator"; req.ik_request.ik_link_name = "tool0"; req.ik_request.avoid_collisions = True
                req.ik_request.pose_stamped.header.frame_id = "base_link"
                req.ik_request.pose_stamped.pose.position = target_pose_world.position
                req.ik_request.pose_stamped.pose.orientation = self.euler_to_quaternion(180.0, 0.0, float(yaw_deg))
                
                # Constraint: j6 auf -65°
                jc = JointConstraint()
                jc.joint_name = "wrist_3_joint"; jc.position = j6_target_rad
                jc.tolerance_above = 0.001; jc.tolerance_below = 0.001; jc.weight = 1.0
                req.ik_request.constraints.joint_constraints.append(jc)
                
                future = self._ik_client.call_async(req)
                if not self.wait_for_future(future, timeout=0.1): continue
                res = future.result()
                
                if res and res.error_code.val == 1: # SUCCESS
                    # Sicherstellen, dass j6 in der Lösung wirklich passt
                    joint_names = res.solution.joint_state.name
                    if "wrist_3_joint" in joint_names:
                        idx = joint_names.index("wrist_3_joint")
                        val = res.solution.joint_state.position[idx]
                        if abs(val - j6_target_rad) < 0.02:
                            print(f"✅ IK Lösung bei Yaw={yaw_deg}° gefunden (j6={math.degrees(val):.1f}°).")
                            ik_solution = res.solution.joint_state.position
                            break

            if ik_solution:
                print(f"🚀 Führe PTP-Joint Move auf j6=-65° aus...")
                if self.move_ptp_joints(ik_solution):
                    print("✅ Anfahrt abgeschlossen.")
                else:
                    print("⚠️ Gelenkfahrt fehlgeschlagen.")
            else:
                print(f"⚠️ Keine IK Lösung mit j6=-65° gefunden! Nutze Cartesian Fallback...")
                target_yaw = 0.0
                if current_tool_rot:
                    _, _, target_yaw = self.quaternion_to_euler(current_tool_rot)
                target_pose_world.orientation = self.euler_to_quaternion(180.0, 0.0, target_yaw)
                self.move_smart(target_pose_world)

            # Target für RViz (auf Tischhöhe zum Zeigen)
            ps = PoseStamped()
            ps.header.frame_id = "base_link"
            ps.header.stamp = self.get_clock().now().to_msg()
            ps.pose = copy.deepcopy(target_pose_world)
            ps.pose.position.z = float(OBJECT_Z_LEVEL)
            self.current_target_msg = ps
            self.target_pub.publish(ps)
            
            return True
        except Exception as e:
            print(f"❌ Fehler bei Ziel-Berechnung: {e}")

    def publish_all_targets_rviz(self, detections):
        """Projiziert alle erkannten Objekte und publisht sie als Marker in RViz"""
        if not self.focal_length or not detections: 
            # Publishe leeres Array um alte Marker zu löschen, wenn nichts erkannt wird
            self.marker_pub.publish(MarkerArray())
            return
            
        try:
            now = rclpy.time.Time()
            transform = self.tf_buffer.lookup_transform("base_link", "camera_optical_frame", now)
        except Exception as e:
            return
            
        marker_array = MarkerArray()
        
        # Füge einen DELETEALL Marker hinzu, um alte Marker aus dem vorherigen Frame abzuräumen
        delete_marker = Marker()
        delete_marker.action = Marker.DELETEALL
        marker_array.markers.append(delete_marker)
        
        for idx, obj in enumerate(detections):
            pixel_data = obj['pixel_data']
            
            vx_cam = (pixel_data['dx']) / self.focal_length
            vy_cam = (pixel_data['dy']) / self.focal_length
            vz_cam = 1.0
            
            p0_world = np.array([
                transform.transform.translation.x,
                transform.transform.translation.y,
                transform.transform.translation.z
            ])
            
            v_msg = Vector3Stamped()
            v_msg.header.frame_id = "camera_optical_frame"
            v_msg.vector.x = float(vx_cam); v_msg.vector.y = float(vy_cam); v_msg.vector.z = float(vz_cam)
            
            v_world_msg = tf2_geometry_msgs.do_transform_vector3(v_msg, transform)
            v_world = np.array([v_world_msg.vector.x, v_world_msg.vector.y, v_world_msg.vector.z])
            v_world = v_world / np.linalg.norm(v_world)
            
            # Intersection with Z = OBJECT_Z_LEVEL
            if abs(v_world[2]) > 0.05:
                t = (OBJECT_Z_LEVEL - p0_world[2]) / v_world[2]
            else:
                t = 0.5 # Fallback
                
            intersection_world = p0_world + t * v_world
            
            # 1. Ziel-Pfeil (wie vorher)
            m = Marker()
            m.header.frame_id = "base_link"
            m.header.stamp = self.get_clock().now().to_msg()
            m.ns = "detections"
            m.id = idx * 2
            m.type = Marker.ARROW
            m.action = Marker.ADD
            m.pose.position.x = intersection_world[0]
            m.pose.position.y = intersection_world[1]
            m.pose.position.z = float(OBJECT_Z_LEVEL)
            
            # Pfeil schaut nach unten auf das Objekt: Pitch = 90 deg down
            m.pose.orientation = self.euler_to_quaternion(0, 90, pixel_data['angle_deg'])
            
            m.scale.x = 0.05
            m.scale.y = 0.01 
            m.scale.z = 0.01 
            
            m.color.r = 0.0
            m.color.g = 1.0
            m.color.b = 0.0
            m.color.a = 1.0 
            
            
            marker_array.markers.append(m)
            
            # 2. Sicht-Strahl (Linie von Kamera zum Ziel)
            ray = Marker()
            ray.header.frame_id = "base_link"
            ray.header.stamp = self.get_clock().now().to_msg()
            ray.ns = "detections_rays"
            ray.id = (idx * 2) + 1
            ray.type = Marker.LINE_STRIP
            ray.action = Marker.ADD
            ray.scale.x = 0.005 # Linien-Dicke
            
            ray.color.r = 1.0
            ray.color.g = 0.0
            ray.color.b = 0.0
            ray.color.a = 0.8
            
            p_start = Point(); p_start.x = p0_world[0]; p_start.y = p0_world[1]; p_start.z = p0_world[2]
            p_end = Point(); p_end.x = intersection_world[0]; p_end.y = intersection_world[1]; p_end.z = intersection_world[2]
            ray.points.append(p_start)
            ray.points.append(p_end)
            
            # Orientation w=1.0 for LINE_STRIP is required
            ray.pose.orientation.w = 1.0
            
            marker_array.markers.append(ray)
            
        self.marker_pub.publish(marker_array)

    def euler_to_quaternion(self, roll_deg, pitch_deg, yaw_deg):
        # Hilfsfunktion für Pose-Rotation
        r = math.radians(roll_deg); p = math.radians(pitch_deg); y = math.radians(yaw_deg)
        cy = math.cos(y * 0.5); sy = math.sin(y * 0.5); cp = math.cos(p * 0.5); sp = math.sin(p * 0.5); cr = math.cos(r * 0.5); sr = math.sin(r * 0.5)
        q = Quaternion()
        q.w = cr * cp * cy + sr * sp * sy; q.x = sr * cp * cy - cr * sp * sy; q.y = cr * sp * cy + sr * cp * sy; q.z = cr * cp * sy - sr * sp * cy
        return q

    def quaternion_to_euler(self, q):
        # Hilfsfunktion: Quaternion zu Euler (deg)
        # yaw (z-axis rotation)
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        # pitch (y-axis rotation)
        sinp = 2 * (q.w * q.y - q.z * q.x)
        if abs(sinp) >= 1:
            pitch = math.pi / 2 if sinp > 0 else -math.pi / 2
        else:
            pitch = math.asin(sinp)
        # roll (x-axis rotation)
        sinr_cosp = 2 * (q.w * q.x + q.y * q.z)
        cosr_cosp = 1 - 2 * (q.x * q.x + q.y * q.y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)

    def go_home(self):
        self.current_target_msg = None
        goal = MoveGroup.Goal(); goal.request.workspace_parameters.header.frame_id = "base_link"; goal.request.group_name = "ur_manipulator"
        target_joints = self.home_joints
        constraints = Constraints()
        names = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"]
        for i, n in enumerate(names):
            jc = JointConstraint(); jc.joint_name = n; jc.position = target_joints[i]; jc.tolerance_above = 0.001; jc.tolerance_below = 0.001; jc.weight = 1.0
            constraints.joint_constraints.append(jc)
        goal.request.goal_constraints.append(constraints)
        goal.request.max_velocity_scaling_factor = self.speed_scale
        goal.request.max_acceleration_scaling_factor = self.speed_scale
        self._move_group_client.wait_for_server()
        self._move_group_client.send_goal_async(goal)